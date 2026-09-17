#!/usr/bin/env python3
"""Chop & Absorption State Machine LP Terminal — Solana Meteora DLMM Edition (Mobile / VPS).
Data Source: Official Meteora DLMM Data API (https://dlmm.datapi.meteora.ag) + DexScreener Batch API.
Zero Rate Limits (Anti-429), Real DLMM Fees, Precision Pool Links.
Akses Browser HP: http://<IP_VPS>:8771
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


HOST = "0.0.0.0"
PORT = 8771
INTERVAL_SEC = 300
POSITION_USD = 100
FEE_TIER = 0.01

LOG_PATH = Path(__file__).resolve().parent / "sol-hp-log.json"
FILTER_PATH = Path(__file__).resolve().parent / "sol-hp-filters.json"

LOG_MAX = 200
LOG_GAP_MS = 15 * 60_000
LOG_KEEP_MS = 24 * 60 * 60_000

DEFAULT_FILTERS = {
    "min_liq": 20000,           # TVL pool min $20k
    "min_mcap": 0,
    "min_vl": 2,                # V/L 24h min 2x
    "max_5m": 15,               # max volatilitas 5m %
    "max_1h": 80,               # max volatilitas 1h %
    "max_top10": 50,
    "max_er": 20,               # Efficiency Ratio sweet spot <= 20
    "max_dev_hold": 25,
    "max_insider": 15,
    "min_holder": 100,          # min holder count
    "position": 100,            # modal $100
    "interval": 300,            # scan 5m
    "min_absorb_score": 65,
    "min_absorb_mcap": 250000,
    "telegram_token": "",
    "telegram_chat_id": "",
    "telegram_enabled": False,
}

lock = threading.Lock()
state: dict = {
    "scanning": False,
    "error": None,
    "scanned_at": None,
    "next_scan_at": None,
    "n": 0,
    "rows": [],
    "log": [],
    "fees": {},
    "filters": dict(DEFAULT_FILTERS),
    "prev_micro_states": {},
    "prev_fee_decay": set(),
}

QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def get_next_boundary(interval_sec: int = 300, offset_sec: int = 1) -> float:
    now = time.time()
    return ((int(now) // interval_sec) + 1) * interval_sec + offset_sec


def usd_fmt(n: float) -> str:
    if not n:
        return "-"
    if n >= 1e6:
        return f"{n / 1e6:.2f}m"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return str(round(n))


def calculate_lp_range(price: float, p5: float, p1: float, liq: float) -> dict:
    if price <= 0:
        return {"range_pct": 20.0, "lower": 0.0, "upper": 0.0, "note": "+-20% (Default)", "safety": "Safe"}
    vol_1h = abs(p1)
    vol_5m = abs(p5)
    base_pct = max(15.0, min(40.0, vol_1h * 2.5 + vol_5m * 1.5))
    if liq >= 200_000:
        mult = 0.85
    elif liq >= 100_000:
        mult = 1.0
    elif liq >= 50_000:
        mult = 1.15
    else:
        mult = 1.35
    final_pct = round(min(45.0, base_pct * mult), 1)
    lower = round(price * (1 - final_pct / 100), 10)
    upper = round(price * (1 + final_pct / 100), 10)
    safety_label = "Safe" if final_pct <= 25 else "Wide" if final_pct <= 35 else "Risky"
    return {
        "range_pct": final_pct,
        "lower": lower,
        "upper": upper,
        "note": f"+-{final_pct:.1f}% dari harga saat ini [{safety_label}]",
        "safety": safety_label,
    }


def calculate_microstructure(row: dict, f: dict, vl: float, er: float, p5: float, p1: float, liq: float, mcap: float = 0.0) -> dict:
    mcap_val = mcap
    buy_ratio = row.get("buy_ratio", 50.0)
    buys = row.get("buys", 0)
    sells = row.get("sells", 0)

    score = 0.0
    if er <= 1.0:
        score += 35.0
    elif er <= 3.0:
        score += 30.0
    elif er <= 6.0:
        score += 22.0
    elif er <= 12.0:
        score += 14.0
    elif er <= 20.0:
        score += 5.0

    if vl >= 20.0:
        score += 25.0
    elif vl >= 10.0:
        score += 20.0
    elif vl >= 5.0:
        score += 15.0
    elif vl >= 2.0:
        score += 10.0

    vol_pen = (abs(p5) * 1.2) + (abs(p1) * 0.25)
    score = max(0.0, score - min(20.0, vol_pen))

    if buy_ratio >= 50.0:
        flow_bonus = min(15.0, (buy_ratio - 50.0) * 1.5)
        score += flow_bonus
    else:
        flow_pen = min(20.0, (50.0 - buy_ratio) * 1.8)
        score = max(0.0, score - flow_pen)

    if liq >= 150_000:
        score += 15.0
    elif liq >= 80_000:
        score += 10.0
    elif liq >= 30_000:
        score += 5.0

    min_mcap_setting = float(f.get("min_absorb_mcap") or 250_000)
    if mcap_val < min_mcap_setting and mcap_val > 0:
        score *= 0.65

    score = round(min(100.0, max(0.0, score)), 1)

    # State Machine
    if er <= 8.0 and abs(p5) <= 7.0 and abs(p1) <= 30.0 and buy_ratio >= 47.0 and vl >= 3.0:
        state = "ABSORPTION"
        state_title = "ABSORPTION (Sweet Spot)"
        state_desc = "Volume masif terserap rapat. Range sempit, fee deras."
        action = "ENTRY LP (Range Ideal)"
        action_type = "PRIME"
    elif er <= 15.0 and p1 < -5.0 and p5 >= -2.0 and buy_ratio >= 50.0 and vl >= 2.0:
        state = "REACCUMULATION"
        state_title = "UGLY REACCUMULATION"
        state_desc = "Pullback menyerap seller, buyer diam-diam akumulasi."
        action = "ENTRY LP (Buffer Lebar)"
        action_type = "VALID"
    elif abs(p5) > 12.0 or abs(p1) > 40.0:
        state = "EXPANSION"
        state_title = "EXPANSION (Out of Range)"
        state_desc = "Harga sedang meledak/breakout vertikal. Risiko IL tinggi."
        action = "HINDARI / TUNGGU CHOP"
        action_type = "WARN"
    elif buy_ratio < 42.0 or p1 < -25.0:
        state = "DISTRIBUTION"
        state_title = "DISTRIBUTION (Toxic Dump)"
        state_desc = "Penjual mendominasi, harga dump. Bahaya inventory toxic."
        action = "EXIT LP / JANGAN MASUK"
        action_type = "DANGER"
    else:
        state = "NEUTRAL"
        state_title = "NEUTRAL / CHOPPING"
        state_desc = "Pergerakan sideways standar tanpa sinyal ekstrim."
        action = "MONITORING"
        action_type = "WAIT"

    return {
        "score": score,
        "state": state,
        "state_title": state_title,
        "state_desc": state_desc,
        "action": action,
        "action_type": action_type,
        "buy_ratio": buy_ratio,
        "clue": f"Mcap ${usd_fmt(mcap_val)} · V/L {vl:.1f}x · ER {er:.1f}",
    }


def _trigger_telegram(alert_type: str, token_data: dict) -> None:
    with lock:
        f = state.get("filters") or {}
    if not f.get("telegram_enabled"):
        return
    try:
        from telegram_bot import send_alert  # type: ignore
        send_alert(alert_type, token_data)
    except Exception:
        pass


def load_log() -> list[dict]:
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_log(rows: list[dict]) -> None:
    try:
        LOG_PATH.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def load_filters() -> dict:
    for fp in [FILTER_PATH, Path(__file__).resolve().parent / "hp-filters.json", Path(__file__).resolve().parent / "v5-filters.json"]:
        try:
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out = dict(DEFAULT_FILTERS)
                    out.update({k: data[k] for k in DEFAULT_FILTERS if k in data})
                    return out
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return dict(DEFAULT_FILTERS)


def save_filters(f: dict) -> None:
    try:
        FILTER_PATH.write_text(json.dumps(f, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def prune_log(rows: list[dict], now: int) -> list[dict]:
    cut = now - LOG_KEEP_MS
    out = []
    for r in rows:
        hits = [h for h in (r.get("hits") or [r.get("at", 0)]) if h >= cut]
        if not hits:
            continue
        item = dict(r)
        item["hits"] = hits
        item["at"] = hits[-1]
        out.append(item)
    return out


def record_hits(rows: list[dict]) -> list[dict]:
    with lock:
        log = list(state.get("log") or [])
    now = int(time.time() * 1000)
    idx = {r["address"]: i for i, r in enumerate(log)}
    for t in rows:
        if t["tag"] != "CHOP":
            continue
        addr = t["address"]
        if addr in idx:
            entry = log[idx[addr]]
            hits = list(entry.get("hits") or [entry.get("at", now)])
            if not hits or (now - hits[-1]) >= LOG_GAP_MS:
                hits.append(now)
            entry["hits"] = hits
            entry["at"] = now
            entry["tag"] = t["tag"]
            entry["symbol"] = t["symbol"]
            entry["name"] = t["name"]
            entry["chain"] = t.get("chain", "SOL")
            entry["dex"] = t.get("dex", "Meteora DLMM")
            entry["gmgn"] = t.get("gmgn", "")
            entry["dexscreener"] = t.get("dexscreener", "")
            entry["meteora"] = t.get("meteora", "")
            entry["twitter"] = t.get("twitter", "")
        else:
            log.append({
                "address": addr,
                "symbol": t["symbol"],
                "name": t["name"],
                "chain": t.get("chain", "SOL"),
                "dex": t.get("dex", "Meteora DLMM"),
                "tag": t["tag"],
                "at": now,
                "hits": [now],
                "gmgn": t.get("gmgn", ""),
                "dexscreener": t.get("dexscreener", ""),
                "meteora": t.get("meteora", ""),
                "twitter": t.get("twitter", ""),
            })
            idx[addr] = len(log) - 1
    log = prune_log(log, now)
    if len(log) > LOG_MAX:
        log = sorted(log, key=lambda r: r.get("at", 0), reverse=True)[:LOG_MAX]
    save_log(log)
    return log


# ================= DATA FETCHING: METEORA DLMM + DEXSCREENER =================

def fetch_meteora_dlmm_pools(limit: int = 50, min_tvl: int = 15000) -> list[dict]:
    """Mengambil pool DLMM aktif dari official REST API Meteora tanpa butuh API key."""
    pools_map: dict[str, dict] = {}

    # Query 1: Top Fee/TVL Ratio pools (sweet spot LP yield)
    params1 = {
        "page_size": min(limit, 60),
        "sort_by": "fee_tvl_ratio_1h:desc",
        "filter_by": f"is_blacklisted=false && tvl>={min_tvl}",
    }
    url1 = "https://dlmm.datapi.meteora.ag/pools?" + urllib.parse.urlencode(params1)
    try:
        req = urllib.request.Request(url1, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=12) as res:
            data = json.loads(res.read().decode())
            for p in data.get("data", []):
                if p.get("address"):
                    pools_map[p["address"]] = p
    except Exception as e:
        print(f"[Meteora] Query 1 Error: {e}")

    # Query 2: Top 1h Volume pools (high volume liquidity)
    params2 = {
        "page_size": min(limit, 60),
        "sort_by": "volume_1h:desc",
        "filter_by": f"is_blacklisted=false && tvl>={min_tvl}",
    }
    url2 = "https://dlmm.datapi.meteora.ag/pools?" + urllib.parse.urlencode(params2)
    try:
        req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=12) as res:
            data = json.loads(res.read().decode())
            for p in data.get("data", []):
                if p.get("address") and p["address"] not in pools_map:
                    pools_map[p["address"]] = p
    except Exception as e:
        print(f"[Meteora] Query 2 Error: {e}")

    return list(pools_map.values())


def fetch_dexscreener_batch(token_addrs: list[str]) -> dict[str, dict]:
    """Enrich token pools with DexScreener 5m/1h price changes and Market Cap (in batches of 30)."""
    if not token_addrs:
        return {}
    out: dict[str, dict] = {}
    chunk_size = 30
    for i in range(0, min(len(token_addrs), 90), chunk_size):
        chunk = token_addrs[i:i + chunk_size]
        joined = ",".join(chunk)
        url = f"https://api.dexscreener.com/tokens/v1/solana/{joined}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=10) as res:
                pairs = json.loads(res.read().decode())
                if isinstance(pairs, list):
                    for p in pairs:
                        base = p.get("baseToken", {}).get("address")
                        if base and base not in out:
                            out[base] = p
        except Exception as e:
            print(f"[DexScreener] Batch {i} Error: {e}")
    return out


def score_meteora_pool(pool: dict, dex_item: dict, f: dict) -> dict:
    pool_address = pool.get("address", "")
    pool_name = pool.get("name", "Unknown-SOL")
    token_x = pool.get("token_x") or {}
    token_y = pool.get("token_y") or {}

    # Identify base / meme token vs quote token (SOL/USDC/USDT)
    x_addr = token_x.get("address", "")
    y_addr = token_y.get("address", "")
    if x_addr in QUOTE_MINTS and y_addr not in QUOTE_MINTS:
        meme_tok = token_y
        quote_tok = token_x
    elif y_addr in QUOTE_MINTS and x_addr not in QUOTE_MINTS:
        meme_tok = token_x
        quote_tok = token_y
    else:
        meme_tok = token_x
        quote_tok = token_y

    token_addr = meme_tok.get("address", pool_address)
    symbol = str(meme_tok.get("symbol") or pool_name.split("-")[0])
    name = str(meme_tok.get("name") or pool_name)

    # Meteora DLMM real liquidity & volume metrics
    liq = float(pool.get("tvl") or 0.0)
    vol_1h = float(pool.get("volume", {}).get("1h") or 0.0)
    vol_24h = float(pool.get("volume", {}).get("24h") or (vol_1h * 24))
    vl = round((vol_1h * 24) / liq, 2) if liq > 0 else 0.0

    # Real Meteora LP fees collected
    real_fee_1h = float(pool.get("fees", {}).get("1h") or 0.0)
    real_fee_24h = float(pool.get("fees", {}).get("24h") or (real_fee_1h * 24))

    # Price & Volatility from DexScreener (or fallback to pool)
    p_change = dex_item.get("priceChange") or {}
    p1 = float(p_change.get("h1") or 0.0)
    p5 = float(p_change.get("m5") or 0.0)

    price = float(dex_item.get("priceUsd") or meme_tok.get("price") or pool.get("current_price") or 0.0)
    mcap = float(dex_item.get("marketCap") or dex_item.get("fdv") or meme_tok.get("market_cap") or 0.0)

    # Order flow / transactions
    txns = dex_item.get("txns", {}).get("h1") or {}
    buys = int(txns.get("buys") or 0)
    sells = int(txns.get("sells") or 0)
    total_tx = buys + sells
    buy_ratio = round((buys / total_tx) * 100, 1) if total_tx > 0 else 50.0

    # Efficiency Ratio (ER = |p1| / vl) — Invariant Chop LP
    er = round(abs(p1) / vl, 2) if vl > 0 else 99.0

    # Real Fee calculation for $pos LP modal
    pos = float(f.get("position") or POSITION_USD)
    share = (pos / liq) if liq > 0 else 0.0
    if real_fee_1h > 0:
        fee_hour = round(real_fee_1h * share, 4)
        fee_24h = round(real_fee_24h * share, 4)
    else:
        # Fallback to standard 1% fee tier estimation
        fee_hour = round(vol_1h * FEE_TIER * share, 4)
        fee_24h = round(fee_hour * 24, 4)
    fee_7d = round(fee_24h * 7, 4)
    breakeven_hours = round(pos / fee_hour, 1) if fee_hour > 0 else 9999.0

    # LP Range Advisor (safe / wide / risky)
    lp_range = calculate_lp_range(price, p5, p1, liq)

    # Holders count
    holders = int(meme_tok.get("holders") or 0)

    # Criteria filtering (Chop Sideways LP Strategy)
    gaps = []
    if liq < f["min_liq"]:
        gaps.append(f"Liq (${usd_fmt(liq)}) < ${usd_fmt(f['min_liq'])}")
    if mcap < f["min_mcap"] and f["min_mcap"] > 0:
        gaps.append(f"Mcap (${usd_fmt(mcap)}) < ${usd_fmt(f['min_mcap'])}")
    if vl < f["min_vl"]:
        gaps.append(f"V/L ({vl:.1f}x) < {f['min_vl']}x")
    if abs(p5) > f["max_5m"]:
        gaps.append(f"|5m| ({abs(p5):.1f}%) > {f['max_5m']}%")
    if abs(p1) > f["max_1h"]:
        gaps.append(f"|1h| ({abs(p1):.1f}%) > {f['max_1h']}%")
    if er > f["max_er"]:
        gaps.append(f"ER ({er:.1f}) > {f['max_er']}")
    if holders > 0 and holders < f["min_holder"]:
        gaps.append(f"Holders ({holders}) < {f['min_holder']}")

    tag = "CHOP" if not gaps else "SKIP"

    # Advisories
    advisories = []
    if total_tx >= 15:
        if buy_ratio < 45.0:
            advisories.append({"level": "warn", "label": f"🔴 Buy {buy_ratio:.0f}%", "title": f"Seller dominan ({sells} Sells / {buys} Buys)"})
        elif buy_ratio >= 52.0:
            advisories.append({"level": "ok", "label": f"🟢 Buy {buy_ratio:.0f}%", "title": f"Buyer dominan ({buys} Buys / {sells} Sells)"})
        else:
            advisories.append({"level": "muted", "label": f"⚖️ Buy {buy_ratio:.0f}%", "title": f"Order flow seimbang ({buys} B / {sells} S)"})

    min_holder = int(f.get("min_holder") or 100)
    if holders > 0:
        if holders < min_holder:
            advisories.append({"level": "warn", "label": f"👥 Holder {holders}", "title": f"Holder ({holders}) di bawah minimum ({min_holder})"})
        else:
            advisories.append({"level": "ok", "label": f"✅ Holder {holders}", "title": f"Holder cukup aman ({holders})"})

    # Dedicated Microstructure Analysis
    row_mock = {"buy_ratio": buy_ratio, "buys": buys, "sells": sells}
    micro = calculate_microstructure(row_mock, f, vl, er, p5, p1, liq, mcap)
    micro["lp_range"] = lp_range

    return {
        "symbol": symbol,
        "name": name,
        "chain": "SOL",
        "dex": "Meteora DLMM",
        "pool_name": pool_name,
        "address": token_addr,
        "pool_address": pool_address,
        "price": price,
        "liq": round(liq, 2),
        "vol": round(vol_1h, 2),
        "vl": vl,
        "p5": round(p5, 2),
        "p1": round(p1, 2),
        "mcap": round(mcap, 2),
        "buys": buys,
        "sells": sells,
        "buy_ratio": buy_ratio,
        "er": er,
        "advisories": advisories,
        "tag": tag,
        "gaps": gaps,
        "fee_hour": fee_hour,
        "fee_24h": fee_24h,
        "fee_7d": fee_7d,
        "breakeven_hours": breakeven_hours,
        "lp_range": lp_range,
        "bin_step": pool.get("pool_config", {}).get("bin_step", 0),
        "base_fee_pct": pool.get("pool_config", {}).get("base_fee_pct", 0),
        "real_fee_1h": real_fee_1h,
        "holders": holders,
        "top10_rate": 0,
        "gmgn": f"https://gmgn.ai/sol/token/{token_addr}",
        "dexscreener": f"https://dexscreener.com/solana/{token_addr}",
        "meteora": f"https://app.meteora.ag/dlmm/{pool_address}",
        "twitter": f"https://x.com/search?q=%24{urllib.parse.quote(symbol)}&f=live",
        "micro": micro,
    }


def scan() -> None:
    with lock:
        if state["scanning"]:
            return
        state["scanning"] = True
        state["error"] = None
        f = dict(state.get("filters") or DEFAULT_FILTERS)

    try:
        pools = fetch_meteora_dlmm_pools(limit=50, min_tvl=int(f.get("min_liq", 20000)))
        meme_addrs = []
        for p in pools:
            tx = (p.get("token_x") or {}).get("address", "")
            ty = (p.get("token_y") or {}).get("address", "")
            m = tx if tx not in QUOTE_MINTS else ty
            if m and m not in meme_addrs:
                meme_addrs.append(m)

        dex_data = fetch_dexscreener_batch(meme_addrs)
        rows = [score_meteora_pool(p, dex_data.get((p.get("token_x") or {}).get("address", "") if (p.get("token_x") or {}).get("address", "") not in QUOTE_MINTS else (p.get("token_y") or {}).get("address", ""), {}), f) for p in pools]

        with lock:
            fees = state.setdefault("fees", {})

        for t in rows:
            hist = list(fees.get(t["address"], []))
            hist.append(t["fee_hour"])
            hist = hist[-8:]
            fees[t["address"]] = hist
            t["fee_spark"] = hist

            # Fee decay: 2-window comparison
            t["fee_decay"] = False
            t["fee_trend"] = "—"
            if len(hist) >= 4:
                recent = (hist[-1] + hist[-2]) / 2
                before = (hist[-3] + hist[-4]) / 2
                if before > 0:
                    ratio = recent / before
                    if ratio >= 1.2:
                        t["fee_trend"] = "↗"
                    elif ratio >= 0.7:
                        t["fee_trend"] = "→"
                    elif ratio >= 0.35:
                        t["fee_trend"] = "↘"
                        t["fee_decay"] = True
                    else:
                        t["fee_trend"] = "↓↓"
                        t["fee_decay"] = True
                else:
                    t["fee_trend"] = "→"
            elif len(hist) >= 3:
                if hist[-3] > hist[-2] > hist[-1] and hist[-1] < hist[-3] * 0.35:
                    t["fee_decay"] = True
                    t["fee_trend"] = "↓↓"

        keep = {t["address"] for t in rows}
        for k in list(fees):
            if k not in keep and len(fees) > 80:
                fees.pop(k, None)

        rows.sort(
            key=lambda t: (
                0 if t["tag"] == "CHOP" else 1,
                -t["vl"],
            )
        )
        log = record_hits(rows)

        now_ms = int(time.time() * 1000)
        next_boundary_ts = get_next_boundary(INTERVAL_SEC, offset_sec=1)

        with lock:
            prev_states = dict(state.get("prev_micro_states") or {})
            prev_decay = set(state.get("prev_fee_decay") or set())

        # Telegram Alert Triggers
        new_states: dict = {}
        new_decay: set = set()
        alert_threads: list = []

        for t in rows:
            addr = t["address"]
            micro = t.get("micro", {})
            cur_state = micro.get("state", "NEUTRAL")
            prev_state = prev_states.get(addr, "")
            new_states[addr] = cur_state

            if cur_state == "ABSORPTION" and prev_state != "ABSORPTION":
                th = threading.Thread(target=_trigger_telegram, args=("absorption", t), daemon=True)
                th.start()
                alert_threads.append(th)

            if t.get("fee_decay") and addr not in prev_decay:
                new_decay.add(addr)
                th = threading.Thread(target=_trigger_telegram, args=("fee_decay", t), daemon=True)
                th.start()
                alert_threads.append(th)
            elif not t.get("fee_decay"):
                pass
            else:
                new_decay.add(addr)

            if cur_state == "DISTRIBUTION" and prev_state not in ("DISTRIBUTION", "", "NEUTRAL"):
                th = threading.Thread(target=_trigger_telegram, args=("emergency", t), daemon=True)
                th.start()
                alert_threads.append(th)

        with lock:
            state["rows"] = rows
            state["log"] = log
            state["n"] = len(rows)
            state["scanned_at"] = now_ms
            state["next_scan_at"] = int(next_boundary_ts * 1000)
            state["prev_micro_states"] = new_states
            state["prev_fee_decay"] = new_decay
    except Exception as err:
        with lock:
            state["error"] = f"Scan error: {err}"
    finally:
        with lock:
            state["scanning"] = False


def loop() -> None:
    scan()
    while True:
        target_ts = get_next_boundary(INTERVAL_SEC, offset_sec=1)
        wait_sec = max(1.0, target_ts - time.time())
        time.sleep(wait_sec)
        scan()


HTML = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Chop Radar (Solana Meteora DLMM — Port 8771)</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🪓</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #090d14;
  --panel: #0f1523;
  --panel-hover: #141c2f;
  --card: #141a2c;
  --line: rgba(255, 255, 255, 0.08);
  --line-strong: rgba(255, 255, 255, 0.14);
  --fg: #f1f5f9;
  --mut: #72829d;
  --mut-light: #94a3b8;
  --pump: #f59e0b;
  --pump-soft: rgba(245, 158, 11, 0.14);
  --chop: #10b981;
  --chop-soft: rgba(16, 185, 129, 0.14);
  --bad: #f43f5e;
  --bad-soft: rgba(244, 63, 94, 0.14);
  --blue: #38bdf8;
  --blue-soft: rgba(56, 189, 248, 0.14);
  --purple: #a855f7;
  --purple-soft: rgba(168, 85, 247, 0.16);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; background: var(--bg); color: var(--fg); font-family: 'Inter', system-ui, -apple-system, sans-serif; font-size: 13px; }
body { display: flex; flex-direction: column; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.12); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.22); }

/* Header Top */
.top-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 18px; background: var(--panel); border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}
.brand-group { display: flex; align-items: center; gap: 12px; }
.logo-icon {
  width: 34px; height: 34px; border-radius: 9px;
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.25) 0%, rgba(168, 85, 247, 0.25) 100%);
  border: 1px solid rgba(255, 255, 255, 0.15);
  display: flex; align-items: center; justify-content: center;
  overflow: hidden;
}
.logo-icon img {
  width: 26px; height: 26px; object-fit: contain;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.4));
}
.brand-title { font-size: 15px; font-weight: 800; color: #fff; letter-spacing: -0.01em; display: flex; align-items: center; gap: 8px; }
.brand-badge {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  padding: 2px 7px; border-radius: 5px; background: var(--purple-soft); color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.3);
}

/* Master Mode Switcher */
.mode-switch-group {
  display: flex; align-items: center; gap: 4px;
  background: #080b12; padding: 3px; border-radius: 8px;
  border: 1px solid var(--line);
}
.mode-btn {
  padding: 5px 14px; border: 0; border-radius: 6px; background: transparent;
  color: var(--mut-light); font-size: 12px; font-weight: 700; cursor: pointer;
  transition: all 0.18s ease; display: flex; align-items: center; gap: 6px;
}
.mode-btn:hover { color: #fff; }
.mode-btn.active {
  background: var(--card); color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 2px 6px rgba(0,0,0,0.4);
}
.mode-btn.active.mode-absorb {
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.25) 0%, rgba(16, 185, 129, 0.2) 100%);
  border-color: rgba(168, 85, 247, 0.4);
  color: #e9d5ff;
}

.top-right { display: flex; align-items: center; gap: 12px; }
/* Chain Switcher Group */
.chain-switch-group {
  display: flex; align-items: center; gap: 4px;
  background: #080b12; padding: 3px; border-radius: 8px;
  border: 1px solid var(--line);
}
.chain-btn {
  padding: 5px 12px; border: 0; border-radius: 6px; background: transparent;
  color: var(--mut-light); font-size: 11.5px; font-weight: 700; cursor: pointer;
  transition: all 0.18s ease; display: flex; align-items: center; gap: 5px;
}
.chain-btn:hover { color: #fff; }
.chain-btn.active {
  background: var(--card); color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.14);
  box-shadow: 0 2px 6px rgba(0,0,0,0.4);
}
.chain-btn.active#chain-btn-sol {
  background: rgba(168, 85, 247, 0.25);
  border-color: rgba(168, 85, 247, 0.5);
  color: #e9d5ff;
}
.chain-btn.active#chain-btn-rh {
  background: rgba(59, 130, 246, 0.25);
  border-color: rgba(59, 130, 246, 0.5);
  color: #bfdbfe;
}
.chain-btn.active#chain-btn-both {
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.2) 0%, rgba(168, 85, 247, 0.2) 100%);
  border-color: rgba(168, 85, 247, 0.4);
  color: #fff;
}

.chain-pill {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 4px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  font-family: 'JetBrains Mono', monospace;
}
.chain-pill.chain-rh {
  background: rgba(59, 130, 246, 0.2);
  color: #60a5fa;
  border: 1px solid rgba(59, 130, 246, 0.4);
}
.chain-pill.chain-sol {
  background: rgba(168, 85, 247, 0.2);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.4);
}
.chain-btn.active#chain-btn-arc {
  background: rgba(249, 115, 22, 0.25);
  border-color: rgba(249, 115, 22, 0.5);
  color: #fed7aa;
}
.chain-pill.chain-arc {
  background: rgba(249, 115, 22, 0.2);
  color: #fb923c;
  border: 1px solid rgba(249, 115, 22, 0.4);
}

.status-pill {
  display: flex; align-items: center; gap: 8px; padding: 5px 12px;
  background: var(--card); border: 1px solid var(--line); border-radius: 20px;
  font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--mut-light);
}
.pulse-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--chop); box-shadow: 0 0 8px var(--chop);
  animation: pulse 2s infinite;
}
@keyframes pulse { 0%{opacity:1;} 50%{opacity:0.35;} 100%{opacity:1;} }

.btn-scan {
  height: 32px; padding: 0 13px; border: 0; border-radius: 7px;
  background: var(--chop); color: #071912; font-weight: 700; font-size: 12px;
  cursor: pointer; transition: all 0.15s ease;
}
.btn-scan:hover { background: #34d399; transform: translateY(-1px); }
.btn-scan:disabled { opacity: 0.5; cursor: not-allowed; }

/* Sub-Mode Container */
.subview { display: none; flex: 1; flex-direction: column; min-height: 0; }
.subview.active-view { display: flex; }

/* KPI Strip */
.kpi-strip {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
  padding: 8px 18px 4px; flex-shrink: 0;
}
.kpi-card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 9px;
  padding: 8px 12px; display: flex; flex-direction: column; gap: 2px;
}
.kpi-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--mut); font-weight: 600; }
.kpi-val { font-size: 16.5px; font-weight: 800; font-family: 'JetBrains Mono', monospace; color: var(--fg); }
.kpi-val.chop { color: var(--chop); }
.kpi-val.purple { color: #c084fc; }

/* Strategy Banner for Absorption Mode */
.strategy-banner {
  margin: 4px 18px 6px; padding: 8px 14px; border-radius: 8px;
  background: linear-gradient(90deg, rgba(168, 85, 247, 0.12) 0%, rgba(16, 185, 129, 0.08) 100%);
  border: 1px solid rgba(168, 85, 247, 0.25);
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-shrink: 0;
}
.strat-text { font-size: 11.5px; color: #e2e8f0; line-height: 1.4; }
.strat-text b { color: #a855f7; }
.strat-tag {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  padding: 3px 8px; border-radius: 4px; background: rgba(168, 85, 247, 0.25);
  color: #e9d5ff; white-space: nowrap; font-family: 'JetBrains Mono', monospace;
}

/* Navigation Tabs & Filter Bar */
.control-bar {
  margin: 4px 18px 6px; display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-shrink: 0;
}
.tabs-group { display: flex; align-items: center; gap: 4px; background: var(--panel); padding: 3px; border-radius: 8px; border: 1px solid var(--line); }
.tab-btn {
  padding: 5px 11px; border: 0; border-radius: 6px; background: transparent;
  color: var(--mut-light); font-size: 11.5px; font-weight: 600; cursor: pointer;
  transition: all 0.15s;
}
.tab-btn.active { background: var(--card); color: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.3); }

.filter-bar {
  display: flex; align-items: center; gap: 6px; background: var(--panel);
  padding: 5px 8px; border-radius: 8px; border: 1px solid var(--line);
  flex-wrap: wrap;
}
.f-input-group { display: flex; align-items: center; gap: 4px; }
.f-input-group label { font-size: 10px; text-transform: uppercase; color: var(--mut); font-weight: 600; }
.f-input-group input {
  height: 25px; width: 64px; border: 1px solid var(--line-strong); border-radius: 5px;
  background: var(--bg); color: #fff; padding: 0 5px; font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; text-align: right;
}
.btn-apply {
  height: 25px; padding: 0 9px; border: 1px solid var(--chop); border-radius: 5px;
  background: var(--chop-soft); color: var(--chop); font-size: 11px; font-weight: 700; cursor: pointer;
}
.btn-apply:hover { background: var(--chop); color: #071912; }

/* Workstation Layout */
.workstation { flex: 1; min-height: 0; padding: 0 18px 12px; display: flex; flex-direction: column; gap: 10px; }
.workstation.mode-all #main-panel { flex: 1.7; min-height: 0; }
.workstation.mode-all #bottom-split { flex: 1.1; min-height: 0; display: grid; grid-template-columns: 1fr 1.35fr; gap: 10px; }
.workstation:not(.mode-all) #bottom-split { display: none; }
.workstation:not(.mode-all) #main-panel { flex: 1; min-height: 0; }

.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 9px;
  display: flex; flex-direction: column; min-height: 0; overflow: hidden;
}
.panel-head {
  padding: 7px 12px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;
  background: rgba(255,255,255,0.015);
}
.panel-title { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--mut-light); display: flex; align-items: center; gap: 7px; }
.panel-scroll { flex: 1; min-height: 0; overflow: auto; }

/* Tables */
table { width: 100%; border-collapse: collapse; text-align: left; }
th {
  position: sticky; top: 0; z-index: 2; background: #111726;
  font-family: 'JetBrains Mono', monospace; font-size: 10.5px; font-weight: 600;
  text-transform: uppercase; color: var(--mut); padding: 7px 9px; border-bottom: 1px solid var(--line);
}
th.num, td.num { text-align: right; }
td {
  padding: 7px 9px; border-bottom: 1px solid var(--line);
  font-family: 'JetBrains Mono', monospace; font-size: 11.5px; vertical-align: middle;
}
tr:hover td { background: var(--panel-hover); }

/* Badges & Pills */
.badge-tag {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 10px; font-weight: 700; padding: 2px 5px; border-radius: 4px;
}
.tag-CHOP { background: var(--chop-soft); color: var(--chop); border: 1px solid rgba(16, 185, 129, 0.3); }
.tag-SKIP { background: rgba(255,255,255,0.05); color: var(--mut); border: 1px solid var(--line); }

/* State Machine Pills */
.state-pill {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10.5px; font-weight: 800; padding: 3px 7px; border-radius: 5px;
  font-family: 'JetBrains Mono', monospace; text-transform: uppercase;
}
.state-absorb { background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }
.state-expand { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }
.state-dist { background: rgba(244, 63, 94, 0.16); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.35); }
.state-reaccum { background: rgba(56, 189, 248, 0.16); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.35); }
.state-neutral { background: rgba(255, 255, 255, 0.06); color: var(--mut-light); border: 1px solid var(--line); }

/* Action Buttons / Badges */
.action-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10.5px; font-weight: 700; padding: 3px 8px; border-radius: 5px;
  font-family: 'Inter', sans-serif;
}
.act-prime { background: linear-gradient(135deg, rgba(16, 185, 129, 0.25) 0%, rgba(56, 189, 248, 0.2) 100%); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }
.act-valid { background: var(--chop-soft); color: var(--chop); border: 1px solid rgba(16, 185, 129, 0.3); }
.act-warn { background: var(--pump-soft); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
.act-danger { background: var(--bad-soft); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.3); }
.act-wait { background: rgba(255, 255, 255, 0.05); color: var(--mut-light); border: 1px solid var(--line); }

/* Score Gauge Bar */
.score-gauge-wrap { display: flex; align-items: center; gap: 7px; width: 110px; }
.score-gauge-bar { flex: 1; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden; }
.score-gauge-fill { height: 100%; border-radius: 3px; }
.score-num { font-size: 11px; font-weight: 700; min-width: 26px; text-align: right; }

.gap-pill {
  display: inline-block; font-size: 10px; padding: 2px 5px;
  border-radius: 4px; background: rgba(244, 63, 94, 0.12);
  color: #fb7185; margin: 1px 2px; border: 1px solid rgba(244, 63, 94, 0.25);
  font-family: 'JetBrains Mono', monospace; white-space: nowrap;
}

.adv-badge {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 10px; font-weight: 600; padding: 2px 5px; border-radius: 4px;
  font-family: 'JetBrains Mono', monospace; margin: 1px 2px;
  white-space: nowrap; cursor: help;
}
.adv-ok { background: var(--chop-soft); color: var(--chop); border: 1px solid rgba(16, 185, 129, 0.3); }
.adv-warn { background: var(--pump-soft); color: var(--pump); border: 1px solid rgba(245, 158, 11, 0.3); }
.adv-danger { background: var(--bad-soft); color: var(--bad); border: 1px solid rgba(244, 63, 94, 0.35); font-weight: 700; }
.adv-muted { background: rgba(255, 255, 255, 0.05); color: var(--mut-light); border: 1px solid var(--line); }

.safe-pill { font-size: 10px; padding: 2px 4px; border-radius: 4px; display: inline-block; }
.safe-ok { color: var(--chop); background: var(--chop-soft); }
.safe-warn { color: var(--pump); background: var(--pump-soft); }
.safe-bad { color: var(--bad); background: var(--bad-soft); }

.links-group { display: flex; align-items: center; gap: 4px; justify-content: flex-end; }
.btn-link {
  padding: 2px 5px; border-radius: 4px; background: rgba(255,255,255,0.06);
  border: 1px solid var(--line); color: var(--blue); text-decoration: none;
  font-size: 9.5px; font-weight: 600; transition: all 0.15s;
}
.btn-link:hover { background: var(--blue-soft); border-color: rgba(56, 189, 248, 0.4); }

.btn-copy-ca {
  background: transparent; border: 0; cursor: pointer; color: var(--mut);
  padding: 1px 3px; border-radius: 3px;
}
.btn-copy-ca:hover { color: #fff; background: rgba(255,255,255,0.1); }

/* LP Range Advisor Box */
.lp-range-box { display: flex; flex-direction: column; gap: 2px; }
.lp-range-vals { font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 700; color: #fff; }
.lp-range-badge {
  display: inline-flex; align-items: center; gap: 3px; font-size: 9.5px;
  font-weight: 700; padding: 1px 5px; border-radius: 3px; width: fit-content;
}
.lp-range-safe { background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }
.lp-range-wide { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }
.lp-range-risky { background: rgba(244, 63, 94, 0.16); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.35); }

/* Telegram Bar (Hidden for now to focus on dashboard) */
.tg-bar {
  display: none;
  margin: 4px 18px 6px; padding: 6px 14px; border-radius: 8px;
  background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.18);
  align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
}
.tg-inputs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tg-input-group { display: flex; align-items: center; gap: 4px; font-size: 11px; }
.tg-input-group label { color: var(--mut-light); font-size: 10px; font-weight: 600; text-transform: uppercase; }
.tg-input-group input {
  height: 24px; border: 1px solid var(--line-strong); border-radius: 4px;
  background: var(--bg); color: #fff; padding: 0 6px; font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
}
.btn-tg {
  height: 24px; padding: 0 9px; border-radius: 4px; font-size: 10.5px; font-weight: 700;
  cursor: pointer; transition: all 0.15s;
}
.btn-tg-test { background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.35); color: #7dd3fc; }
.btn-tg-test:hover { background: #38bdf8; color: #080b12; }
.btn-tg-save { background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.35); color: #34d399; }
.btn-tg-save:hover { background: #10b981; color: #080b12; }

@media (max-width: 1150px) {
  html, body { overflow: auto; height: auto; }
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .control-bar { flex-direction: column; align-items: stretch; margin: 6px 10px; }
  .workstation { padding: 0 10px 14px; }
}

/* Mobile / HP Screen Enhancements */
@media (max-width: 768px) {
  html, body { overflow-y: auto !important; height: auto !important; min-height: 100%; }
  body { padding-bottom: 24px; }
  .top-bar {
    flex-direction: column !important;
    align-items: stretch !important;
    gap: 8px !important;
    padding: 10px 12px !important;
  }
  .brand-group {
    justify-content: space-between !important;
    width: 100% !important;
  }
  .mode-switch-group {
    width: 100% !important;
    display: flex !important;
  }
  .mode-btn {
    flex: 1 !important;
    justify-content: center !important;
    padding: 6px 8px !important;
    font-size: 11px !important;
    text-align: center !important;
  }
  .chain-switch-group {
    width: 100% !important;
    display: flex !important;
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
  }
  .chain-btn {
    flex: 1 !important;
    justify-content: center !important;
    padding: 6px 8px !important;
    font-size: 10.5px !important;
    white-space: nowrap !important;
  }
  .top-right {
    display: flex !important;
    justify-content: space-between !important;
    width: 100% !important;
    gap: 6px !important;
  }
  .status-pill {
    flex: 1 !important;
    font-size: 10px !important;
    padding: 4px 8px !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
  }
  .btn-scan {
    flex-shrink: 0 !important;
    height: 30px !important;
    padding: 0 10px !important;
    font-size: 11px !important;
  }
  .kpi-strip {
    grid-template-columns: repeat(2, 1fr) !important;
    gap: 6px !important;
    padding: 6px 12px 2px !important;
  }
  .kpi-card {
    padding: 6px 10px !important;
  }
  .kpi-val {
    font-size: 15px !important;
  }
  .strategy-banner {
    margin: 4px 12px 6px !important;
    padding: 6px 10px !important;
    flex-direction: column !important;
    align-items: flex-start !important;
  }
  .control-bar {
    margin: 4px 12px 6px !important;
    flex-direction: column !important;
    align-items: stretch !important;
    gap: 6px !important;
  }
  .tabs-group {
    width: 100% !important;
    overflow-x: auto !important;
    display: flex !important;
    -webkit-overflow-scrolling: touch !important;
  }
  .tab-btn {
    flex: 1 !important;
    text-align: center !important;
    white-space: nowrap !important;
    font-size: 11px !important;
    padding: 5px 8px !important;
  }
  .filter-bar {
    width: 100% !important;
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 6px !important;
    padding: 6px 8px !important;
  }
  .f-input-group {
    flex: 1 1 45% !important;
    display: flex !important;
    justify-content: space-between !important;
  }
  .f-input-group input {
    width: 60px !important;
  }
  .btn-apply {
    width: 100% !important;
    height: 28px !important;
    margin-top: 4px !important;
  }
  .workstation {
    padding: 0 12px 14px !important;
    overflow: visible !important;
    height: auto !important;
    flex: none !important;
  }
  #bottom-split {
    grid-template-columns: 1fr !important;
  }
  .table-scroll {
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
  }
  .table-scroll table {
    min-width: 640px !important;
  }
  th, td {
    padding: 6px 8px !important;
    font-size: 11px !important;
  }
}


/* === Option A: Telegram Header Button & Modal === */
.btn-tg-header {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 11px;
  border-radius: 7px;
  background: var(--card);
  border: 1px solid var(--line-strong);
  color: var(--mut-light);
  font-size: 11.5px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}
.btn-tg-header:hover {
  border-color: rgba(255, 255, 255, 0.28);
  color: #fff;
}
.btn-tg-header.live {
  background: rgba(16, 185, 129, 0.16);
  border-color: rgba(16, 185, 129, 0.55);
  color: #34d399;
}
.tg-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #64748b;
  display: inline-block;
}
.btn-tg-header.live .tg-dot {
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
  animation: pulse 2s infinite;
}

.tg-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(4, 7, 14, 0.78);
  backdrop-filter: blur(5px);
  -webkit-backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 99999;
  padding: 14px;
}
.tg-modal-card {
  background: #0f1523;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 12px;
  width: 100%;
  max-width: 440px;
  box-shadow: 0 20px 45px rgba(0, 0, 0, 0.75);
  animation: modalIn 0.18s ease-out;
  overflow: hidden;
}
@keyframes modalIn {
  from { opacity: 0; transform: scale(0.95) translateY(-8px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.tg-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #141a2c;
  border-bottom: 1px solid var(--line);
}
.tg-modal-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 800;
  color: #fff;
}
.tg-modal-close {
  background: transparent;
  border: 0;
  color: var(--mut);
  font-size: 18px;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
  line-height: 1;
}
.tg-modal-close:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}
.tg-modal-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tg-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}
.tg-status-tag {
  font-size: 10px;
  font-weight: 800;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
}
.tg-status-tag.on {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.4);
}
.tg-status-tag.off {
  background: rgba(244, 63, 94, 0.15);
  color: #fb7185;
  border: 1px solid rgba(244, 63, 94, 0.3);
}

.tg-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.tg-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.tg-slider {
  position: absolute;
  cursor: pointer;
  inset: 0;
  background-color: #334155;
  transition: 0.2s;
  border-radius: 24px;
}
.tg-slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: 0.2s;
  border-radius: 50%;
}
.tg-switch input:checked + .tg-slider {
  background-color: #10b981;
}
.tg-switch input:checked + .tg-slider:before {
  transform: translateX(20px);
}

.tg-form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tg-form-group label {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--mut-light);
  letter-spacing: 0.03em;
}
.tg-form-group input {
  height: 38px;
  background: var(--bg);
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  color: #fff;
  padding: 0 10px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  outline: none;
  transition: border 0.15s;
}
.tg-form-group input:focus {
  border-color: #38bdf8;
}
.tg-hint {
  font-size: 9.5px;
  color: var(--mut);
  line-height: 1.35;
}
.tg-feedback-box {
  padding: 9px 12px;
  border-radius: 7px;
  font-size: 11.5px;
  line-height: 1.4;
  font-weight: 600;
}
.tg-feedback-box.ok {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #34d399;
}
.tg-feedback-box.err {
  background: rgba(244, 63, 94, 0.15);
  border: 1px solid rgba(244, 63, 94, 0.4);
  color: #fb7185;
}
.tg-modal-actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.btn-tg-action {
  flex: 1;
  height: 38px;
  border: 0;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
}
.btn-tg-action.test {
  background: rgba(56, 189, 248, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #7dd3fc;
}
.btn-tg-action.test:hover {
  background: #38bdf8;
  color: #080b12;
}
.btn-tg-action.save {
  background: #10b981;
  color: #061e14;
}
.btn-tg-action.save:hover {
  background: #34d399;
}
.btn-tg-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}


/* Excel-style Sortable Table Headers */
th.sortable {
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}
th.sortable:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.sort-icon {
  display: inline-block;
  margin-left: 5px;
  font-size: 10px;
  color: var(--mut);
  vertical-align: middle;
}
.sort-icon.active-desc {
  color: var(--chop);
  font-weight: 800;
}
.sort-icon.active-asc {
  color: #38bdf8;
  font-weight: 800;
}

</style>
</head>
<body>

<header class="top-bar">
  <div class="brand-group">
    <div class="logo-icon">🪓</div>
    <div>
      <div class="brand-title">
        CHOP RADAR TERMINAL
        <span class="brand-badge" style="background:rgba(168,85,247,0.18); color:#d8b4fe; border-color:rgba(168,85,247,0.4)">SOLANA METEORA DLMM</span>
      </div>
    </div>
  </div>

  <!-- Central Mode Switcher: Tanpa Campur-Campur -->
  <div class="mode-switch-group">
    <button class="mode-btn active" id="btn-mode-standard" onclick="switchMasterMode('STANDARD')">
      <span>⚡</span> Standar Chop Radar
    </button>
    <button class="mode-btn mode-absorb" id="btn-mode-micro" onclick="switchMasterMode('MICRO')">
      <span>🔬</span> Absorption LP Radar (Mage Microstructure)
    </button>
  </div>

  <!-- Active Network Indicator -->
  <div class="chain-switch-group" style="background:rgba(168,85,247,0.1); border-color:rgba(168,85,247,0.3)">
    <div style="display:flex; align-items:center; gap:6px; padding:5px 12px; font-weight:700; color:#d8b4fe; font-size:11.5px">
      <span>🟣</span>
      <span>SOLANA METEORA DLMM</span>
    </div>
  </div>

  <div class="top-right">
    <div id="err-banner" style="display:none; color:var(--bad); font-size:11.5px; font-weight:600; padding:4px 9px; border-radius:6px; background:var(--bad-soft); border:1px solid rgba(244,63,94,0.3)"></div>
    <div class="status-pill">
      <div class="pulse-dot"></div>
      <span id="txt-timer">Next scan: 05:00</span>
      <span style="color:var(--line-strong)">|</span>
      <span id="txt-meta">Memuat...</span>
    </div>
    <button class="btn-tg-header" id="btn-tg-header" type="button" onclick="toggleTgModal()" title="Pengaturan Bot Telegram">
      <span class="tg-dot" id="tg-header-dot"></span>
      <span>✈️</span>
      <span id="tg-header-label">Bot: OFF</span>
    </button>
    <button class="btn-scan" id="btn-scan" type="button">Scan Sekarang</button>
  </div>
</header>

<!-- ================= MODE 1: STANDAR CHOP RADAR (PORT 8768 INHERITED) ================= -->
<div class="subview active-view" id="view-standard">
  <div class="kpi-strip">
    <div class="kpi-card">
      <div class="kpi-label">⚡ Siap LP (Chop)</div>
      <div class="kpi-val chop" id="kpi-chop">0</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Rata-Rata V/L (LP)</div>
      <div class="kpi-val chop" id="kpi-vl">—</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Top Est. Fee / Jam</div>
      <div class="kpi-val chop" id="kpi-fee">—</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Pair Di-scan</div>
      <div class="kpi-val" id="kpi-total">0</div>
    </div>
  </div>

  <div class="control-bar">
    <div class="tabs-group" id="tabs-standard">
      <button class="tab-btn active" data-tab="ALL">Semua Tampilan</button>
      <button class="tab-btn" data-tab="CHOP">⚡ Siap LP Saja</button>
      <button class="tab-btn" data-tab="LOG">🕒 Log 24 Jam</button>
      <button class="tab-btn" data-tab="GAP">🔍 Belum Kriteria</button>
    </div>

    <div class="filter-bar">
      <div class="f-input-group">
        <label>Min Liq $</label>
        <input id="f-liq"/>
      </div>
      <div class="f-input-group">
        <label>Min Mcap $</label>
        <input id="f-mcap"/>
      </div>
      <div class="f-input-group">
        <label>Min V/L</label>
        <input id="f-vl"/>
      </div>
      <div class="f-input-group">
        <label>Posisi $</label>
        <input id="f-pos"/>
      </div>
      <div class="f-input-group">
        <label>|5m| Max %</label>
        <input id="f-5m"/>
      </div>
      <div class="f-input-group">
        <label>1h Max %</label>
        <input id="f-1h"/>
      </div>
      <div class="f-input-group">
        <label>Max Top10 %</label>
        <input id="f-top10"/>
      </div>
      <div class="f-input-group">
        <label>Max ER</label>
        <input id="f-er"/>
      </div>
      <div class="f-input-group">
        <label>Max Dev %</label>
        <input id="f-dev"/>
      </div>
      <div class="f-input-group">
        <label>Max Insider %</label>
        <input id="f-insider"/>
      </div>
      <button class="btn-apply" id="btn-apply" type="button">Terapkan</button>
    </div>
  </div>

  <main class="workstation mode-all" id="workstation-standard">
    <section class="panel" id="main-panel">
      <div class="panel-head">
        <div class="panel-title">
          <span id="view-title">Daftar Sinyal LP</span>
          <span style="font-family:'JetBrains Mono',monospace; font-size:11px; padding:1px 6px; border-radius:10px; background:rgba(255,255,255,0.08)" id="view-count">0</span>
        </div>
        <div style="font-size:11px; color:var(--mut);">Auto-scan tiap kelipatan 5m (:00, :05, :10, dst.) · Port 8769</div>
      </div>
      <div class="panel-scroll" id="view-scroll"></div>
    </section>

    <div class="bottom-split" id="bottom-split">
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <span>🕒 Log 24 Jam LP Hits</span>
            <span style="font-family:'JetBrains Mono',monospace; font-size:11px; padding:1px 6px; border-radius:10px; background:rgba(255,255,255,0.08)" id="bottom-log-count">0</span>
          </div>
          <div style="font-size:11px; color:var(--mut);">Riwayat Sinyal LP</div>
        </div>
        <div class="panel-scroll" id="bottom-log-scroll"></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <span>🔍 Belum Kriteria (Alasan / Gaps)</span>
            <span style="font-family:'JetBrains Mono',monospace; font-size:11px; padding:1px 6px; border-radius:10px; background:rgba(255,255,255,0.08)" id="bottom-gap-count">0</span>
          </div>
          <div style="font-size:11px; color:var(--mut);">Alasan Belum Lolos Hard Filter</div>
        </div>
        <div class="panel-scroll" id="bottom-gap-scroll"></div>
      </section>
    </div>
  </main>
</div>

<!-- ================= MODE 2: DEDICATED ABSORPTION & STATE MACHINE (MAGE NOTE) ================= -->
<div class="subview" id="view-micro">
  <div class="kpi-strip">
    <div class="kpi-card">
      <div class="kpi-label">🟢 Prime Absorption (Entry LP)</div>
      <div class="kpi-val chop" id="kpi-prime-count">0</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Top Absorption Score</div>
      <div class="kpi-val purple" id="kpi-top-score">—</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">💰 Top Est. Profit ($100 Modal)</div>
      <div class="kpi-val chop" id="kpi-prime-fee" style="font-size:14px">—</div>
      <div style="font-size:10px; color:var(--mut-light); font-family:'JetBrains Mono',monospace" id="kpi-prime-fee-sub">24h: — · BE: —</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Chain Regime Mode</div>
      <div class="kpi-val" style="color:var(--blue); font-size:14px" id="kpi-regime">Dual-Chain Flow</div>
      <div style="font-size:10px; color:var(--mut-light); font-family:'JetBrains Mono',monospace">Chop Sideways Focus</div>
    </div>
  </div>

  <div class="strategy-banner">
    <div class="strat-text">
      <b>💡 Core LP Edge (Mage Microstructure Note)</b>: 
      <i>"Absorption > Momentum"</i>. Jangan kejar koin yang sedang terbang vertikal (EXPANSION) atau yang sedang dump (DISTRIBUTION).
      Cari koin di fase <b>ABSORPTION / UGLY REACCUMULATION</b>: Volume masif sedang diserap tanpa harga jebol. Inilah ladang fee terkaya untuk CLMM LP di Solana & Uniswap V3 di Robinhood!
    </div>
    <div class="strat-tag">MAGE NOTE STRATEGY</div>
  </div>

  <!-- Telegram Quick Configuration Bar -->
  <!-- Telegram Config Modal (Option A) -->
<div id="tg-modal" class="tg-modal-overlay" style="display:none;" onclick="if(event.target===this) toggleTgModal(false)">
  <div class="tg-modal-card">
    <div class="tg-modal-header">
      <div class="tg-modal-title">
        <span>✈️</span>
        <span>Pengaturan Bot Telegram LP</span>
      </div>
      <button type="button" class="tg-modal-close" onclick="toggleTgModal(false)" title="Tutup">✕</button>
    </div>
    <div class="tg-modal-body">
      <div class="tg-switch-row">
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:12px; font-weight:700; color:#e2e8f0">Status Notifikasi Bot:</span>
          <span id="tg-modal-status-text" class="tg-status-tag off">OFF</span>
        </div>
        <label class="tg-switch">
          <input type="checkbox" id="tg-modal-enabled">
          <span class="tg-slider"></span>
        </label>
      </div>

      <div class="tg-form-group">
        <label>Bot Token Telegram (dari @BotFather)</label>
        <input type="text" id="tg-modal-token" placeholder="7123456789:AAHqxxxxxxxxxxxxxxxxx" spellcheck="false" autocomplete="off">
        <div class="tg-hint">Buka Telegram &gt; cari <b>@BotFather</b> &gt; ketik /newbot untuk buat bot dan copy tokennya.</div>
      </div>

      <div class="tg-form-group">
        <label>Chat ID Telegram Anda (dari @userinfobot)</label>
        <input type="text" id="tg-modal-chat-id" placeholder="123456789" spellcheck="false" autocomplete="off">
        <div class="tg-hint">Buka Telegram &gt; cari <b>@userinfobot</b> &gt; klik Start untuk melihat angka Id akun Anda.</div>
      </div>

      <div id="tg-modal-feedback" class="tg-feedback-box" style="display:none;"></div>

      <div class="tg-modal-actions">
        <button type="button" class="btn-tg-action test" id="btn-modal-test" onclick="testTelegramHP()">🧪 Kirim Test</button>
        <button type="button" class="btn-tg-action save" id="btn-modal-save" onclick="saveTelegramHP()">💾 Simpan & Aktifkan</button>
      </div>
    </div>
  </div>
</div>
<div class="tg-bar" style="display:none;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="font-size:14px">🔔</span>
      <span style="font-weight:700; font-size:11.5px; color:#e2e8f0">Telegram LP Alert Bot:</span>
      <span id="tg-status-pill" style="font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px; background:rgba(255,255,255,0.08); color:var(--mut-light)">Menunggu Config</span>
    </div>
    <div class="tg-inputs">
      <div class="tg-input-group">
        <label>Bot Token</label>
        <input id="tg-token" type="password" placeholder="123456:ABC-DEF..." style="width:140px"/>
      </div>
      <div class="tg-input-group">
        <label>Chat ID</label>
        <input id="tg-chat-id" placeholder="-100... / 98765..." style="width:105px"/>
      </div>
      <button class="btn-tg btn-tg-save" id="btn-tg-save" type="button">Simpan Bot</button>
      <button class="btn-tg btn-tg-test" id="btn-tg-test" type="button">Kirim Test</button>
    </div>
  </div>

  <div class="control-bar">
    <div class="tabs-group" id="tabs-micro">
      <button class="tab-btn active" data-filter="ALL">Semua Token (State Machine)</button>
      <button class="tab-btn" data-filter="ABSORPTION" style="color:#34d399">🟢 Absorption Saja (LP Siap)</button>
      <button class="tab-btn" data-filter="EXPANSION" style="color:#fbbf24">🟡 Expansion (Pump Vertikal)</button>
      <button class="tab-btn" data-filter="DISTRIBUTION" style="color:#fb7185">🔴 Distribution (Dump / Toxic)</button>
    </div>

    <div class="filter-bar">
      <div class="f-input-group">
        <label>Min Absorb Score</label>
        <input id="f-min-score" value="65"/>
      </div>
      <div class="f-input-group">
        <label>Min Mcap $</label>
        <input id="f-mcap-micro" style="width:78px" value="500000"/>
      </div>
      <div class="f-input-group">
        <label>Min Holder</label>
        <input id="f-holder-micro" style="width:55px" value="150"/>
      </div>
      <div class="f-input-group">
        <label>Modal LP $</label>
        <input id="f-pos-micro" value="100"/>
      </div>
      <button class="btn-apply" id="btn-apply-micro" type="button">Terapkan Tuning</button>
    </div>
  </div>

  <main class="workstation" style="padding-top:0">
    <!-- State Machine & Recommendation Table -->
    <section class="panel" style="flex:1">
      <div class="panel-head">
        <div class="panel-title">
          <span>🔬 Tabel State Machine Microstructure & Rekomendasi Aksi LP</span>
          <span style="font-family:'JetBrains Mono',monospace; font-size:11px; padding:1px 6px; border-radius:10px; background:rgba(168,85,247,0.15); color:#d8b4fe" id="micro-count">0</span>
        </div>
        <div style="font-size:11px; color:var(--mut);">State Tracker: Absorption (LP Entry) · Expansion (Out of Range) · Distribution (Toxic Dump)</div>
      </div>
      <div class="panel-scroll" id="micro-scroll"></div>
    </section>
  </main>
</div>


<script>
const usd = n => !n ? '—' : n >= 1e6 ? (n/1e6).toFixed(2)+'m' : n >= 1e3 ? (n/1e3).toFixed(1)+'k' : String(Math.round(n));
const fee = n => n == null || n === 0 ? '—' : '$' + n.toFixed(2) + '/h';
const pct = n => `<span style="color:${n > 0 ? 'var(--chop)' : n < 0 ? 'var(--bad)' : 'var(--mut)'}">${n >= 0 ? '+' : ''}${n.toFixed(1)}%</span>`;
const jam = ms => new Date(ms).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });

let masterMode = 'STANDARD'; // 'STANDARD' or 'MICRO'
let activeStandardTab = 'ALL';
let activeMicroFilter = 'ALL';
let currentData = {};
let activeChainFilter = 'BOTH';
let microSortCol = null;   // 'fee'
let microSortDir = 'none';  // 'none', 'desc', 'asc'

function toggleMicroSort(col) {
  if (microSortCol !== col) {
    microSortCol = col;
    microSortDir = 'desc';
  } else if (microSortDir === 'desc') {
    microSortDir = 'asc';
  } else if (microSortDir === 'asc') {
    microSortCol = null;
    microSortDir = 'none';
  } else {
    microSortDir = 'desc';
  }
  renderCurrentView();
}


function setChainFilter(mode) {
  activeChainFilter = mode;
  document.querySelectorAll('.chain-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById(mode === 'SOL' ? 'chain-btn-sol' : mode === 'RH' ? 'chain-btn-rh' : mode === 'ARC' ? 'chain-btn-arc' : 'chain-btn-both');
  if (btn) btn.classList.add('active');

  // Persist chain filter to backend filters
  fetch('/api/filters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chain_mode: mode })
  }).catch(() => {});

  renderCurrentView();
}

function renderLinks(t) {
  return `<div class="links-group">
    <a class="btn-link" style="background:rgba(168,85,247,0.2); color:#e9d5ff; border-color:rgba(168,85,247,0.45); font-weight:700" href="${t.meteora}" target="_blank" rel="noreferrer" title="Buka Pool Meteora DLMM">Meteora</a>
    <a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer" title="DexScreener Solana">DexS</a>
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer" title="GMGN Sol">GMGN</a>
    <a class="btn-link" href="${t.twitter}" target="_blank" rel="noreferrer" title="Cek Narasi X">X</a>
  </div>`;
}

function renderGapsLinks(t) {
  return `<div class="links-group">
    <a class="btn-link" style="background:rgba(168,85,247,0.2); color:#e9d5ff; border-color:rgba(168,85,247,0.45); font-weight:700" href="${t.meteora}" target="_blank" rel="noreferrer" title="Buka Pool Meteora DLMM">Meteora</a>
    <a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer">DexS</a>
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
  </div>`;
}

function renderLogLinks(t) {
  return `<div class="links-group">
    <a class="btn-link" style="background:rgba(168,85,247,0.2); color:#e9d5ff; border-color:rgba(168,85,247,0.45); font-weight:700" href="${t.meteora}" target="_blank" rel="noreferrer" title="Buka Pool Meteora DLMM">Meteora</a>
    <a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer">DexS</a>
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
  </div>`;
}
  if (t.chain === 'ARC') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer" title="DexScreener Arc">DexS</a>
      <a class="btn-link" href="${t.twitter}" target="_blank" rel="noreferrer" title="Cek Narasi X">X</a>
    </div>`;
  }
  return `<div class="links-group">
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
    <a class="btn-link" href="${t.fomo}" target="_blank" rel="noreferrer" title="FOMO Family">FOMO</a>
    <a class="btn-link" href="${t.barker}" target="_blank" rel="noreferrer" title="BarkerMoney LP">Barker</a>
    <a class="btn-link" href="${t.twitter}" target="_blank" rel="noreferrer" title="Cek Narasi X">X</a>
  </div>`;
}

function renderGapsLinks(t) {
  if (t.chain === 'SOL') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="https://dexscreener.com/solana/${t.address}" target="_blank" rel="noreferrer">DexS</a>
    </div>`;
  }
  if (t.chain === 'ARC') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="https://dexscreener.com/arc/${t.address}" target="_blank" rel="noreferrer">DexS</a>
    </div>`;
  }
  return `<div class="links-group">
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
    <a class="btn-link" href="${t.fomo}" target="_blank" rel="noreferrer">FOMO</a>
  </div>`;
}

function renderLogLinks(t) {
  if (t.chain === 'SOL') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="https://dexscreener.com/solana/${t.address}" target="_blank" rel="noreferrer">DexS</a>
    </div>`;
  }
  if (t.chain === 'ARC') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="https://dexscreener.com/arc/${t.address}" target="_blank" rel="noreferrer">DexS</a>
    </div>`;
  }
  return `<div class="links-group">
    <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
    <a class="btn-link" href="https://fomo.family/token/${t.address}" target="_blank" rel="noreferrer">FOMO</a>
  </div>`;
}

function switchMasterMode(mode) {
  masterMode = mode;
  document.getElementById('btn-mode-standard').classList.toggle('active', mode === 'STANDARD');
  document.getElementById('btn-mode-micro').classList.toggle('active', mode === 'MICRO');
  document.getElementById('view-standard').classList.toggle('active-view', mode === 'STANDARD');
  document.getElementById('view-micro').classList.toggle('active-view', mode === 'MICRO');
  renderCurrentView();
}

function copyText(txt, btn, msg = '✓') {
  navigator.clipboard.writeText(txt).then(() => {
    const orig = btn.innerHTML;
    btn.innerHTML = msg;
    setTimeout(() => { btn.innerHTML = orig; }, 1200);
  });
}

function renderTable(rows) {
  if (!rows.length) {
    return `<div style="padding:40px; text-align:center; color:var(--mut)">Tidak ada token pada kategori ini.</div>`;
  }

  const erColor = v => v <= 5 ? 'var(--chop)' : v <= 15 ? 'var(--pump)' : 'var(--bad)';
  const trendColor = t => t === '↗' ? 'var(--chop)' : t === '→' ? 'var(--mut-light)' : t === '↘' ? 'var(--pump)' : t === '↓↓' ? 'var(--bad)' : 'var(--mut)';
  const sparkline = arr => {
    if (!arr || arr.length < 2) return '';
    const vals = arr.map(v => '$' + v.toFixed(2));
    return `<div style="font-size:9.5px; color:var(--mut); margin-top:2px; font-family:'JetBrains Mono',monospace">${vals.join(' → ')}</div>`;
  };

  const feeIcon = microSortCol === 'fee' ? (microSortDir === 'desc' ? '▼' : '▲') : '⇅';
  const feeIconCls = microSortCol === 'fee' ? (microSortDir === 'desc' ? 'sort-icon active-desc' : 'sort-icon active-asc') : 'sort-icon';

  let h = `<table><thead><tr>
    <th>Token</th>
    <th>Sinyal</th>
    <th class="num">Current MC</th>
    <th class="num">Liq</th>
    <th class="num">V/L</th>
    <th class="num" title="Efficiency Ratio: |1h%| / V/L — semakin kecil = semakin choppy">ER</th>
    <th style="text-align:center" title="Keterangan Tambahan untuk Keputusan Manual Entry: Momentum (Drill Down), Drop ATH, dan Order Flow">Advisory Radar (Manual Guide)</th>
    <th class="num">Est. Fee/h</th>
    <th class="num">5m / 1h</th>
    <th style="text-align:center">On-Chain Safety (GMGN)</th>
    <th style="text-align:right">Riset Hub</th>
  </tr></thead><tbody>`;

  rows.forEach(t => {
    const top10Class = t.top10_rate <= 25 ? 'safe-ok' : t.top10_rate <= 45 ? 'safe-warn' : 'safe-bad';
    const trend = t.fee_trend || '—';
    const tColor = trendColor(trend);
    const athText = t.ath_mc ? `<div style="font-size:10px; color:var(--mut)" title="Puncak ATH: $${usd(t.ath_mc)}">ATH: $${usd(t.ath_mc)} (${t.drop_ath >= 0 ? '+' : ''}${t.drop_ath.toFixed(0)}%)</div>` : '';

    const advPills = (t.advisories || []).map(a => {
      const cls = a.level === 'danger' ? 'adv-danger' : a.level === 'warn' ? 'adv-warn' : a.level === 'ok' ? 'adv-ok' : 'adv-muted';
      return `<span class="adv-badge ${cls}" title="${a.title || ''}">${a.label}</span>`;
    }).join('');

    h += `<tr>
      <td>
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-weight:800; color:#fff">${t.symbol}</span><span style="font-size:9.5px; padding:1px 4px; border-radius:3px; background:rgba(168,85,247,0.15); color:#d8b4fe; font-family:monospace" title="Bin Step Meteora">B:${t.bin_step || 0}</span>
          <span class="chain-pill chain-sol">SOL</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
      </td>
      <td><span class="badge-tag tag-${t.tag}">${t.tag}</span></td>
      <td class="num">
        <div style="font-weight:600">$${usd(t.mcap)}</div>
        ${athText}
      </td>
      <td class="num">$${usd(t.liq)}</td>
      <td class="num" style="font-weight:600; color:var(--chop)">${t.vl.toFixed(1)}×</td>
      <td class="num" style="font-weight:600; color:${erColor(t.er || 0)}" title="ER rendah = choppy (ideal LP)">${t.er != null ? t.er.toFixed(1) : '—'}</td>
      <td style="text-align:center">
        <div style="display:flex; align-items:center; justify-content:center; flex-wrap:wrap; gap:2px">
          ${advPills || '—'}
        </div>
      </td>
      <td class="num">
        <div>
          <span style="color:var(--chop); font-weight:600">${fee(t.fee_hour)}</span>
          <span style="color:${tColor}; font-weight:700; margin-left:4px" title="Fee Trend">${trend}</span>
        </div>
        ${sparkline(t.fee_spark)}
      </td>
      <td class="num">${pct(t.p5)} / ${pct(t.p1)}</td>
      <td style="text-align:center">
        <span class="safe-pill ${top10Class}" title="Top 10 Holder Rate">Top10: ${t.top10_rate}%</span>
        ${t.snipers > 0 ? `<span class="safe-pill safe-warn" title="Sniper Wallets">🎯 ${t.snipers}</span>` : ''}
        ${t.insider_rate > 5 ? `<span class="safe-pill safe-bad" title="Insider Wallets">🐭 ${t.insider_rate}%</span>` : ''}
        ${t.dev_team_hold > 10 ? `<span class="safe-pill safe-warn" title="Dev Team Hold">🔒 Dev ${t.dev_team_hold}%</span>` : ''}
      </td>
      <td style="text-align:right">
        ${renderLinks(t)}
      </td>
    </tr>`;
  });
  h += `</tbody></table>`;
  return h;
}

function renderGapsTable(rows) {
  if (!rows.length) return `<div style="padding:20px; text-align:center; color:var(--mut)">Semua token memenuhi kriteria atau belum ada data.</div>`;
  let h = `<table><thead><tr>
    <th>Token</th>
    <th class="num">Mcap</th>
    <th class="num">Liq / V/L</th>
    <th>Alasan Belum Lolos (Gaps)</th>
    <th style="text-align:right">Riset</th>
  </tr></thead><tbody>`;
  rows.forEach(t => {
    const pills = (t.gaps || []).map(g => `<span class="gap-pill">${g}</span>`).join('');
    h += `<tr>
      <td>
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-weight:800; color:#fff">${t.symbol}</span><span style="font-size:9.5px; padding:1px 4px; border-radius:3px; background:rgba(168,85,247,0.15); color:#d8b4fe; font-family:monospace" title="Bin Step Meteora">B:${t.bin_step || 0}</span>
          <span class="chain-pill chain-sol">SOL</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
      </td>
      <td class="num">$${usd(t.mcap)}</td>
      <td class="num">$${usd(t.liq)} <span style="color:var(--mut)">(${t.vl.toFixed(1)}×)</span></td>
      <td>${pills || '—'}</td>
      <td style="text-align:right">
        ${renderGapsLinks(t)}
      </td>
    </tr>`;
  });
  h += `</tbody></table>`;
  return h;
}

function renderLogTable(rows) {
  if (!rows.length) return `<div style="padding:20px; text-align:center; color:var(--mut)">Belum ada log 24 jam.</div>`;
  let h = `<table><thead><tr>
    <th>Token</th>
    <th>Sinyal</th>
    <th>Terakhir</th>
    <th>Kemunculan</th>
    <th style="text-align:right">Riset</th>
  </tr></thead><tbody>`;
  rows.forEach(t => {
    h += `<tr>
      <td>
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-weight:800; color:#fff">${t.symbol}</span><span style="font-size:9.5px; padding:1px 4px; border-radius:3px; background:rgba(168,85,247,0.15); color:#d8b4fe; font-family:monospace" title="Bin Step Meteora">B:${t.bin_step || 0}</span>
          <span class="chain-pill chain-sol">SOL</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
      </td>
      <td><span class="badge-tag tag-${t.tag}">${t.tag}</span></td>
      <td>${jam(t.at)}</td>
      <td style="font-size:11px; color:var(--mut-light)">${(t.hits || []).map(jam).join(' · ')}</td>
      <td style="text-align:right">
        ${renderLogLinks(t)}
      </td>
    </tr>`;
  });
  h += `</tbody></table>`;
  return h;
}

/* ================= RENDER DEDICATED MICROSTRUCTURE TABLE ================= */
function renderMicroTable(rows) {
  if (!rows.length) {
    return `<div style="padding:40px; text-align:center; color:var(--mut)">Tidak ada token yang cocok dengan filter State saat ini.</div>`;
  }

  let h = `<table><thead><tr>
    <th>Token</th>
    <th>Micro State</th>
    <th>Aksi Rekomendasi LP</th>
    <th class="num" title="Absorption Score: Efisiensi penyerapan supply (0-100)">Absorb Score</th>
    <th class="num">Current MC</th>
    <th class="num">Liq / V/L</th>
    <th style="min-width:145px" title="LP Range Advisor: Rekomendasi batas aman berdasarkan volatilitas & depth">📐 LP Range Advisor</th>
    <th class="num sortable" style="min-width:125px" onclick="toggleMicroSort('fee')" title="Klik untuk mengurutkan (Tertinggi / Terendah / Default)">💰 Est. Fee ($100) <span class="${feeIconCls}">${feeIcon}</span></th>
    <th style="text-align:center">👥 Safety & Holders</th>
    <th>Catatan Mikrostruktur (Mage Note)</th>
    <th style="text-align:right">Riset Hub</th>
  </tr></thead><tbody>`;

  rows.forEach(t => {
    const m = t.micro || {};
    const state = m.state || 'NEUTRAL';
    const stateClass = state === 'ABSORPTION' ? 'state-absorb' :
                       state === 'EXPANSION' ? 'state-expand' :
                       state === 'DISTRIBUTION' ? 'state-dist' :
                       state === 'REACCUMULATION' ? 'state-reaccum' : 'state-neutral';

    const actType = m.action_type || 'WAIT';
    const actClass = actType === 'PRIME' ? 'act-prime' :
                     actType === 'VALID' ? 'act-valid' :
                     actType === 'WARN' ? 'act-warn' :
                     actType === 'DANGER' ? 'act-danger' : 'act-wait';

    const score = m.score || 0;
    const scoreColor = score >= 75 ? 'var(--chop)' : score >= 55 ? '#38bdf8' : score >= 40 ? 'var(--pump)' : 'var(--bad)';

    const buyRatio = m.buy_ratio || 50;
    const flowColor = buyRatio >= 52 ? 'var(--chop)' : buyRatio < 45 ? 'var(--bad)' : 'var(--mut-light)';

    const lp = m.lp_range || t.lp_range || {};
    const lowS = lp.lower ? `$${lp.lower.toFixed(8).replace(/\.?0+$/, '')}` : '—';
    const upS = lp.upper ? `$${lp.upper.toFixed(8).replace(/\.?0+$/, '')}` : '—';
    const rangePct = lp.range_pct || 20;
    const safeBadgeCls = lp.safety === 'Safe' ? 'lp-range-safe' : (lp.safety === 'Wide' ? 'lp-range-wide' : 'lp-range-risky');

    const feeH = t.fee_hour || 0;
    const fee24 = t.fee_24h || (feeH * 24);
    const be = t.breakeven_hours || 0;
    const beStr = be && be < 9999 ? `${be.toFixed(0)}h` : '—';

    // Holders badge
    const holders = t.holders || 0;
    const holderCls = holders >= 150 ? 'safe-ok' : (holders >= 100 ? 'safe-warn' : 'safe-bad');

    h += `<tr>
      <td>
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-weight:800; color:#fff; font-size:12.5px">${t.symbol}</span><span style="font-size:9.5px; padding:1px 4px; border-radius:3px; background:rgba(168,85,247,0.15); color:#d8b4fe; font-family:monospace" title="Bin Step Meteora">B:${t.bin_step || 0}</span>
          <span class="chain-pill chain-sol">SOL</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
        <div style="font-size:10px; color:var(--mut)">${t.pool_name || t.name.slice(0, 14)}</div>
      </td>
      <td>
        <span class="state-pill ${stateClass}">${m.state_title || state}</span>
      </td>
      <td>
        <span class="action-badge ${actClass}">${m.action || '—'}</span>
      </td>
      <td class="num">
        <div class="score-gauge-wrap" style="float:right">
          <div class="score-gauge-bar">
            <div class="score-gauge-fill" style="width:${score}%; background:${scoreColor}"></div>
          </div>
          <span class="score-num" style="color:${scoreColor}">${score.toFixed(0)}</span>
        </div>
      </td>
      <td class="num">
        <div style="font-weight:700">$${usd(t.mcap)}</div>
      </td>
      <td class="num">
        <div>$${usd(t.liq)}</div>
        <div style="font-weight:700; color:var(--chop)">${t.vl.toFixed(1)}×</div>
      </td>
      <td>
        <div class="lp-range-box">
          <div class="lp-range-vals">
            <span>${lowS}</span> — <span>${upS}</span>
            <button class="btn-copy-ca" style="font-size:9px" onclick="copyText('${lowS} - ${upS}', this)" title="Salin Range">📋</button>
          </div>
          <span class="lp-range-badge ${safeBadgeCls}">±${rangePct}% · ${lp.safety || 'Safe'}</span>
        </div>
      </td>
      <td class="num">
        <div style="color:var(--chop); font-weight:700">$${feeH.toFixed(2)}/h</div>
        <div style="font-size:10px; color:var(--mut-light)">$${fee24.toFixed(2)}/d · BE: ${beStr}</div>
      </td>
      <td style="text-align:center">
        <div style="display:flex; align-items:center; justify-content:center; gap:4px; flex-wrap:wrap">
          <span class="safe-pill ${holderCls}" title="Holder Count">👥 ${holders}</span>
          <span class="safe-pill" style="color:${flowColor}; background:rgba(255,255,255,0.05)" title="Buy Ratio">${buyRatio.toFixed(0)}% Buy</span>
        </div>
        <div style="font-size:9.5px; color:var(--mut); margin-top:2px">Top10: ${t.top10_rate}%</div>
      </td>
      <td style="max-width:240px">
        <div style="font-size:11px; color:#cbd5e1; line-height:1.3">${m.clue || '—'}</div>
      </td>
      <td style="text-align:right">
        ${renderLinks(t)}
      </td>
    </tr>`;
  });

  h += `</tbody></table>`;
  return h;
}

function toggleTgModal(show) {
  const modal = document.getElementById('tg-modal');
  if (!modal) return;
  if (show === undefined) {
    modal.style.display = (modal.style.display === 'none' || !modal.style.display) ? 'flex' : 'none';
  } else {
    modal.style.display = show ? 'flex' : 'none';
  }
  const fb = document.getElementById('tg-modal-feedback');
  if (fb) fb.style.display = 'none';
}

function updateTgHeaderBadge(enabled) {
  const btn = document.getElementById('btn-tg-header');
  const lbl = document.getElementById('tg-header-label');
  if (btn && lbl) {
    if (enabled) {
      btn.classList.add('live');
      lbl.textContent = 'Bot: LIVE';
    } else {
      btn.classList.remove('live');
      lbl.textContent = 'Bot: OFF';
    }
  }
}

async function testTelegramHP() {
  const btn = document.getElementById('btn-modal-test');
  const fb = document.getElementById('tg-modal-feedback');
  const token = (document.getElementById('tg-modal-token').value || '').trim();
  const chatId = (document.getElementById('tg-modal-chat-id').value || '').trim();

  if (!token || !chatId) {
    if (fb) {
      fb.style.display = 'block';
      fb.className = 'tg-feedback-box err';
      fb.textContent = '❌ Bot Token dan Chat ID wajib diisi terlebih dahulu!';
    }
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Mengirim...';
  if (fb) fb.style.display = 'none';

  try {
    const res = await fetch('/api/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, chat_id: chatId })
    });
    const data = await res.json();
    if (fb) {
      fb.style.display = 'block';
      if (data.ok) {
        fb.className = 'tg-feedback-box ok';
        fb.textContent = '✅ Berhasil! Pesan uji coba berhasil masuk ke Telegram Anda. Jangan lupa klik "Simpan & Aktifkan".';
      } else {
        fb.className = 'tg-feedback-box err';
        fb.textContent = '❌ Gagal: ' + (data.error || 'Pastikan token benar & Anda sudah klik /start di bot Anda.');
      }
    }
  } catch (e) {
    if (fb) {
      fb.style.display = 'block';
      fb.className = 'tg-feedback-box err';
      fb.textContent = '❌ Error koneksi: ' + e.message;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Kirim Test';
  }
}

async function saveTelegramHP() {
  const btn = document.getElementById('btn-modal-save');
  const fb = document.getElementById('tg-modal-feedback');
  const token = (document.getElementById('tg-modal-token').value || '').trim();
  const chatId = (document.getElementById('tg-modal-chat-id').value || '').trim();
  const enabled = document.getElementById('tg-modal-enabled').checked;

  btn.disabled = true;
  btn.textContent = 'Menyimpan...';

  try {
    const res = await fetch('/api/telegram/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        telegram_token: token,
        telegram_chat_id: chatId,
        telegram_enabled: enabled
      })
    });
    const data = await res.json();
    if (fb) {
      fb.style.display = 'block';
      if (data.ok) {
        fb.className = 'tg-feedback-box ok';
        fb.textContent = '💾 Tersimpan! Status notifikasi: ' + (enabled ? 'AKTIF (LIVE) 🟢' : 'NONAKTIF ⚪');
        updateTgHeaderBadge(enabled);
        setTimeout(() => { toggleTgModal(false); }, 1400);
      } else {
        fb.className = 'tg-feedback-box err';
        fb.textContent = '❌ Gagal menyimpan pengaturan.';
      }
    }
  } catch (e) {
    if (fb) {
      fb.style.display = 'block';
      fb.className = 'tg-feedback-box err';
      fb.textContent = '❌ Error: ' + e.message;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '💾 Simpan & Aktifkan';
  }
}

function paint(data) {
  currentData = data;
  const f = data.filters || {};
  if (f.chain_mode && !['BOTH', 'SOL', 'RH', 'ARC'].includes(activeChainFilter)) {
    activeChainFilter = f.chain_mode.toUpperCase();
  }
  document.querySelectorAll('.chain-btn').forEach(b => b.classList.remove('active'));
  const curBtn = document.getElementById(activeChainFilter === 'SOL' ? 'chain-btn-sol' : activeChainFilter === 'RH' ? 'chain-btn-rh' : activeChainFilter === 'ARC' ? 'chain-btn-arc' : 'chain-btn-both');
  if (curBtn) curBtn.classList.add('active');
  document.getElementById('f-liq').value = f.min_liq ?? 30000;
  document.getElementById('f-mcap').value = f.min_mcap ?? 0;
  document.getElementById('f-vl').value = f.min_vl ?? 3;
  document.getElementById('f-pos').value = f.position ?? 100;
  document.getElementById('f-5m').value = f.max_5m ?? 12;
  document.getElementById('f-1h').value = f.max_1h ?? 80;
  document.getElementById('f-top10').value = f.max_top10 ?? 45;
  document.getElementById('f-er').value = f.max_er ?? 20;
  document.getElementById('f-dev').value = f.max_dev_hold ?? 20;
  document.getElementById('f-insider').value = f.max_insider ?? 10;

  document.getElementById('f-min-score').value = f.min_absorb_score ?? 65;
  document.getElementById('f-mcap-micro').value = f.min_absorb_mcap ?? 500000;
  document.getElementById('f-holder-micro').value = f.min_holder ?? 150;
  document.getElementById('f-pos-micro').value = f.position ?? 100;

  // Telegram Config inputs & status
  if (f.telegram_token) document.getElementById('tg-token').value = f.telegram_token;
  if (f.telegram_chat_id) document.getElementById('tg-chat-id').value = f.telegram_chat_id;
  const tgPill = document.getElementById('tg-status-pill');
  if (f.telegram_token && f.telegram_chat_id) {
    tgPill.style.background = 'rgba(16, 185, 129, 0.2)';
    tgPill.style.color = '#34d399';
    tgPill.textContent = 'Aktif ✅';
  } else {
    tgPill.style.background = 'rgba(255,255,255,0.08)';
    tgPill.style.color = 'var(--mut-light)';
    tgPill.textContent = 'Belum Lengkap';
  }

  const rows = data.rows || [];
  const chops = rows.filter(r => r.tag === 'CHOP');

  // KPI Standard
  document.getElementById('kpi-chop').textContent = chops.length;
  document.getElementById('kpi-total').textContent = `${data.n || 0} Pairs`;
  if (chops.length > 0) {
    const avgVl = chops.reduce((acc, r) => acc + (r.vl || 0), 0) / chops.length;
    document.getElementById('kpi-vl').textContent = avgVl.toFixed(1) + '×';
    const maxFee = Math.max(...chops.map(r => r.fee_hour || 0));
    document.getElementById('kpi-fee').textContent = '$' + maxFee.toFixed(2) + '/h';
  } else {
    document.getElementById('kpi-vl').textContent = '—';
    document.getElementById('kpi-fee').textContent = '—';
  }

  // KPI Microstructure / Mage Note
  const minMcapMicro = Number(document.getElementById('f-mcap-micro')?.value) || (f.min_absorb_mcap ?? 500000);
  const minHolderMicro = Number(document.getElementById('f-holder-micro')?.value) || (f.min_holder ?? 150);
  const primes = rows.filter(r => r.micro && (r.micro.state === 'ABSORPTION' || r.micro.state === 'REACCUMULATION') && (minMcapMicro <= 0 || (r.mcap || 0) >= minMcapMicro) && ((r.holders || 0) >= minHolderMicro));
  document.getElementById('kpi-prime-count').textContent = primes.length;
  if (rows.length > 0) {
    const maxScore = Math.max(...rows.map(r => (r.micro ? r.micro.score : 0)));
    document.getElementById('kpi-top-score').textContent = `${maxScore.toFixed(0)} / 100`;
  } else {
    document.getElementById('kpi-top-score').textContent = '—';
  }

  if (primes.length > 0) {
    const bestPrime = [...primes].sort((a, b) => (b.fee_hour || 0) - (a.fee_hour || 0))[0];
    const maxPrimeFee = bestPrime.fee_hour || 0;
    const maxPrime24 = bestPrime.fee_24h || (maxPrimeFee * 24);
    const be = bestPrime.breakeven_hours || 0;
    document.getElementById('kpi-prime-fee').textContent = '$' + maxPrimeFee.toFixed(2) + '/h';
    document.getElementById('kpi-prime-fee-sub').textContent = `24h: $${maxPrime24.toFixed(2)} · BE: ${be < 9999 ? be.toFixed(0) + 'h' : '—'}`;
  } else {
    document.getElementById('kpi-prime-fee').textContent = '—';
    document.getElementById('kpi-prime-fee-sub').textContent = '24h: — · BE: —';
  }

  const errEl = document.getElementById('err-banner');
  if (data.error) {
    errEl.style.display = 'inline-block';
    errEl.textContent = '⚠️ ' + data.error;
  } else {
    errEl.style.display = 'none';
  }

  const btnScan = document.getElementById('btn-scan');
  btnScan.disabled = !!data.scanning;
  btnScan.textContent = data.scanning ? 'Memindai...' : 'Scan Sekarang';

  renderCurrentView();
  updateTimeDisplay();
}

function renderCurrentView() {
  let rows = currentData.rows || [];
  if (activeChainFilter !== 'BOTH') {
    rows = rows.filter(r => (r.chain || 'RH').toUpperCase() === activeChainFilter);
  }
  let log = currentData.log || [];
  if (activeChainFilter !== 'BOTH') {
    log = log.filter(r => (r.chain || 'RH').toUpperCase() === activeChainFilter);
  }
  const chops = rows.filter(r => r.tag === 'CHOP');
  const gaps = rows.filter(r => r.tag === 'SKIP');

  const regimeEl = document.getElementById('kpi-regime');
  if (regimeEl) {
    regimeEl.textContent = 'Meteora DLMM (Solana Official)';
  }

  if (masterMode === 'STANDARD') {
    const ws = document.getElementById('workstation-standard');
    const scroll = document.getElementById('view-scroll');
    const title = document.getElementById('view-title');
    const count = document.getElementById('view-count');

    if (activeStandardTab === 'ALL') {
      ws.classList.add('mode-all');
      title.textContent = '⚡ Koin Lolos Kriteria (Siap Chop LP)';
      count.textContent = chops.length;
      scroll.innerHTML = renderTable(chops);

      document.getElementById('bottom-log-count').textContent = log.length;
      document.getElementById('bottom-log-scroll').innerHTML = renderLogTable(log);

      document.getElementById('bottom-gap-count').textContent = gaps.length;
      document.getElementById('bottom-gap-scroll').innerHTML = renderGapsTable(gaps);
    } else {
      ws.classList.remove('mode-all');
      if (activeStandardTab === 'CHOP') {
        title.textContent = '⚡ Koin Lolos Kriteria (Siap Chop LP)';
        count.textContent = chops.length;
        scroll.innerHTML = renderTable(chops);
      } else if (activeStandardTab === 'LOG') {
        title.textContent = '🕒 Log 24 Jam LP Hits';
        count.textContent = log.length;
        scroll.innerHTML = renderLogTable(log);
      } else if (activeStandardTab === 'GAP') {
        title.textContent = '🔍 Belum Memenuhi Kriteria (Gaps)';
        count.textContent = gaps.length;
        scroll.innerHTML = renderGapsTable(gaps);
      }
    }
  } else if (masterMode === 'MICRO') {
    let filtered = [...rows];
    const minMcap = Number(document.getElementById('f-mcap-micro')?.value);
    if (!isNaN(minMcap) && minMcap > 0) {
      filtered = filtered.filter(r => (r.mcap || 0) >= minMcap);
    }
    const minHolder = Number(document.getElementById('f-holder-micro')?.value);
    if (!isNaN(minHolder) && minHolder > 0) {
      filtered = filtered.filter(r => (r.holders || 0) >= minHolder);
    }
    if (activeMicroFilter === 'ABSORPTION') {
      filtered = filtered.filter(r => r.micro && (r.micro.state === 'ABSORPTION' || r.micro.state === 'REACCUMULATION'));
    } else if (activeMicroFilter === 'EXPANSION') {
      filtered = filtered.filter(r => r.micro && r.micro.state === 'EXPANSION');
    } else if (activeMicroFilter === 'DISTRIBUTION') {
      filtered = filtered.filter(r => r.micro && r.micro.state === 'DISTRIBUTION');
    }

    // Sort: Est. Fee if sorted, otherwise Absorption & Reaccumulation first, then highest score
    if (microSortCol === 'fee' && microSortDir !== 'none') {
      filtered.sort((a, b) => {
        const fa = a.fee_hour || 0;
        const fb = b.fee_hour || 0;
        return microSortDir === 'desc' ? (fb - fa) : (fa - fb);
      });
    } else {
      filtered.sort((a, b) => {
        const sa = (a.micro && (a.micro.state === 'ABSORPTION' || a.micro.state === 'REACCUMULATION')) ? 1 : 0;
        const sb = (b.micro && (b.micro.state === 'ABSORPTION' || b.micro.state === 'REACCUMULATION')) ? 1 : 0;
        if (sb !== sa) return sb - sa;
        return ((b.micro ? b.micro.score : 0) - (a.micro ? a.micro.score : 0));
      });
    }

    document.getElementById('micro-count').textContent = `${filtered.length} Token`;
    document.getElementById('micro-scroll').innerHTML = renderMicroTable(filtered);
  }
}

function updateTimeDisplay() {
  if (!currentData.scanned_at) {
    document.getElementById('txt-meta').textContent = 'Belum scan';
    return;
  }
  const elapsed = Math.max(0, Math.round((Date.now() - currentData.scanned_at) / 1000));
  document.getElementById('txt-meta').textContent = `${elapsed}s lalu · ${currentData.n || 0} pairs`;

  if (currentData.next_scan_at) {
    const remainMs = currentData.next_scan_at - Date.now();
    if (remainMs > 0) {
      const sec = Math.floor((remainMs / 1000) % 60);
      const min = Math.floor((remainMs / 1000) / 60);
      document.getElementById('txt-timer').textContent = `Next scan: ${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    } else {
      document.getElementById('txt-timer').textContent = currentData.scanning ? 'Memindai...' : 'Segera scan';
    }
  }
}

async function load() {
  try {
    const res = await fetch('/api/state');
    const data = await res.json();
    paint(data);
  } catch (err) {
    console.error('Fetch error:', err);
  }
}

// Event Listeners for Standard Tabs
document.getElementById('tabs-standard').onclick = e => {
  const btn = e.target.closest('button');
  if (!btn || !btn.dataset.tab) return;
  document.querySelectorAll('#tabs-standard .tab-btn').forEach(b => b.classList.toggle('active', b === btn));
  activeStandardTab = btn.dataset.tab;
  renderCurrentView();
};

// Event Listeners for Microstructure Sub-Filters
document.getElementById('tabs-micro').onclick = e => {
  const btn = e.target.closest('button');
  if (!btn || !btn.dataset.filter) return;
  document.querySelectorAll('#tabs-micro .tab-btn').forEach(b => b.classList.toggle('active', b === btn));
  activeMicroFilter = btn.dataset.filter;
  renderCurrentView();
};


document.getElementById('btn-apply').onclick = async () => {
  const btn = document.getElementById('btn-apply');
  btn.textContent = 'Menyimpan...';
  const body = {
    min_liq: Number(document.getElementById('f-liq').value),
    min_mcap: Number(document.getElementById('f-mcap').value),
    min_vl: Number(document.getElementById('f-vl').value),
    position: Number(document.getElementById('f-pos').value),
    max_5m: Number(document.getElementById('f-5m').value),
    max_1h: Number(document.getElementById('f-1h').value),
    max_top10: Number(document.getElementById('f-top10').value),
    max_er: Number(document.getElementById('f-er').value),
    max_dev_hold: Number(document.getElementById('f-dev').value),
    max_insider: Number(document.getElementById('f-insider').value),
    chain_mode: activeChainFilter,
    interval: 300,
  };
  try {
    await fetch('/api/filters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await fetch('/api/scan', { method: 'POST' });
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, 600));
      const res = await fetch('/api/state');
      const data = await res.json();
      paint(data);
      if (!data.scanning) break;
    }
  } catch (err) {
    console.error('Apply error:', err);
    document.getElementById('tg-modal-enabled')?.addEventListener('change', function() {
  const tag = document.getElementById('tg-modal-status-text');
  if (tag) {
    tag.textContent = this.checked ? 'LIVE' : 'OFF';
    tag.className = 'tg-status-tag ' + (this.checked ? 'on' : 'off');
  }
});

load();
  } finally {
    btn.textContent = 'Terapkan';
  }
};

document.getElementById('btn-apply-micro').onclick = async () => {
  const btn = document.getElementById('btn-apply-micro');
  btn.textContent = 'Menyimpan...';
  const body = {
    min_absorb_score: Number(document.getElementById('f-min-score').value),
    min_absorb_mcap: Number(document.getElementById('f-mcap-micro').value),
    min_holder: Number(document.getElementById('f-holder-micro').value),
    position: Number(document.getElementById('f-pos-micro').value),
    chain_mode: activeChainFilter,
    interval: 300,
  };
  try {
    await fetch('/api/filters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await fetch('/api/scan', { method: 'POST' });
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, 600));
      const res = await fetch('/api/state');
      const data = await res.json();
      paint(data);
      if (!data.scanning) break;
    }
  } catch (err) {
    console.error('Apply micro error:', err);
    document.getElementById('tg-modal-enabled')?.addEventListener('change', function() {
  const tag = document.getElementById('tg-modal-status-text');
  if (tag) {
    tag.textContent = this.checked ? 'LIVE' : 'OFF';
    tag.className = 'tg-status-tag ' + (this.checked ? 'on' : 'off');
  }
});

load();
  } finally {
    btn.textContent = 'Terapkan Tuning';
  }
};

document.getElementById('f-mcap-micro').addEventListener('input', renderCurrentView);
document.getElementById('f-min-score').addEventListener('input', renderCurrentView);
document.getElementById('f-holder-micro').addEventListener('input', renderCurrentView);


document.getElementById('btn-scan').onclick = async () => {
  const btn = document.getElementById('btn-scan');
  btn.disabled = true;
  btn.textContent = 'Memindai...';
  try {
    await fetch('/api/scan', { method: 'POST' });
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, 600));
      const res = await fetch('/api/state');
      const data = await res.json();
      paint(data);
      if (!data.scanning) break;
    }
  } catch (err) {
    console.error('Scan error:', err);
    document.getElementById('tg-modal-enabled')?.addEventListener('change', function() {
  const tag = document.getElementById('tg-modal-status-text');
  if (tag) {
    tag.textContent = this.checked ? 'LIVE' : 'OFF';
    tag.className = 'tg-status-tag ' + (this.checked ? 'on' : 'off');
  }
});

load();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan Sekarang';
  }
};

document.getElementById('tg-modal-enabled')?.addEventListener('change', function() {
  const tag = document.getElementById('tg-modal-status-text');
  if (tag) {
    tag.textContent = this.checked ? 'LIVE' : 'OFF';
    tag.className = 'tg-status-tag ' + (this.checked ? 'on' : 'off');
  }
});

load();
setInterval(load, 7000);
setInterval(updateTimeDisplay, 1000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(time.strftime("%H:%M:%S"), args[-1] if args else fmt, flush=True)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, HTML.encode(), "text/html; charset=utf-8")
            return
        if self.path in ("/favicon.ico", "/chop_icon.png"):
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🪓</text></svg>'
            self._send(200, svg.encode("utf-8"), "image/svg+xml")
            return
        if self.path == "/api/state":
            with lock:
                payload = {
                    "scanning": state["scanning"],
                    "error": state["error"],
                    "scanned_at": state["scanned_at"],
                    "next_scan_at": state.get("next_scan_at"),
                    "n": state["n"],
                    "rows": state["rows"],
                    "log": state["log"],
                    "filters": state["filters"],
                }
            self._send(200, json.dumps(payload, separators=(",", ":")).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/filters":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode() if n else "{}")
            except json.JSONDecodeError:
                self._send(400, b'{"error":"json"}', "application/json")
                return
            merged = dict(DEFAULT_FILTERS)
            with lock:
                merged.update(state.get("filters") or {})
            if isinstance(body, dict):
                for key, default in DEFAULT_FILTERS.items():
                    if key not in body:
                        continue
                    merged[key] = type(default)(body[key])
            merged["interval"] = 300
            with lock:
                state["filters"] = merged
            save_filters(merged)
            self._send(200, json.dumps(merged, separators=(",", ":")).encode(), "application/json")
            return
        if self.path == "/api/telegram/test":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req_data = json.loads(self.rfile.read(n).decode() if n else "{}")
            except json.JSONDecodeError:
                self._send(400, b'{"error":"json"}', "application/json")
                return
            tk = req_data.get("token") or ""
            cid = req_data.get("chat_id") or ""
            if not tk or not cid:
                with lock:
                    f = state.get("filters") or {}
                    tk = tk or f.get("telegram_token", "")
                    cid = cid or f.get("telegram_chat_id", "")
            try:
                from telegram_bot import send_test_alert
                ok, err = send_test_alert(tk, cid)
                self._send(200, json.dumps({"ok": ok, "error": err}).encode(), "application/json")
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")
            return
        if self.path == "/api/telegram/config":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode() if n else "{}")
            except json.JSONDecodeError:
                self._send(400, b'{"error":"json"}', "application/json")
                return
            with lock:
                f = dict(state.get("filters") or DEFAULT_FILTERS)
                if "telegram_token" in body:
                    f["telegram_token"] = str(body["telegram_token"]).strip()
                if "telegram_chat_id" in body:
                    f["telegram_chat_id"] = str(body["telegram_chat_id"]).strip()
                if "telegram_enabled" in body:
                    f["telegram_enabled"] = bool(body["telegram_enabled"])
                state["filters"] = f
            save_filters(f)
            self._send(200, json.dumps({"ok": True, "filters": f}).encode(), "application/json")
            return
        if self.path != "/api/scan":
            self._send(404, b"not found", "text/plain")
            return
        threading.Thread(target=scan, daemon=True).start()
        self._send(200, b'{"ok":true}', "application/json")


def main() -> None:
    with lock:
        state["log"] = prune_log(load_log(), int(time.time() * 1000))
        state["filters"] = load_filters()
    print("=" * 68)
    print("🚀 Chop Radar Mobile / VPS Terminal — Solana Meteora DLMM Edition")
    print("📡 Local access   : http://127.0.0.1:%s" % PORT)
    print("📱 Mobile / VPS    : http://0.0.0.0:%s (buka via IP VPS dari HP)" % PORT)
    print("🟣 Network Source  : Meteora DLMM Data API (Zero Rate Limits / Anti-429)")
    print("⏱️ Auto-scan       : Tiap 5 menit · Ctrl+C untuk stop")
    print("=" * 68 + "\n")
    threading.Thread(target=loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard sol hp...")
if __name__ == "__main__":
    main()

