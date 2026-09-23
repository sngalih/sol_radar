#!/usr/bin/env python3
"""Chop & Absorption State Machine LP Terminal — Unified Multi-Chain & Second Wave (V6).
Robinhood + Solana + Arc Multi-Chain Radar + Second Wave Hunter Spot Panel.
python "dashboard v6.py" → http://127.0.0.1:8772
"""

from __future__ import annotations

import json
import http.cookiejar
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))

GMGN_KEY = "gmgn_2d9f7ba605cabf03adeb3276e42427d4"
HOST = "127.0.0.1"
PORT = 8772
INTERVAL_SEC = 300
POSITION_USD = 100
FEE_TIER = 0.01

LOG_PATH = Path(__file__).resolve().parent / "v6-log.json"
ICON_PATH = Path(__file__).resolve().parent / "chop_icon.png"
FILTER_PATH = Path(__file__).resolve().parent / "v6-filters.json"

LOG_MAX = 200
LOG_GAP_MS = 15 * 60_000
LOG_KEEP_MS = 24 * 60 * 60_000

DEFAULT_FILTERS = {
    "chain_mode": "BOTH",       # "RH", "SOL", "ARC", "BOTH"
    "min_liq": 30000,
    "min_mcap": 0,
    "min_vl": 3,
    "max_5m": 12,
    "max_1h": 80,
    "max_top10": 45,
    "max_er": 20,
    "max_dev_hold": 20,
    "max_insider": 10,
    "min_holder": 150,          # min 150 holder untuk safety distribusi
    "position": 100,
    "interval": 300,
    "min_absorb_score": 65,
    "min_absorb_mcap": 500000,
    "min_fee_absorb": 0.50,     # Opsi B: min fee/h untuk Absorption Radar ($0.50/h)
    "filter_stocks": True,      # Opsi A: filter saham sintetis Robinhood (META, NVDA, dll.)
    "telegram_token": "",
    "telegram_chat_id": "",
    "telegram_enabled": False,
    "sw_min_ath": 200000,
    "sw_max_mcap": 60000,
    "sw_min_liq": 10000,
    "sw_max_age": 24,
    "sw_min_buy": 52,
}

lock = threading.Lock()
state: dict = {
    "scanning": False,
    "error": None,
    "scanned_at": None,
    "next_scan_at": None,
    "n": 0,
    "rows": [],
    "sw_rows": [],
    "log": [],
    "fees": {},
    "filters": dict(DEFAULT_FILTERS),
    "prev_micro_states": {},          # addr -> state string, for alert tracking
    "prev_fee_decay": set(),          # addrs already alerted for fee decay
}


def num(row: dict, *keys: str) -> float:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def usd_fmt(n: float) -> str:
    if not n:
        return "0"
    if n >= 1e6:
        return f"{n/1e6:.2f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}k"
    return f"{n:,.0f}"


def calculate_lp_range(price: float, p5: float, p1: float, liq: float) -> dict:
    """Auto LP Range Advisor: hitung range optimal berdasarkan volatilitas & liquidity depth.
    Rumus: buffer = max(15%, min(40%, |1h%| * 2.5 + |5m%| * 1.5)) × liquidity_multiplier
    """
    if price <= 0:
        return {"range_pct": 0.0, "lower": 0.0, "upper": 0.0, "note": "--"}
    vol_1h = abs(p1)
    vol_5m = abs(p5)
    # Base: 2.5x range 1h + 1.5x noise 5m, clamp antara 15-40%
    base_pct = max(15.0, min(40.0, vol_1h * 2.5 + vol_5m * 1.5))
    # Liquidity depth correction: pool dalam = lebih sempit lebih aman
    if liq >= 200_000:
        mult = 0.85   # Pool sangat dalam, range bisa lebih kompak
    elif liq >= 100_000:
        mult = 1.0
    elif liq >= 50_000:
        mult = 1.15   # Pool sedang, sedikit lebih lebar untuk buffer
    else:
        mult = 1.35   # Pool dangkal, lebarkan untuk safety
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


def _trigger_telegram(alert_type: str, token_data: dict) -> None:
    """Fire-and-forget Telegram alert dalam background thread."""
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
    for fp in [FILTER_PATH, Path(__file__).resolve().parent / "v4-filters.json"]:
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
        r = dict(r)
        r["hits"] = hits[-16:]
        r["at"] = hits[-1]
        out.append(r)
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:LOG_MAX]


def record_hits(live: list[dict]) -> list[dict]:
    now = int(time.time() * 1000)
    with lock:
        log = prune_log(list(state["log"] or load_log()), now)
    by = {r["address"]: r for r in log}
    for t in live:
        if t["tag"] != "CHOP":
            continue
        row = by.get(t["address"])
        if not row:
            row = {
                "address": t["address"],
                "symbol": t["symbol"],
                "chain": t.get("chain", "RH"),
                "tag": t["tag"],
                "price": t["price"],
                "at": now,
                "hits": [now],
                "gmgn": t["gmgn"],
            }
            by[t["address"]] = row
            log.insert(0, row)
            continue
        last = row["hits"][-1] if row.get("hits") else row.get("at", 0)
        if now - last < LOG_GAP_MS:
            continue
        row["hits"].append(now)
        row["at"] = now
        row["tag"] = t["tag"]
        row["price"] = t["price"]
        row["symbol"] = t["symbol"]
        row["chain"] = t.get("chain", row.get("chain", "RH"))
    log = prune_log(log, now)
    save_log(log)
    return log


def gmgn_rank(chain: str = "robinhood", limit: int = 40) -> list[dict]:
    qs = urllib.parse.urlencode(
        {
            "chain": chain,
            "interval": "1h",
            "limit": str(limit),
            "order_by": "volume",
            "direction": "desc",
            "timestamp": str(int(time.time())),
            "client_id": str(uuid.uuid4()),
        }
    )
    req = urllib.request.Request(
        "https://openapi.gmgn.ai/v1/market/rank?" + qs,
        headers={
            "X-APIKEY": GMGN_KEY,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
        },
    )
    data = None
    for attempt in range(3):
        try:
            with OPENER.open(req, timeout=25) as res:
                data = json.loads(res.read().decode())
            break
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                backoff = 4.0 * (attempt + 1)
                print(f"[GMGN] 429 Rate Limit ({chain}) — jeda {backoff}s lalu retry (percobaan {attempt + 1}/2)...")
                time.sleep(backoff)
                continue
            raise

    rows: object = data
    if isinstance(rows, dict):
        d1 = rows.get("data") or rows
        if isinstance(d1, dict):
            d2 = d1.get("data") or d1
            if isinstance(d2, dict):
                rows = d2.get("rank") or []
            elif isinstance(d2, list):
                rows = d2
            else:
                rows = []
    if not isinstance(rows, list):
        return []
    out = []
    chain_tag = "SOL" if chain == "sol" else "ARC" if chain == "arc" else "RH"
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
            item["chain"] = chain_tag
            out.append(item)
    return out


def calculate_microstructure(row: dict, f: dict, vl: float, er: float, p5: float, p1: float, liq: float, mcap: float = 0.0) -> dict:
    """Mage Microstructure Note Engine:
    Menghitung Absorption Score (0-100), Price Response, Order Flow,
    dan State Machine (ABSORPTION, EXPANSION, DISTRIBUTION).
    """
    buys = int(num(row, "buys"))
    sells = int(num(row, "sells"))
    total_tx = buys + sells
    buy_ratio = round((buys / total_tx * 100), 1) if total_tx > 0 else 50.0

    top10_rate = round(num(row, "top_10_holder_rate") * 100, 1)
    dev_team_hold = round(num(row, "dev_team_hold_rate") * 100, 1)
    insider_rate = round(num(row, "rat_trader_amount_rate") * 100, 1)
    is_wash = bool(row.get("is_wash_trading"))
    is_honeypot = bool(row.get("is_honeypot"))
    smart_degens = int(num(row, "smart_degen_count"))

    # 1. Turnover & Volume Weight (Max 30 pts)
    # Di Robinhood, high V/L adalah syarat mutlak fee deras
    if vl >= 10.0:
        pts_vl = 30.0
    elif vl >= 5.0:
        pts_vl = 26.0
    elif vl >= 3.0:
        pts_vl = 21.0
    elif vl >= 1.5:
        pts_vl = 14.0
    else:
        pts_vl = max(0.0, vl * 8.0)

    # 2. Price Response / ER (Max 25 pts)
    # Price Response = |p1| / vl. Semakin kecil ER, semakin padat penyerapan
    if er <= 2.5:
        pts_er = 25.0
    elif er <= 5.0:
        pts_er = 22.0
    elif er <= 10.0:
        pts_er = 17.0
    elif er <= 20.0:
        pts_er = 10.0
    else:
        pts_er = max(0.0, 10.0 - (er - 20) * 0.5)

    # 3. Volatility Compression / Symmetrical Tightness (Max 20 pts)
    # Mengukur seberapa sempit pergerakan 5m dan 1h
    a5 = abs(p5)
    a1 = abs(p1)
    if a5 <= 4.0 and a1 <= 12.0:
        pts_vol = 20.0
    elif a5 <= 8.0 and a1 <= 25.0:
        pts_vol = 16.0
    elif a5 <= 12.0 and a1 <= 50.0:
        pts_vol = 10.0
    else:
        pts_vol = 4.0

    # 4. Order Flow Absorption Balance (Max 15 pts)
    # Di fase absorption, buy & sell saling bertarung sengit (45% - 62% buy)
    if 46.0 <= buy_ratio <= 60.0:
        pts_flow = 15.0  # Perfect absorption balance
    elif 60.0 < buy_ratio <= 72.0:
        pts_flow = 12.0  # Moderate accumulation
    elif 38.0 <= buy_ratio < 46.0:
        pts_flow = 8.0   # Mild seller pressure but being tested
    else:
        pts_flow = 3.0   # Extreme imbalance

    # 5. On-Chain Security & Smart Wallets (Max 10 pts)
    pts_safety = 0.0
    if not is_wash and not is_honeypot and top10_rate <= 45.0:
        pts_safety += 5.0
        if dev_team_hold <= 20.0 and insider_rate <= 10.0:
            pts_safety += 3.0
        if smart_degens > 0:
            pts_safety += 2.0

    total_score = round(min(100.0, max(0.0, pts_vl + pts_er + pts_vol + pts_flow + pts_safety)), 1)

    min_vl = float(f.get("min_vl") or 3)
    max_5m = float(f.get("max_5m") or 12)
    max_1h = float(f.get("max_1h") or 80)
    min_liq = float(f.get("min_liq") or 30000)
    min_absorb_mcap = float(f.get("min_absorb_mcap") or 500000)

    # --- STATE MACHINE CLASSIFICATION ---
    if is_wash or is_honeypot or top10_rate > 60.0 or insider_rate > 25.0:
        state = "DISTRIBUTION"
        state_title = "DISTRIBUTION / TOXIC"
        action = "🛑 DILARANG LP (Risiko On-Chain / Rug)"
        action_type = "DANGER"
        clue = "Gagal filter keamanan on-chain (Wash trading / Honeypot / Whale dominan)"
    elif p1 > max_1h or (p5 > max_5m and buy_ratio >= 65.0):
        state = "EXPANSION"
        state_title = "EXPANSION / RUNNER"
        action = "🟡 HINDARI LP (Risiko Out-of-Range Naik)"
        action_type = "WARN"
        clue = f"Koin sedang pump vertikal (1h {p1:+.1f}%, 5m {p5:+.1f}%). LP rentan out-of-range"
    elif p1 < -25.0 or (p5 < -10.0 and buy_ratio < 40.0):
        state = "DISTRIBUTION"
        state_title = "DISTRIBUTION / DUMP"
        action = "🔴 HINDARI LP (Toxic Inventory / Dump)"
        action_type = "DANGER"
        clue = f"Seller mengguyur likuiditas (1h {p1:+.1f}%, Buy {buy_ratio:.0f}%). Bahaya IL parah"
    elif total_score >= 70.0 and vl >= min_vl and abs(p5) <= max_5m and abs(p1) <= max_1h and liq >= min_liq:
        if min_absorb_mcap > 0 and mcap < min_absorb_mcap:
            state = "REACCUMULATION"
            state_title = "LOW MCAP ABSORPTION"
            action = "⏳ PANTAU (Mcap < $500k)"
            action_type = "WAIT"
            clue = f"Penyerapan bagus (Score {total_score:.0f}), tapi Mcap ${usd_fmt(mcap)} masih di bawah minimum (${usd_fmt(min_absorb_mcap)})"
        else:
            state = "ABSORPTION"
            state_title = "ABSORPTION (PRIME LP)"
            action = "🚀 ENTRY LP (Zona Panen Fee Emas)"
            action_type = "PRIME"
            clue = f"Penyerapan sempurna: V/L {vl:.1f}x tertahan di range sempit ({p1:+.1f}% 1h), order flow seimbang"
    elif total_score >= 50.0 and vl >= 2.0 and abs(p5) <= max_5m * 1.3:
        state = "REACCUMULATION"
        state_title = "UGLY REACCUMULATION"
        action = "🟢 LAYAK LP (Sideways Terjaga)"
        action_type = "VALID"
        clue = f"Aktivitas sideways terbentuk ({vl:.1f}x V/L), seller mulai kehabisan amunisi"
    else:
        state = "NEUTRAL"
        state_title = "NEUTRAL / CHOP BASE"
        action = "⏳ PANTAU (Tunggu Spike Volume)"
        action_type = "WAIT"
        clue = f"Aktivitas perputaran belum cukup agresif untuk yield LP tinggi (V/L {vl:.1f}x)"

    return {
        "score": total_score,
        "state": state,
        "state_title": state_title,
        "action": action,
        "action_type": action_type,
        "clue": clue,
        "price_response": er,
        "buy_ratio": buy_ratio,
        "total_tx": total_tx,
    }


def score(row: dict, f: dict) -> dict:
    price = num(row, "price")
    liq = num(row, "liquidity")
    vol = num(row, "volume")
    p5 = num(row, "price_change_percent5m")
    p1 = num(row, "price_change_percent1h")
    mcap = num(row, "market_cap", "marketcap")
    vl = (vol / liq) if liq else 0.0
    addr = str(row.get("address") or "")
    chain = str(row.get("chain") or "RH").upper()

    # Safety Metrics from GMGN API
    top10_rate = round(num(row, "top_10_holder_rate") * 100, 1)
    holders = int(num(row, "holder_count"))
    snipers = int(num(row, "sniper_count"))
    sniper_rate = round(num(row, "top70_sniper_hold_rate") * 100, 1)
    insider_rate = round(num(row, "rat_trader_amount_rate") * 100, 1)
    smart_degens = int(num(row, "smart_degen_count"))
    is_wash = bool(row.get("is_wash_trading"))
    is_honeypot = bool(row.get("is_honeypot"))
    is_renounced = bool(row.get("is_renounced"))
    dev_burn_ratio = round(num(row, "dev_token_burn_ratio") * 100, 1)
    dev_team_hold = round(num(row, "dev_team_hold_rate") * 100, 1)

    ath_mc = num(row, "history_highest_market_cap", "ath_market_cap")
    if not ath_mc and mcap:
        ath_mc = mcap
    drop_ath = round(((mcap - ath_mc) / ath_mc * 100), 1) if (ath_mc and ath_mc > 0) else 0.0

    open_ts = num(row, "open_timestamp", "creation_timestamp")
    age_hours = round((time.time() - open_ts) / 3600.0, 1) if open_ts > 0 else 9999.0

    buys = int(num(row, "buys"))
    sells = int(num(row, "sells"))
    total_tx = buys + sells
    buy_ratio = round((buys / total_tx * 100), 1) if total_tx > 0 else 50.0

    twitter = str(row.get("twitter_username") or "")
    website = str(row.get("website") or "")

    min_liq = float(f.get("min_liq") or 30000)
    min_vl = float(f.get("min_vl") or 3)
    min_mcap = float(f.get("min_mcap") or 0)
    max_5m = float(f.get("max_5m") or 12)
    max_1h = float(f.get("max_1h") or 80)
    max_top10 = float(f.get("max_top10") or 45)
    max_er = float(f.get("max_er") or 20)
    max_dev_hold = float(f.get("max_dev_hold") or 20)
    max_insider = float(f.get("max_insider") or 10)
    pos = float(f.get("position") or 100)

    # Efficiency Ratio: ER = |1h%| / V/L — semakin KECIL = semakin choppy (ideal LP)
    er = round(abs(p1) / vl, 2) if vl > 0 else 999.0

    # 1. HARD FILTERS (Penentu Masuk CHOP vs SKIP - Core Baseline)
    gaps: list[str] = []
    if liq < min_liq:
        gaps.append(f"Liq ${liq:,.0f} < ${min_liq:,.0f}")
    if min_mcap > 0 and mcap < min_mcap:
        gaps.append(f"Mcap ${usd_fmt(mcap)} < ${usd_fmt(min_mcap)}")
    if vl < min_vl:
        gaps.append(f"V/L {vl:.1f}x < {min_vl:g}x")
    if abs(p5) > max_5m:
        gaps.append(f"5m {p5:+.1f}% vertikal")
    if p1 >= max_1h:
        gaps.append(f"1h {p1:+.1f}% pump")
    if is_wash:
        gaps.append("Wash Trading terdeteksi")
    if is_honeypot:
        gaps.append("Honeypot terdeteksi")

    # 2. SOFT ADVISORY BADGES (Panduan Keputusan Manual)
    advisories: list[dict] = []
    if p1 < 0 and p5 < -4.0:
        advisories.append({"level": "danger", "label": "⚠️ Drill Down", "title": f"Peringatan: 5m ({p5:+.1f}%) dan 1h ({p1:+.1f}%) meluncur turun bersamaan"})
    elif p1 > 0 and p5 > 3.0:
        advisories.append({"level": "warn", "label": "↗️ Up Momentum", "title": f"5m ({p5:+.1f}%) dan 1h ({p1:+.1f}%) sedang mendaki"})
    else:
        advisories.append({"level": "ok", "label": "🌊 Chop Base", "title": "Pergerakan harga relatif ping-pong / sideways"})

    if drop_ath < -70.0:
        advisories.append({"level": "warn", "label": f"📉 ATH {drop_ath:.0f}%", "title": f"Penurunan dalam dari ATH (${usd_fmt(ath_mc)}): {drop_ath:.1f}%"})
    elif drop_ath >= -45.0 and drop_ath < 0:
        advisories.append({"level": "ok", "label": f"🟢 ATH {drop_ath:.0f}%", "title": f"Pullback sehat dari ATH (${usd_fmt(ath_mc)}): {drop_ath:.1f}%"})
    elif drop_ath >= 0:
        advisories.append({"level": "warn", "label": "🔥 New ATH", "title": "Harga sedang di puncak ATH"})
    else:
        advisories.append({"level": "muted", "label": f"ATH {drop_ath:.0f}%", "title": f"Jarak dari ATH: {drop_ath:.1f}%"})

    if total_tx >= 15:
        if buy_ratio < 45.0:
            advisories.append({"level": "warn", "label": f"🔴 Buy {buy_ratio:.0f}%", "title": f"Seller dominan ({sells} Sells vs {buys} Buys)"})
        elif buy_ratio >= 52.0:
            advisories.append({"level": "ok", "label": f"🟢 Buy {buy_ratio:.0f}%", "title": f"Buyer aktif ({buys} Buys vs {sells} Sells)"})
        else:
            advisories.append({"level": "muted", "label": f"⚖️ Buy {buy_ratio:.0f}%", "title": f"Order flow seimbang ({buys} B / {sells} S)"})

    min_holder = int(f.get("min_holder") or 150)

    tag = "CHOP" if not gaps else "SKIP"

    share = (pos / liq) if liq > 0 else 0
    fee_hour = vol * FEE_TIER * share
    fee_24h = round(fee_hour * 24, 4)
    fee_7d = round(fee_hour * 24 * 7, 4)
    breakeven_hours = round(pos / fee_hour, 1) if fee_hour > 0 else 9999.0

    # LP Range Auto Advisor
    lp_range = calculate_lp_range(price, p5, p1, liq)

    # Holder advisory badge
    if holders < 100:
        advisories.append({"level": "danger", "label": f"⚠️ Holder {holders}", "title": f"Holder sangat sedikit ({holders}), distribusi tidak aman"})
    elif holders < min_holder:
        advisories.append({"level": "warn", "label": f"👥 Holder {holders}", "title": f"Holder ({holders}) di bawah minimum aman ({min_holder}). Watch out whale exit."})
    else:
        advisories.append({"level": "ok", "label": f"✅ Holder {holders}", "title": f"Distribusi holder cukup sehat ({holders} holder)"})

    # 3. Dedicated Microstructure Analysis
    micro = calculate_microstructure(row, f, vl, er, p5, p1, liq, mcap)
    micro["lp_range"] = lp_range  # Inject LP range into micro dict

    return {
        "symbol": str(row.get("symbol") or "?"),
        "name": str(row.get("name") or ""),
        "chain": chain,
        "price": price,
        "liq": round(liq, 2),
        "vol": round(vol, 2),
        "vl": round(vl, 2),
        "p5": round(p5, 2),
        "p1": round(p1, 2),
        "mcap": round(mcap, 2),
        "ath_mc": round(ath_mc, 2),
        "drop_ath": drop_ath,
        "age_hours": age_hours,
        "buys": buys,
        "sells": sells,
        "buy_ratio": buy_ratio,
        "er": er,
        "advisories": advisories,
        "tag": tag,
        "gaps": gaps,
        "fee_hour": round(fee_hour, 4),
        "fee_24h": fee_24h,
        "fee_7d": fee_7d,
        "breakeven_hours": breakeven_hours,
        "lp_range": lp_range,
        "gmgn": f"https://gmgn.ai/{'sol' if chain == 'SOL' else 'arc' if chain == 'ARC' else 'robinhood'}/token/{addr}",
        "dexscreener": (f"https://dexscreener.com/solana/{addr}" if chain == "SOL"
                        else f"https://dexscreener.com/arc/{addr}" if chain == "ARC"
                        else ""),
        "raydium": "https://raydium.io/clmm/" if chain == "SOL" else "",
        "meteora": "https://app.meteora.ag/dlmm/" if chain == "SOL" else "",
        "fomo": f"https://fomo.family/token/{addr}" if chain == "RH" else "",
        "barker": "https://barkermoney.com/" if chain == "RH" else "",
        "twitter": twitter or f"https://x.com/search?q={urllib.parse.quote(str(row.get('symbol') or ''))}&f=live",
        "website": website,
        "address": addr,
        "top10_rate": top10_rate,
        "holders": holders,
        "snipers": snipers,
        "sniper_rate": sniper_rate,
        "insider_rate": insider_rate,
        "smart_degens": smart_degens,
        "dev_burn_ratio": dev_burn_ratio,
        "dev_team_hold": dev_team_hold,
        "is_wash": is_wash,
        "is_honeypot": is_honeypot,
        "is_renounced": is_renounced,
        "micro": micro,
    }


def score_second_wave(rows: list[dict], f: dict) -> list[dict]:
    """Filter kandidat Second Wave Hunter dari scored rows GMGN.
    Kriteria wajib:
    1. Usia <= 24j
    2. MC $10k - $60k
    3. Liq >= $10k
    4. Vol 24h >= $10k
    5. ATH GMGN >= $200k & current MC <= 35% ATH (pullback >= 65%)
    6. buy_ratio >= 52% atau vol/24 >= $1500
    """
    sw_min_ath = float(f.get("sw_min_ath") or 200000)
    sw_max_mcap = float(f.get("sw_max_mcap") or 60000)
    sw_min_liq = float(f.get("sw_min_liq") or 10000)
    sw_max_age = float(f.get("sw_max_age") or 24)
    sw_min_buy = float(f.get("sw_min_buy") or 52)

    candidates: list[dict] = []
    for t in rows:
        age_h = float(t.get("age_hours") or 9999.0)
        mcap = float(t.get("mcap") or 0.0)
        liq = float(t.get("liq") or 0.0)
        vol = float(t.get("vol") or 0.0)
        ath_mc = float(t.get("ath_mc") or 0.0)
        buy_ratio = float(t.get("buy_ratio") or 50.0)

        # Stage 1: Basic constraints
        if age_h > sw_max_age:
            continue
        if not (10_000 <= mcap <= sw_max_mcap):
            continue
        if liq < sw_min_liq:
            continue
        if vol < 10_000:
            continue

        # Stage 2: ATH & Pullback
        if ath_mc < sw_min_ath:
            continue
        if mcap > ath_mc * 0.35:
            continue

        # Stage 3: Buying pressure
        vol_h1_est = vol / 24.0
        if buy_ratio < sw_min_buy and vol_h1_est < 1500.0:
            continue

        pullback = round((1 - mcap / ath_mc) * 100, 1) if ath_mc > 0 else 0.0
        item = dict(t)
        item["sw_pullback"] = pullback
        item["sw_vol_h1"] = vol_h1_est
        candidates.append(item)

    candidates.sort(key=lambda x: -x["ath_mc"])
    return candidates


def get_next_boundary(interval_sec: int = INTERVAL_SEC, offset_sec: int = 1) -> float:
    now = time.time()
    next_slot = ((int(now) // interval_sec) + 1) * interval_sec
    return float(next_slot + offset_sec)


# Daftar ticker saham AS / ETF di Robinhood Chain (Tokenized Equity)
_STOCK_TICKERS: frozenset = frozenset({
    "META", "NVDA", "GOOGL", "GOOG", "SPY", "SPCX", "MU", "MSTR",
    "GLD", "HIMS", "AAPL", "GME", "AMC", "TSLA", "MSFT", "AMZN",
    "QQQ", "COIN", "AMD", "NFLX", "PLTR", "BABA", "DIS", "INTC",
    "BRK", "JPM", "V", "WMT", "SLV", "USO", "TLT", "IWM", "VIX",
    "NVDL", "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SOXL", "BITX",
    "HOOD", "RBLX", "SNAP", "LYFT", "UBER", "ABNB", "RIVN", "F",
})
_STOCK_NAME_MARKERS: tuple = (
    "• robinhood token", "robinhood token",
    "common stock", "etf trust",
    "class a common stock", "class b common stock",
)


def _is_stock_row(row: dict) -> bool:
    """Deteksi apakah scored row adalah tokenized equity/ETF yang tidak relevan untuk LP meme."""
    name = str(row.get("name") or "").lower()
    sym = str(row.get("symbol") or "").upper()
    chain = str(row.get("chain") or "").upper()
    if any(m in name for m in _STOCK_NAME_MARKERS):
        return True
    if chain == "RH" and sym in _STOCK_TICKERS:
        return True
    return False


def scan() -> None:
    with lock:
        if state["scanning"]:
            return
        state["scanning"] = True
        state["error"] = None
        f = dict(state.get("filters") or DEFAULT_FILTERS)
    try:
        chain_mode = str(f.get("chain_mode", "BOTH")).upper()
        raw_items: list[dict] = []
        if chain_mode == "RH":
            raw_items = gmgn_rank("robinhood", 50)
        elif chain_mode == "SOL":
            raw_items = gmgn_rank("sol", 50)
        elif chain_mode == "ARC":
            raw_items = gmgn_rank("arc", 50)
        else:  # BOTH — 3 chain, 50 pairs masing-masing (dengan jeda 3.5s & fallback)
            raw_items = []
            for ch in ["robinhood", "sol", "arc"]:
                try:
                    items = gmgn_rank(ch, 50)
                    raw_items.extend(items)
                except urllib.error.HTTPError as e:
                    if e.code == 429 and raw_items:
                        print(f"[GMGN] ⚠️ Chain {ch} dilewati sementara karena kuota rate limit 429 (tetap tampilkan {len(raw_items)} pairs)")
                    else:
                        raise
                time.sleep(3.5)

        rows = [score(r, f) for r in raw_items]

        # Opsi A: Filter tokenized stocks/ETF (META, NVDA, GOOGL, dll.) dari semua view
        do_filter_stocks = bool(f.get("filter_stocks", True))
        if do_filter_stocks:
            rows = [r for r in rows if not _is_stock_row(r)]

        sw_rows = score_second_wave(rows, f)

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

        # --- TELEGRAM ALERT TRIGGERS ---
        new_states: dict = {}
        new_decay: set = set()
        alert_threads: list = []

        rows_by_addr = {t["address"]: t for t in rows}

        for t in rows:
            addr = t["address"]
            micro = t.get("micro", {})
            cur_state = micro.get("state", "NEUTRAL")
            prev_state = prev_states.get(addr, "")
            new_states[addr] = cur_state

            # Alert 1: Baru masuk ABSORPTION dari state lain
            if cur_state == "ABSORPTION" and prev_state != "ABSORPTION":
                th = threading.Thread(target=_trigger_telegram, args=("absorption", t), daemon=True)
                th.start()
                alert_threads.append(th)

            # Alert 2: Fee decay (hanya 1x per token sampai recovery)
            if t.get("fee_decay") and addr not in prev_decay:
                new_decay.add(addr)
                th = threading.Thread(target=_trigger_telegram, args=("fee_decay", t), daemon=True)
                th.start()
                alert_threads.append(th)
            elif not t.get("fee_decay"):
                pass  # Recovery: akan dihapus dari decay set
            else:
                new_decay.add(addr)  # masih decay, pertahankan

            # Alert 3: Emergency exit — masuk DISTRIBUTION dari state non-DISTRIBUTION
            if cur_state == "DISTRIBUTION" and prev_state not in ("DISTRIBUTION", "", "NEUTRAL"):
                th = threading.Thread(target=_trigger_telegram, args=("emergency", t), daemon=True)
                th.start()
                alert_threads.append(th)

        with lock:
            state["rows"] = rows
            state["sw_rows"] = sw_rows
            state["log"] = log
            state["n"] = len(rows)
            state["scanned_at"] = now_ms
            state["next_scan_at"] = int(next_boundary_ts * 1000)
            state["prev_micro_states"] = new_states
            state["prev_fee_decay"] = new_decay
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:240]
        with lock:
            if err.code == 429:
                state["error"] = "GMGN Rate Limit (429) — Tunggu 30 detik lalu scan lagi"
            else:
                state["error"] = f"GMGN HTTP {err.code}: {body}"
    except Exception as err:
        with lock:
            state["error"] = str(err)
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
<title>Chop Radar V5 — Unified Multi-Chain LP Terminal (Port 8769)</title>
<link rel="icon" type="image/png" href="/chop_icon.png"/>
<link rel="apple-touch-icon" href="/chop_icon.png"/>
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
.mode-btn.active.mode-secondwave {
  background: linear-gradient(135deg, rgba(249, 115, 22, 0.3) 0%, rgba(234, 179, 8, 0.25) 100%);
  border-color: rgba(249, 115, 22, 0.5);
  color: #fed7aa;
}
.sw-badge {
  display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px;
  font-weight: 700; font-family: 'JetBrains Mono', monospace;
}
.sw-pullback-deep {
  background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);
}
.sw-pullback-mid {
  background: rgba(251, 146, 60, 0.15); color: #fb923c; border: 1px solid rgba(251, 146, 60, 0.3);
}
.sw-buy-strong {
  background: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.3);
}
.sw-buy-normal {
  background: rgba(250, 204, 21, 0.15); color: #facc15; border: 1px solid rgba(250, 204, 21, 0.3);
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

@media (max-width: 1150px) {
  html, body { overflow: auto; height: auto; }
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .control-bar { flex-direction: column; align-items: stretch; margin: 6px 10px; }
  .workstation { padding: 0 10px 14px; }
}
</style>
</head>
<body>

<header class="top-bar">
  <div class="brand-group">
    <div class="logo-icon"><img src="/chop_icon.png" alt="Chop Icon" onerror="this.outerHTML='🪓'"/></div>
    <div>
      <div class="brand-title">
        CHOP RADAR TERMINAL
        <span class="brand-badge">V6.0 MULTI-CHAIN</span>
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
    <button class="mode-btn mode-secondwave" id="btn-mode-secondwave" onclick="switchMasterMode('SECONDWAVE')">
      <span>🎯</span> Second Wave Hunter (Spot)
    </button>
  </div>

  <!-- Real-Time Chain Switcher -->
  <div class="chain-switch-group">
    <button class="chain-btn active" id="chain-btn-both" type="button" onclick="setChainFilter('BOTH')">🌐 All Chains</button>
    <button class="chain-btn" id="chain-btn-sol" type="button" onclick="setChainFilter('SOL')">🟣 SOL Only</button>
    <button class="chain-btn" id="chain-btn-arc" type="button" onclick="setChainFilter('ARC')">🟠 ARC Only</button>
    <button class="chain-btn" id="chain-btn-rh" type="button" onclick="setChainFilter('RH')">🦅 RH Only</button>
  </div>

  <div class="top-right">
    <div id="err-banner" style="display:none; color:var(--bad); font-size:11.5px; font-weight:600; padding:4px 9px; border-radius:6px; background:var(--bad-soft); border:1px solid rgba(244,63,94,0.3)"></div>
    <div class="status-pill">
      <div class="pulse-dot"></div>
      <span id="txt-timer">Next scan: 05:00</span>
      <span style="color:var(--line-strong)">|</span>
      <span id="txt-meta">Memuat...</span>
    </div>
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
  <div class="tg-bar">
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



<!-- ================= MODE 3: SECOND WAVE HUNTER (PULLBACK RUNNER SPOT RADAR) ================= -->
<div class="subview" id="view-secondwave">
  <div class="kpi-strip">
    <div class="kpi-card">
      <div class="kpi-label">🎯 Kandidat Second Wave</div>
      <div class="kpi-val" id="kpi-sw-count" style="color:#fb923c">0</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Top ATH Runner</div>
      <div class="kpi-val purple" id="kpi-sw-top-ath">—</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Rata-rata Diskon (Pullback)</div>
      <div class="kpi-val" id="kpi-sw-avg-drop" style="color:#38bdf8">—</div>
      <div style="font-size:10px; color:var(--mut-light); font-family:'JetBrains Mono',monospace">Dari Puncak ATH</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Kriteria Strategi</div>
      <div class="kpi-val" style="color:#facc15; font-size:13.5px">ATH ≥ $200k · &lt;24j</div>
      <div style="font-size:10px; color:var(--mut-light); font-family:'JetBrains Mono',monospace">MC $10k–$60k · Buy ≥ 52%</div>
    </div>
  </div>

  <div class="strategy-banner" style="background: linear-gradient(135deg, rgba(249, 115, 22, 0.12) 0%, rgba(234, 179, 8, 0.08) 100%); border-color: rgba(249, 115, 22, 0.25)">
    <div class="strat-text">
      <b>🎯 Second Wave Hunter Edge</b>: 
      <i>"Bukan LP · Spot Trading Runner Strategy · ⚠️ DYOR"</i>.
      Mencari token berumur <b>&lt; 24 jam</b> yang sempat meledak hingga <b>ATH ~$200k+</b>, lalu pullback sehat ke kisaran <b>$10k–$60k (drop ≥ 65%)</b>.
      Ketika likuiditas bertahan tebal (≥ $10k) dan pembeli baru mulai masuk kembali (Buy Ratio ≥ 52% atau Vol H1 naik), beli gelombang kedua (second wave).
    </div>
    <div class="strat-tag" style="background: rgba(249, 115, 22, 0.2); color: #fed7aa; border: 1px solid rgba(249, 115, 22, 0.4)">SECOND WAVE SPOT</div>
  </div>

  <div class="control-bar">
    <div class="tabs-group" id="tabs-sw">
      <button class="tab-btn active" data-sw="ALL">Semua Sinyal SW</button>
      <button class="tab-btn" data-sw="STRONG_BUY" style="color:#34d399">🟢 Strong Buyer (Buy ≥ 60%)</button>
      <button class="tab-btn" data-sw="DEEP_DROP" style="color:#38bdf8">📉 Diskon Dalam (Drop ≥ 75%)</button>
    </div>

    <div class="filter-bar">
      <div class="f-input-group">
        <label>Min ATH $</label>
        <input id="f-sw-ath" style="width:75px" value="200000"/>
      </div>
      <div class="f-input-group">
        <label>Max MC $</label>
        <input id="f-sw-mc" style="width:65px" value="60000"/>
      </div>
      <div class="f-input-group">
        <label>Min Liq $</label>
        <input id="f-sw-liq" style="width:65px" value="10000"/>
      </div>
      <div class="f-input-group">
        <label>Max Usia (j)</label>
        <input id="f-sw-age" style="width:45px" value="24"/>
      </div>
      <div class="f-input-group">
        <label>Min Buy %</label>
        <input id="f-sw-buy" style="width:45px" value="52"/>
      </div>
      <button class="btn-apply" id="btn-apply-sw" type="button" style="background:linear-gradient(135deg,#ea580c,#d97706); border-color:#ea580c">Terapkan SW</button>
    </div>
  </div>

  <main class="workstation" style="padding-top:0">
    <section class="panel" style="flex:1">
      <div class="panel-head">
        <div class="panel-title">
          <span>🎯 Tabel Sinyal Second Wave Hunter (Pullback & Runner Reaccumulation)</span>
          <span style="font-family:'JetBrains Mono',monospace; font-size:11px; padding:1px 6px; border-radius:10px; background:rgba(249,115,22,0.15); color:#fed7aa" id="sw-count">0 Token</span>
        </div>
        <div style="font-size:11px; color:var(--mut);">Spot Trade Sinyal · Refresh Otomatis Tiap 5 Menit · Port 8772</div>
      </div>
      <div class="panel-scroll" id="sw-scroll"></div>
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
let activeSwFilter = 'ALL';
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
  if (t.chain === 'SOL') {
    return `<div class="links-group">
      <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer">GMGN</a>
      <a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer" title="DexScreener Solana">DexS</a>
      <a class="btn-link" href="${t.raydium}" target="_blank" rel="noreferrer" title="Raydium CLMM LP">Raydium</a>
      <a class="btn-link" href="${t.meteora}" target="_blank" rel="noreferrer" title="Meteora DLMM LP">Meteora</a>
      <a class="btn-link" href="${t.twitter}" target="_blank" rel="noreferrer" title="Cek Narasi X">X</a>
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
  document.getElementById('btn-mode-secondwave').classList.toggle('active', mode === 'SECONDWAVE');
  document.getElementById('view-standard').classList.toggle('active-view', mode === 'STANDARD');
  document.getElementById('view-micro').classList.toggle('active-view', mode === 'MICRO');
  document.getElementById('view-secondwave').classList.toggle('active-view', mode === 'SECONDWAVE');
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
          <span style="font-weight:700; color:#fff">${t.symbol}</span>
          <span class="chain-pill ${t.chain === 'SOL' ? 'chain-sol' : t.chain === 'ARC' ? 'chain-arc' : 'chain-rh'}">${t.chain}</span>
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
          <span style="font-weight:700; color:#fff">${t.symbol}</span>
          <span class="chain-pill ${t.chain === 'SOL' ? 'chain-sol' : t.chain === 'ARC' ? 'chain-arc' : 'chain-rh'}">${t.chain}</span>
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
          <span style="font-weight:700; color:#fff">${t.symbol}</span>
          <span class="chain-pill ${t.chain === 'SOL' ? 'chain-sol' : t.chain === 'ARC' ? 'chain-arc' : 'chain-rh'}">${t.chain || 'RH'}</span>
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

function renderSecondWaveTable(rows) {
  if (!rows || !rows.length) {
    return `<div style="padding:60px 20px; text-align:center; background:rgba(255,255,255,0.015); border-radius:12px; border:1px dashed var(--line); margin:20px;">
      <div style="font-size:36px; margin-bottom:12px">🎯</div>
      <div style="font-size:15px; font-weight:700; color:#fff; margin-bottom:6px">Belum Ada Runner Second Wave yang Lolos Kriteria Saat Ini</div>
      <div style="font-size:12px; color:var(--mut); max-width:580px; margin:0 auto; line-height:1.6">
        Kriteria ketat: Usia ≤ 24 jam · Pernah capai ATH ≥ $200k · Saat ini diskon di $10k–$60k (drop ≥ 65%) · Likuiditas ≥ $10k · Pembeli aktif (Buy ≥ 52%).
        <br><br>Sinyal akan otomatis muncul di tabel ini saat runner mengalami pullback sehat.
      </div>
    </div>`;
  }

  let h = `<table><thead><tr>
    <th>Token</th>
    <th class="num">Current MC</th>
    <th class="num" title="All Time High Market Cap yang tercatat di GMGN">Puncak ATH</th>
    <th style="text-align:center" title="Penurunan harga/mcap dari rekor tertinggi (ATH)">📉 Pullback Diskon</th>
    <th class="num">Likuiditas</th>
    <th class="num">Volume (24h / 1h)</th>
    <th style="text-align:center" title="Order Flow: Persentase pembeli vs penjual">🟢 Order Flow</th>
    <th style="text-align:center">⏱️ Usia Token</th>
    <th style="text-align:center">🛡️ On-Chain Risk</th>
    <th style="text-align:right">Riset & Eksekusi</th>
  </tr></thead><tbody>`;

  rows.forEach(t => {
    const pullback = t.sw_pullback || 0;
    const pbClass = pullback >= 75 ? 'sw-pullback-deep' : 'sw-pullback-mid';
    const buyRatio = t.buy_ratio || 50;
    const buyClass = buyRatio >= 60 ? 'sw-buy-strong' : 'sw-buy-normal';

    const top10 = t.top10_rate || 0;
    const devHold = t.dev_team_hold || 0;
    const insider = t.insider_rate || 0;
    const safetyCls = (top10 <= 45 && devHold <= 20 && insider <= 10) ? 'safe-ok' : (top10 <= 60 ? 'safe-warn' : 'safe-bad');

    const ageStr = (t.age_hours !== undefined && t.age_hours < 999) ? `${t.age_hours.toFixed(1)} jam` : '—';
    const vol1h = t.sw_vol_h1 ? `$${usd(t.sw_vol_h1)}/h` : '—';

    h += `<tr>
      <td>
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-weight:800; color:#fff; font-size:13px">${t.symbol}</span>
          <span class="chain-pill ${t.chain === 'SOL' ? 'chain-sol' : t.chain === 'ARC' ? 'chain-arc' : 'chain-rh'}">${t.chain}</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
        <div style="font-size:10.5px; color:var(--mut)">${(t.name || '').slice(0, 18)}</div>
      </td>
      <td class="num">
        <div style="font-weight:700; color:#fff; font-size:12.5px">$${usd(t.mcap)}</div>
        <div style="font-size:10px; color:var(--mut)">Price: $${(t.price || 0).toFixed(6)}</div>
      </td>
      <td class="num">
        <div style="font-weight:800; color:#c084fc; font-size:12.5px">$${usd(t.ath_mc)}</div>
        <div style="font-size:10px; color:var(--mut-light)">ATH Rekor</div>
      </td>
      <td style="text-align:center">
        <span class="sw-badge ${pbClass}">-${pullback.toFixed(0)}% Diskon</span>
      </td>
      <td class="num">
        <div style="font-weight:600; color:#e2e8f0">$${usd(t.liq)}</div>
      </td>
      <td class="num">
        <div style="font-weight:700; color:#38bdf8">$${usd(t.vol)}</div>
        <div style="font-size:10px; color:var(--mut)">Est: ${vol1h}</div>
      </td>
      <td style="text-align:center">
        <span class="sw-badge ${buyClass}">${buyRatio.toFixed(0)}% Buyer</span>
        <div style="font-size:9.5px; color:var(--mut); margin-top:2px">${t.buys || 0}B / ${t.sells || 0}S</div>
      </td>
      <td style="text-align:center">
        <span style="font-family:'JetBrains Mono',monospace; font-size:11.5px; font-weight:700; color:#e2e8f0">${ageStr}</span>
      </td>
      <td style="text-align:center">
        <span class="safe-pill ${safetyCls}" title="Top10: ${top10}% · Dev: ${devHold}% · Insider: ${insider}%">
          Top10: ${top10.toFixed(0)}% · Dev: ${devHold.toFixed(0)}%
        </span>
      </td>
      <td style="text-align:right">
        <div class="links-group" style="justify-content:flex-end">
          <a class="btn-link" href="${t.gmgn}" target="_blank" rel="noreferrer" style="background:#ea580c; border-color:#ea580c; color:#fff">GMGN Chart</a>
          ${t.dexscreener ? `<a class="btn-link" href="${t.dexscreener}" target="_blank" rel="noreferrer">DexS</a>` : ''}
          ${t.fomo ? `<a class="btn-link" href="${t.fomo}" target="_blank" rel="noreferrer">FOMO</a>` : ''}
        </div>
      </td>
    </tr>`;
  });

  h += `</tbody></table>`;
  return h;
}

function renderMicroTable(rows) {
  if (!rows.length) {
    return `<div style="padding:40px; text-align:center; color:var(--mut)">Tidak ada token yang cocok dengan filter State saat ini.</div>`;
  }

  const feeIcon = microSortCol === 'fee' ? (microSortDir === 'desc' ? '▼' : '▲') : '⇅';
  const feeIconCls = microSortCol === 'fee' ? (microSortDir === 'desc' ? 'sort-icon active-desc' : 'sort-icon active-asc') : 'sort-icon';

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
          <span style="font-weight:800; color:#fff; font-size:12.5px">${t.symbol}</span>
          <span class="chain-pill ${t.chain === 'SOL' ? 'chain-sol' : t.chain === 'ARC' ? 'chain-arc' : 'chain-rh'}">${t.chain}</span>
          <button class="btn-copy-ca" onclick="copyText('${t.address}', this)" title="Salin CA">📋</button>
        </div>
        <div style="font-size:10px; color:var(--mut)">${t.name.slice(0, 14)}</div>
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

async function saveTelegramConfig() {
  const token = document.getElementById('tg-token').value.trim();
  const chatId = document.getElementById('tg-chat-id').value.trim();
  const btn = document.getElementById('btn-tg-save');
  btn.textContent = 'Menyimpan...';
  try {
    const res = await fetch('/api/telegram/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_token: token, telegram_chat_id: chatId, telegram_enabled: true })
    });
    const pill = document.getElementById('tg-status-pill');
    if (token && chatId) {
      pill.style.background = 'rgba(16, 185, 129, 0.2)';
      pill.style.color = '#34d399';
      pill.textContent = 'Aktif ✅';
    } else {
      pill.style.background = 'rgba(255,255,255,0.08)';
      pill.style.color = 'var(--mut-light)';
      pill.textContent = 'Belum Lengkap';
    }
  } catch (e) {
    console.error(e);
  } finally {
    btn.textContent = 'Simpan Bot';
  }
}

async function testTelegram() {
  const token = document.getElementById('tg-token').value.trim();
  const chatId = document.getElementById('tg-chat-id').value.trim();
  const btn = document.getElementById('btn-tg-test');
  if (!token || !chatId) {
    alert('Isi Token Bot dan Chat ID terlebih dahulu!');
    return;
  }
  btn.textContent = 'Mengirim...';
  try {
    const res = await fetch('/api/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, chat_id: chatId })
    });
    const data = await res.json();
    if (data.ok) {
      alert('✅ Test alert berhasil terkirim ke Telegram kamu!');
    } else {
      alert('❌ Gagal mengirim test: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('❌ Koneksi error: ' + e);
  } finally {
    btn.textContent = 'Kirim Test';
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

  if (document.getElementById('f-sw-ath')) document.getElementById('f-sw-ath').value = f.sw_min_ath ?? 200000;
  if (document.getElementById('f-sw-mc')) document.getElementById('f-sw-mc').value = f.sw_max_mcap ?? 60000;
  if (document.getElementById('f-sw-liq')) document.getElementById('f-sw-liq').value = f.sw_min_liq ?? 10000;
  if (document.getElementById('f-sw-age')) document.getElementById('f-sw-age').value = f.sw_max_age ?? 24;
  if (document.getElementById('f-sw-buy')) document.getElementById('f-sw-buy').value = f.sw_min_buy ?? 52;

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

  // KPI Second Wave Hunter
  const swRows = data.sw_rows || [];
  document.getElementById('kpi-sw-count').textContent = swRows.length;
  if (swRows.length > 0) {
    const maxAth = Math.max(...swRows.map(r => r.ath_mc || 0));
    document.getElementById('kpi-sw-top-ath').textContent = '$' + usd(maxAth);
    const avgDrop = swRows.reduce((a, r) => a + (r.sw_pullback || 0), 0) / swRows.length;
    document.getElementById('kpi-sw-avg-drop').textContent = '-' + avgDrop.toFixed(0) + '%';
  } else {
    document.getElementById('kpi-sw-top-ath').textContent = '—';
    document.getElementById('kpi-sw-avg-drop').textContent = '—';
  }
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
    regimeEl.textContent = activeChainFilter === 'SOL' ? 'Solana CLMM' : activeChainFilter === 'RH' ? 'Robinhood Flow' : activeChainFilter === 'ARC' ? 'Arc Chain EVM' : 'Multi-Chain Flow (3 Chain)';
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
  } else if (masterMode === 'SECONDWAVE') {
    let swList = currentData.sw_rows || [];
    if (activeChainFilter !== 'BOTH') {
      swList = swList.filter(r => (r.chain || 'RH').toUpperCase() === activeChainFilter);
    }
    if (activeSwFilter === 'STRONG_BUY') {
      swList = swList.filter(r => (r.buy_ratio || 0) >= 60);
    } else if (activeSwFilter === 'DEEP_DROP') {
      swList = swList.filter(r => (r.sw_pullback || 0) >= 75);
    }
    document.getElementById('sw-count').textContent = `${swList.length} Token`;
    document.getElementById('sw-scroll').innerHTML = renderSecondWaveTable(swList);
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
    // Opsi B: Filter fee rendah di Absorption view (default min $0.50/h)
    const minFeeAbsorb = Number(currentData.filters?.min_fee_absorb ?? 0.50);
    if (minFeeAbsorb > 0) {
      filtered = filtered.filter(r => (r.fee_hour || 0) >= minFeeAbsorb);
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

// Event Listeners for Second Wave Tabs
document.getElementById('tabs-sw').onclick = e => {
  const btn = e.target.closest('button');
  if (!btn || !btn.dataset.sw) return;
  document.querySelectorAll('#tabs-sw .tab-btn').forEach(b => b.classList.toggle('active', b === btn));
  activeSwFilter = btn.dataset.sw;
  renderCurrentView();
};

document.getElementById('btn-apply-sw').onclick = async () => {
  const btn = document.getElementById('btn-apply-sw');
  btn.textContent = 'Menyimpan...';
  const body = {
    sw_min_ath: Number(document.getElementById('f-sw-ath').value),
    sw_max_mcap: Number(document.getElementById('f-sw-mc').value),
    sw_min_liq: Number(document.getElementById('f-sw-liq').value),
    sw_max_age: Number(document.getElementById('f-sw-age').value),
    sw_min_buy: Number(document.getElementById('f-sw-buy').value),
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
    console.error('Filter SW save error:', err);
  } finally {
    btn.textContent = 'Terapkan SW';
  }
};

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

document.getElementById('btn-tg-save').onclick = saveTelegramConfig;
document.getElementById('btn-tg-test').onclick = testTelegram;

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
    load();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan Sekarang';
  }
};

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
            if ICON_PATH.exists():
                try:
                    self._send(200, ICON_PATH.read_bytes(), "image/png")
                    return
                except OSError:
                    pass
        if self.path == "/api/state":
            with lock:
                payload = {
                    "scanning": state["scanning"],
                    "error": state["error"],
                    "scanned_at": state["scanned_at"],
                    "next_scan_at": state.get("next_scan_at"),
                    "n": state["n"],
                    "rows": state["rows"],
                    "sw_rows": state.get("sw_rows", []),
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
    print("Chop Radar & Absorption State Machine Terminal V6 (Unified + Second Wave): http://%s:%s" % (HOST, PORT))
    print("Unified Robinhood & Solana Terminal · Real-Time Chain Switcher · Auto-scan tiap 5m · Port %s · Ctrl+C stop\n" % PORT)
    threading.Thread(target=loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    webbrowser.open("http://%s:%s" % (HOST, PORT))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")


if __name__ == "__main__":
    main()
