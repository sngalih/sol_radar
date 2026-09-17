#!/usr/bin/env python3
"""Chop Radar Solana — Telegram LP Reporter Bot (GMGN Edition).
Auto-scans Solana tokens from GMGN every 5 minutes and sends structured LP reports to Telegram.

Data Source: GMGN Solana Open API (https://openapi.gmgn.ai) + Automatic Fallback to Meteora DLMM.
Matches Dashboard V5 logic 100%: ER, Symmetric Volatility 5m/1h, On-Chain Safety & Microstate Machine.

Report Format:
🟢 SIAP LP (Fee >= $3/h & MC >= $500k)
• TOKEN ➔ $X.XX/h │ MC $X.XX │ ER X.X

📡 ABSORPTION RADAR (MC >= $500k)
• TOKEN ➔ $X.XX/h │ MC $X.XX │ 🎯 Status
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure stdout handles UTF-8 smoothly on Windows and Linux consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# GMGN API Setup with CookieJar to maintain session & prevent 429
COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))
GMGN_KEY = "gmgn_2d9f7ba605cabf03adeb3276e42427d4"

QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

DEFAULT_CONFIG = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "data_source": "GMGN",      # "GMGN" (default) atau "METEORA"
    "interval_sec": 300,        # 5 menit
    "position_usd": 100,        # modal posisi $100
    "min_liq": 20000,           # TVL pool min $20k
    "min_mcap": 500000.0,       # Market cap minimal $500k
    "max_mcap": 500000000.0,    # Filter token raksasa / native SOL ($500M)
    "min_fee_siap_lp": 3.0,     # Hanya tampilkan Siap LP jika fee/hour >= $3.00
    "min_vl": 2.0,              # V/L 24h min 2x
    "max_5m": 15.0,             # volatilitas 5m max 15%
    "max_1h": 80.0,             # volatilitas 1h max 80%
    "max_er": 20.0,             # Efficiency Ratio max 20
    "min_absorb_score": 65.0,
    "top_n_display": 12,        # batas tampilan list token per kategori
}


def load_env_file(filepath: Path) -> dict[str, str]:
    """Parse .env file secara mandiri tanpa memerlukan pustaka python-dotenv tambahan."""
    env_vars: dict[str, str] = {}
    if not filepath.exists():
        return env_vars
    try:
        lines = filepath.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    env_vars[k] = v
    except OSError:
        pass
    return env_vars


def get_config() -> dict[str, Any]:
    """Mengambil konfigurasi gabungan dari: CLI args, OS Env, .env file, dan json filter."""
    conf = dict(DEFAULT_CONFIG)

    # 1. Coba baca dari file .env
    file_env = load_env_file(ENV_FILE)

    # 2. Coba baca dari file filter yang ada di folder
    for fn in ["v5-filters.json", "sol-hp-filters.json", "hp-filters.json"]:
        fp = BASE_DIR / fn
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if data.get("telegram_token"):
                        conf["telegram_bot_token"] = str(data["telegram_token"]).strip()
                    if data.get("telegram_chat_id"):
                        conf["telegram_chat_id"] = str(data["telegram_chat_id"]).strip()
                    for k in ["min_liq", "min_vl", "max_5m", "max_1h", "max_er", "position"]:
                        if k in data:
                            conf[k if k != "position" else "position_usd"] = float(data[k])
                    break
            except Exception:
                pass

    # 3. Timpa dengan .env jika ada
    if "TELEGRAM_BOT_TOKEN" in file_env:
        conf["telegram_bot_token"] = file_env["TELEGRAM_BOT_TOKEN"]
    if "TELEGRAM_CHAT_ID" in file_env:
        conf["telegram_chat_id"] = file_env["TELEGRAM_CHAT_ID"]
    if "DATA_SOURCE" in file_env:
        conf["data_source"] = file_env["DATA_SOURCE"].upper()
    if "SCAN_INTERVAL" in file_env:
        conf["interval_sec"] = int(file_env["SCAN_INTERVAL"])
    if "POSITION_USD" in file_env:
        conf["position_usd"] = float(file_env["POSITION_USD"])
    if "MIN_LIQ" in file_env:
        conf["min_liq"] = float(file_env["MIN_LIQ"])
    if "MIN_MCAP" in file_env:
        conf["min_mcap"] = float(file_env["MIN_MCAP"])
    if "MAX_MCAP" in file_env:
        conf["max_mcap"] = float(file_env["MAX_MCAP"])
    if "MIN_FEE_SIAP_LP" in file_env:
        conf["min_fee_siap_lp"] = float(file_env["MIN_FEE_SIAP_LP"])
    if "MIN_VL" in file_env:
        conf["min_vl"] = float(file_env["MIN_VL"])
    if "MAX_5M" in file_env:
        conf["max_5m"] = float(file_env["MAX_5M"])
    if "MAX_1H" in file_env:
        conf["max_1h"] = float(file_env["MAX_1H"])
    if "MAX_ER" in file_env:
        conf["max_er"] = float(file_env["MAX_ER"])

    # 4. Timpa dengan Environment Variables sistem operasi
    conf["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", conf["telegram_bot_token"])
    conf["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", conf["telegram_chat_id"])
    if os.getenv("DATA_SOURCE"):
        conf["data_source"] = os.environ["DATA_SOURCE"].upper()
    if os.getenv("SCAN_INTERVAL"):
        conf["interval_sec"] = int(os.environ["SCAN_INTERVAL"])
    if os.getenv("POSITION_USD"):
        conf["position_usd"] = float(os.environ["POSITION_USD"])
    if os.getenv("MIN_MCAP"):
        conf["min_mcap"] = float(os.environ["MIN_MCAP"])
    if os.getenv("MAX_MCAP"):
        conf["max_mcap"] = float(os.environ["MAX_MCAP"])
    if os.getenv("MIN_FEE_SIAP_LP"):
        conf["min_fee_siap_lp"] = float(os.environ["MIN_FEE_SIAP_LP"])

    return conf


# ================= DATA FETCHING ENGINES =================

def fetch_gmgn_sol_tokens(limit: int = 50) -> list[dict]:
    """Mengambil data token Solana langsung dari GMGN Open API (1 request per scan)."""
    qs = urllib.parse.urlencode({
        "chain": "sol",
        "interval": "1h",
        "limit": str(limit),
        "order_by": "volume",
        "direction": "desc",
        "timestamp": str(int(time.time())),
        "client_id": str(uuid.uuid4()),
    })
    url = "https://openapi.gmgn.ai/v1/market/rank?" + qs
    req = urllib.request.Request(
        url,
        headers={
            "X-APIKEY": GMGN_KEY,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
        },
    )
    for attempt in range(3):
        try:
            with OPENER.open(req, timeout=22) as res:
                data = json.loads(res.read().decode())
                d1 = data.get("data") or data
                if isinstance(d1, dict):
                    d2 = d1.get("data") or d1
                    if isinstance(d2, dict):
                        return d2.get("rank") or []
                    if isinstance(d2, list):
                        return d2
                elif isinstance(d1, list):
                    return d1
            return []
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                backoff = 4.0 * (attempt + 1)
                print(f"[GMGN] 429 Rate Limit — jeda {backoff}s lalu retry (percobaan {attempt + 1}/2)...", file=sys.stderr)
                time.sleep(backoff)
                continue
            print(f"[GMGN] HTTP Error {err.code}: {err.reason}", file=sys.stderr)
            break
        except Exception as e:
            print(f"[GMGN] Fetch Error: {e}", file=sys.stderr)
            break
    return []


def fetch_meteora_dlmm_pools(limit: int = 50, min_tvl: int = 15000) -> list[dict]:
    """Fallback Engine: Mengambil pool DLMM aktif dari official REST API Meteora."""
    pools_map: dict[str, dict] = {}
    p1 = {
        "page_size": min(limit, 60),
        "sort_by": "fee_tvl_ratio_1h:desc",
        "filter_by": f"is_blacklisted=false && tvl>={min_tvl}",
    }
    u1 = "https://dlmm.datapi.meteora.ag/pools?" + urllib.parse.urlencode(p1)
    try:
        req = urllib.request.Request(u1, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=12) as res:
            data = json.loads(res.read().decode())
            for p in data.get("data", []):
                if p.get("address"):
                    pools_map[p["address"]] = p
    except Exception as e:
        print(f"[Meteora Fallback] Query 1 Error: {e}", file=sys.stderr)

    return list(pools_map.values())


def fetch_dexscreener_batch(token_addrs: list[str]) -> dict[str, dict]:
    """Enrich data token untuk Meteora Fallback via DexScreener batch API."""
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
            print(f"[DexScreener] Error: {e}", file=sys.stderr)
    return out


# ================= METRICS & SCORING =================

def _usd(n: float) -> str:
    if not n:
        return "$0"
    if n >= 1e6:
        val = n / 1e6
        return f"${val:.2f}M" if val < 10 else f"${val:.1f}M"
    if n >= 1e3:
        val = n / 1e3
        return f"${val:.0f}k" if (val >= 100 or val == int(val)) else f"${val:.1f}k"
    return f"${n:,.0f}"


def num(row: dict, *keys: str) -> float:
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0


def score_gmgn_token(row: dict, conf: dict[str, Any]) -> dict[str, Any]:
    """Scoring token Solana berbasis data GMGN (100% identik dengan Dashboard V5)."""
    symbol = str(row.get("symbol") or "?").strip()
    name = str(row.get("name") or symbol).strip()
    addr = str(row.get("address") or "")

    price = num(row, "price")
    liq = num(row, "liquidity")
    vol = num(row, "volume")
    p5 = num(row, "price_change_percent5m")
    p1 = num(row, "price_change_percent1h")
    mcap = num(row, "market_cap", "marketcap")
    vl = (vol / liq) if liq else 0.0

    # On-Chain Security
    top10_rate = round(num(row, "top_10_holder_rate") * 100, 1)
    dev_team_hold = round(num(row, "dev_team_hold_rate") * 100, 1)
    insider_rate = round(num(row, "rat_trader_amount_rate") * 100, 1)
    is_wash = bool(row.get("is_wash_trading"))
    is_honeypot = bool(row.get("is_honeypot"))

    buys = int(num(row, "buys"))
    sells = int(num(row, "sells"))
    total_tx = buys + sells
    buy_ratio = round((buys / total_tx * 100), 1) if total_tx > 0 else 50.0

    # Efficiency Ratio: ER = |1h%| / (V/L)
    er = round(abs(p1) / vl, 2) if vl > 0 else 999.0

    # Estimasi Fee @$100 (Flat 1% Fee Tier seperti di Dashboard V5)
    pos = float(conf.get("position_usd", 100))
    share = (pos / liq) if liq > 0 else 0.0
    fee_hour = round(vol * 0.01 * share, 4)
    fee_24h = round(fee_hour * 24, 4)

    # Microstate Machine (Identik dengan Dashboard V5)
    # 1. Pts V/L (Max 25 pts)
    pts_vl = min(25.0, (vl / 8.0) * 25.0) if vl > 0 else 0.0
    # 2. Pts ER (Max 30 pts)
    pts_er = 30.0 if er <= 1.0 else 25.0 if er <= 2.5 else 18.0 if er <= 5.0 else 10.0 if er <= 10.0 else max(0.0, 10.0 - (er - 10.0) * 0.5)
    # 3. Pts Vol (Max 20 pts)
    pts_vol = 20.0 if vol >= 500000 else 15.0 if vol >= 200000 else 10.0 if vol >= 80000 else 5.0 if vol >= 30000 else 0.0
    # 4. Pts Order Flow (Max 15 pts)
    pts_flow = 15.0 if (46.0 <= buy_ratio <= 60.0) else 12.0 if (60.0 < buy_ratio <= 72.0) else 8.0 if (38.0 <= buy_ratio < 46.0) else 3.0
    # 5. Pts Safety (Max 10 pts)
    pts_safety = 0.0
    if not is_wash and not is_honeypot and top10_rate <= 45.0:
        pts_safety += 5.0
        if dev_team_hold <= 20.0 and insider_rate <= 10.0:
            pts_safety += 3.0
        if int(num(row, "smart_degen_count")) > 0:
            pts_safety += 2.0

    score = round(min(100.0, max(0.0, pts_vl + pts_er + pts_vol + pts_flow + pts_safety)), 1)

    min_vl = float(conf.get("min_vl", 2.0))
    max_5m = float(conf.get("max_5m", 15.0))
    max_1h = float(conf.get("max_1h", 80.0))
    min_liq = float(conf.get("min_liq", 20000.0))

    if is_wash or is_honeypot or top10_rate > 60.0 or insider_rate > 25.0:
        micro_state = "DISTRIBUTION"
        status_label = "Distribution / Toxic"
    elif p1 > max_1h or (p5 > max_5m and buy_ratio >= 65.0):
        micro_state = "EXPANSION"
        status_label = "Expansion / Runner"
    elif p1 < -25.0 or (p5 < -10.0 and buy_ratio < 40.0):
        micro_state = "DISTRIBUTION"
        status_label = "Distribution / Dump"
    elif score >= 70.0 and vl >= min_vl and abs(p5) <= max_5m and abs(p1) <= max_1h and liq >= min_liq:
        micro_state = "ABSORPTION"
        status_label = "Absorption (Prime LP)"
    elif score >= 50.0 and vl >= 2.0 and abs(p5) <= max_5m * 1.3:
        micro_state = "REACCUMULATION"
        status_label = "Ugly Reaccumulation"
    else:
        micro_state = "NEUTRAL"
        status_label = "Chopping Sideways"

    # Validasi filter kriteria Chop Sideways LP
    is_chop = (
        liq >= min_liq
        and vl >= min_vl
        and abs(p5) <= max_5m
        and abs(p1) <= max_1h
        and er <= float(conf.get("max_er", 20.0))
        and not is_wash
        and not is_honeypot
    )

    gmgn_url = f"https://gmgn.ai/sol/token/{addr}"
    dex_url = f"https://dexscreener.com/solana/{addr}"

    return {
        "symbol": symbol,
        "name": name,
        "address": addr,
        "price": price,
        "liq": liq,
        "vol": vol,
        "vl": vl,
        "p5": p5,
        "p1": p1,
        "mcap": mcap,
        "er": er,
        "buy_ratio": buy_ratio,
        "fee_hour": fee_hour,
        "fee_24h": fee_24h,
        "score": score,
        "micro_state": micro_state,
        "status_label": status_label,
        "is_chop": is_chop,
        "url": gmgn_url,
        "gmgn": gmgn_url,
        "dexscreener": dex_url,
    }


# ================= TELEGRAM COMMUNICATION =================

def send_telegram_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Kirim pesan Telegram via HTTP REST API (HTML parse mode)."""
    if not token or not chat_id:
        return False, "Token atau Chat ID belum diisi"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            data = json.loads(res.read().decode())
            return (True, "") if data.get("ok") else (False, str(data.get("description", "Unknown error")))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:250]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, str(e)


# ================= REPORT GENERATION =================

def deduplicate_best_tokens(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mengelompokkan berdasarkan address/symbol token dan memilih 1 token terbaik."""
    best_map: dict[str, dict[str, Any]] = {}
    for p in tokens:
        key = (p.get("address") or p.get("symbol", "")).strip().lower()
        if not key:
            continue
        if key not in best_map:
            best_map[key] = p
        else:
            cur = best_map[key]
            # Bandingkan fee_hour terlebih dahulu, lalu likuiditas sebagai tie-breaker
            p_score = (p.get("fee_hour", 0.0), p.get("liq", 0.0))
            cur_score = (cur.get("fee_hour", 0.0), cur.get("liq", 0.0))
            if p_score > cur_score:
                best_map[key] = p
    return list(best_map.values())


def generate_report(tokens: list[dict[str, Any]], conf: dict[str, Any], source_name: str = "GMGN") -> str:
    """Membuat pesan Telegram sesuai format yang rapi & terstruktur:
    🟢 SIAP LP (Fee >= $3/h & MC >= $500k)
    • TOKEN ➔ Fee/h │ MC │ ER

    📡 ABSORPTION RADAR (MC >= $500k)
    • TOKEN ➔ Fee/h │ MC │ Status
    """
    min_mcap = float(conf.get("min_mcap", 500000.0))
    max_mcap = float(conf.get("max_mcap", 500000000.0))
    min_fee_siap_lp = float(conf.get("min_fee_siap_lp", 3.0))

    # Filter dasar: Hanya token meme/kandidat dalam rentang Mcap (dan bukan SOL/USDC/USDT native)
    filtered = [
        p for p in tokens
        if p.get("address") not in QUOTE_MINTS
        and p.get("symbol", "").upper() not in ("SOL", "WSOL", "USDC", "USDT")
        and min_mcap <= p.get("mcap", 0.0) <= max_mcap
    ]

    # 1. Kategori Siap LP: lolos kriteria CHOP 100% dan fee_hour >= min_fee_siap_lp
    siap_candidates = [
        p for p in filtered
        if p.get("is_chop") and p.get("fee_hour", 0.0) >= min_fee_siap_lp
    ]
    siap_lp = deduplicate_best_tokens(siap_candidates)
    siap_lp.sort(key=lambda x: -x["fee_hour"])

    # 2. Kategori Absorption Radar: micro_state ABSORPTION atau REACCUMULATION atau score tinggi
    absorb_candidates = [
        p for p in filtered
        if p.get("micro_state") in ("ABSORPTION", "REACCUMULATION") or p.get("score", 0.0) >= conf.get("min_absorb_score", 65.0)
    ]
    absorption = deduplicate_best_tokens(absorb_candidates)
    absorption.sort(key=lambda x: (-x.get("score", 0.0), -x.get("fee_hour", 0.0)))

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    top_limit = conf.get("top_n_display", 12)

    lines = [
        f"🚀 <b>CHOP RADAR SOLANA ({source_name.upper()})</b>",
        f"⏱ <i>{now_str} · Tiap {conf['interval_sec'] // 60} Menit</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🟢 <b>SIAP LP (Fee ≥ ${min_fee_siap_lp:.0f}/h & MC ≥ {_usd(min_mcap)})</b>",
    ]

    if siap_lp:
        for t in siap_lp[:top_limit]:
            sym_link = f'<a href="{t["url"]}"><b>{t["symbol"]}</b></a>'
            fee_str = f"<b>${t['fee_hour']:.2f}/h</b>"
            mcap_str = f"MC {_usd(t['mcap'])}"
            er_str = f"ER {t['er']:.1f}"
            lines.append(f"• {sym_link} ➔ {fee_str} │ {mcap_str} │ {er_str}")
        if len(siap_lp) > top_limit:
            lines.append(f"<i>...dan {len(siap_lp) - top_limit} pool lainnya</i>")
    else:
        lines.append(f"<i>(ℹ️ Belum ada pool memenuhi syarat Fee ≥ ${min_fee_siap_lp:.0f}/h & MC ≥ {_usd(min_mcap)})</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📡 <b>ABSORPTION RADAR (MC ≥ {_usd(min_mcap)})</b>")

    if absorption:
        for t in absorption[:top_limit]:
            sym_link = f'<a href="{t["url"]}"><b>{t["symbol"]}</b></a>'
            fee_str = f"${t['fee_hour']:.2f}/h"
            mcap_str = f"MC {_usd(t['mcap'])}"
            status = f"🎯 {t['status_label']}"
            lines.append(f"• {sym_link} ➔ {fee_str} │ {mcap_str} │ {status}")
        if len(absorption) > top_limit:
            lines.append(f"<i>...dan {len(absorption) - top_limit} token lainnya</i>")
    else:
        lines.append("<i>(ℹ️ Belum ada sinyal absorption baru)</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Tap nama token untuk langsung membuka chart & swap di GMGN</i>")

    return "\n".join(lines)


# ================= SCAN ROUTINE =================

def run_single_scan(conf: dict[str, Any], dry_run: bool = False) -> None:
    """Melakukan 1 siklus scan, memformat pesan, dan mengirim ke Telegram."""
    t0 = time.time()
    source = conf.get("data_source", "GMGN").upper()
    print(f"[{time.strftime('%H:%M:%S')}] Memulai scan Solana via {source}...")

    scored_tokens: list[dict[str, Any]] = []

    if source == "GMGN":
        raw_gmgn = fetch_gmgn_sol_tokens(limit=50)
        if raw_gmgn:
            scored_tokens = [score_gmgn_token(r, conf) for r in raw_gmgn]
            print(f"[{time.strftime('%H:%M:%S')}] Berhasil mengambil {len(scored_tokens)} token dari GMGN Solana.")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] GMGN tidak merespons, beralih ke Meteora Fallback...")
            # Fallback ke Meteora DLMM
            source = "METEORA (Fallback)"
            pools_raw = fetch_meteora_dlmm_pools(limit=50, min_tvl=int(conf["min_liq"]))
            meme_addrs = [
                (p.get("token_x") or {}).get("address", "") if (p.get("token_x") or {}).get("address", "") not in QUOTE_MINTS else (p.get("token_y") or {}).get("address", "")
                for p in pools_raw
            ]
            dex_data = fetch_dexscreener_batch(meme_addrs)
            for p in pools_raw:
                tx = (p.get("token_x") or {}).get("address", "")
                ty = (p.get("token_y") or {}).get("address", "")
                m_addr = tx if tx not in QUOTE_MINTS else ty
                d = dex_data.get(m_addr, {})
                t_score = score_gmgn_token({
                    "symbol": (p.get("token_x") or {}).get("symbol") or p.get("name", "").split("-")[0],
                    "name": p.get("name", ""),
                    "address": m_addr,
                    "price": d.get("priceUsd") or 0.0,
                    "liquidity": float(p.get("tvl") or 0.0),
                    "volume": float(p.get("volume", {}).get("1h") or 0.0),
                    "price_change_percent5m": float(d.get("priceChange", {}).get("m5") or 0.0),
                    "price_change_percent1h": float(d.get("priceChange", {}).get("h1") or 0.0),
                    "market_cap": float(d.get("marketCap") or d.get("fdv") or 0.0),
                    "buys": int(d.get("txns", {}).get("h1", {}).get("buys") or 0),
                    "sells": int(d.get("txns", {}).get("h1", {}).get("sells") or 0),
                }, conf)
                t_score["url"] = f"https://app.meteora.ag/dlmm/{p.get('address')}"
                scored_tokens.append(t_score)
    else:
        # User explicitly configured METEORA
        pools_raw = fetch_meteora_dlmm_pools(limit=50, min_tvl=int(conf["min_liq"]))
        meme_addrs = [
            (p.get("token_x") or {}).get("address", "") if (p.get("token_x") or {}).get("address", "") not in QUOTE_MINTS else (p.get("token_y") or {}).get("address", "")
            for p in pools_raw
        ]
        dex_data = fetch_dexscreener_batch(meme_addrs)
        for p in pools_raw:
            tx = (p.get("token_x") or {}).get("address", "")
            ty = (p.get("token_y") or {}).get("address", "")
            m_addr = tx if tx not in QUOTE_MINTS else ty
            d = dex_data.get(m_addr, {})
            t_score = score_gmgn_token({
                "symbol": (p.get("token_x") or {}).get("symbol") or p.get("name", "").split("-")[0],
                "name": p.get("name", ""),
                "address": m_addr,
                "price": d.get("priceUsd") or 0.0,
                "liquidity": float(p.get("tvl") or 0.0),
                "volume": float(p.get("volume", {}).get("1h") or 0.0),
                "price_change_percent5m": float(d.get("priceChange", {}).get("m5") or 0.0),
                "price_change_percent1h": float(d.get("priceChange", {}).get("h1") or 0.0),
                "market_cap": float(d.get("marketCap") or d.get("fdv") or 0.0),
                "buys": int(d.get("txns", {}).get("h1", {}).get("buys") or 0),
                "sells": int(d.get("txns", {}).get("h1", {}).get("sells") or 0),
            }, conf)
            t_score["url"] = f"https://app.meteora.ag/dlmm/{p.get('address')}"
            scored_tokens.append(t_score)

    elapsed = time.time() - t0
    chop_count = sum(1 for p in scored_tokens if p["is_chop"])
    absorb_count = sum(1 for p in scored_tokens if p["micro_state"] in ("ABSORPTION", "REACCUMULATION"))
    print(
        f"[{time.strftime('%H:%M:%S')}] Scan selesai dalam {elapsed:.2f}s | "
        f"Total: {len(scored_tokens)} | Siap LP: {chop_count} | Absorption: {absorb_count}"
    )

    report_text = generate_report(scored_tokens, conf, source_name=source)

    token = conf.get("telegram_bot_token") or ""
    chat_id = conf.get("telegram_chat_id") or ""

    if dry_run or not token or not chat_id:
        print("\n" + "=" * 55)
        print("📢 [PREVIEW LAPORAN TELEGRAM (DRY-RUN / NO TOKEN)]:")
        print("=" * 55)
        print(report_text)
        print("=" * 55 + "\n")
        if not token or not chat_id:
            print("⚠️ Token Bot atau Chat ID belum diisi. Isi di .env atau jalankan dengan parameter.")
    else:
        ok, err = send_telegram_message(token, chat_id, report_text)
        if ok:
            print(f"[{time.strftime('%H:%M:%S')}] ✅ Berhasil mengirim report ke Telegram chat {chat_id}!")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] ❌ Gagal kirim Telegram: {err}", file=sys.stderr)


# ================= INTERACTIVE TELEGRAM LISTENER =================

def telegram_poller_thread(conf: dict[str, Any]) -> None:
    """Mendengarkan chat masuk di Telegram secara polling untuk command /scan atau /help."""
    token = conf.get("telegram_bot_token", "")
    if not token:
        return
    offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"
    print("🤖 Bot listener aktif (/scan, /help siap digunakan)")

    while True:
        try:
            params = {"offset": offset, "timeout": 20}
            u = f"{poll_url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(u, headers={"User-Agent": "ChopRadarBot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.loads(res.read().decode())
                for item in data.get("result", []):
                    offset = max(offset, item["update_id"] + 1)
                    msg = item.get("message") or {}
                    text = str(msg.get("text") or "").strip().lower()
                    cid = str(msg.get("chat", {}).get("id") or "")

                    if not text:
                        continue

                    if text.startswith("/scan"):
                        send_telegram_message(token, cid, "⏳ Sedang memindai data GMGN Solana...")
                        run_single_scan(conf, dry_run=False)
                    elif text.startswith("/start") or text.startswith("/help"):
                        help_msg = (
                            "🤖 <b>Chop Radar Solana Bot (GMGN Edition)</b>\n\n"
                            "Bot ini otomatis memindai token Solana dari GMGN setiap 5 menit.\n\n"
                            "<b>Perintah Tersedia:</b>\n"
                            "• <code>/scan</code> - Jalankan pemindaian & kirim report sekarang juga\n"
                            "• <code>/help</code> - Tampilkan pesan ini\n\n"
                            "<i>Ditenagai oleh GMGN Solana Market API.</i>"
                        )
                        send_telegram_message(token, cid, help_msg)
        except Exception:
            time.sleep(5)
        time.sleep(1)


# ================= MAIN RUNNER =================

def main() -> None:
    parser = argparse.ArgumentParser(description="Chop Radar Solana Telegram Bot (GMGN Edition)")
    parser.add_argument("--dry-run", action="store_true", help="Jalankan 1x scan tanpa kirim Telegram (cetak di terminal)")
    parser.add_argument("--once", action="store_true", help="Jalankan 1x scan dan kirim Telegram, lalu berhenti")
    parser.add_argument("--token", type=str, default="", help="Telegram Bot Token")
    parser.add_argument("--chat-id", type=str, default="", help="Telegram Chat ID")
    parser.add_argument("--interval", type=int, default=0, help="Interval scan dalam detik (default: 300 / 5 menit)")
    parser.add_argument("--source", type=str, default="", help="Data source: GMGN atau METEORA")
    args = parser.parse_args()

    conf = get_config()
    if args.token:
        conf["telegram_bot_token"] = args.token
    if args.chat_id:
        conf["telegram_chat_id"] = args.chat_id
    if args.interval > 0:
        conf["interval_sec"] = args.interval
    if args.source:
        conf["data_source"] = args.source.upper()

    print("=" * 65)
    print(f"🚀 Chop Radar Solana ({conf['data_source']} Edition) — Telegram Bot Reporter")
    print(f"⏱️ Jadwal Scan    : Setiap {conf['interval_sec'] // 60} menit ({conf['interval_sec']} detik)")
    print(f"💰 Posisi Modal  : ${conf['position_usd']:.0f} USD")
    print(f"🛡️ Target Min TVL: ${conf['min_liq']:,.0f}")
    print(f"📊 Filter Mcap   : ≥ {_usd(conf['min_mcap'])}")
    print(f"💵 Filter Siap LP: Fee ≥ ${conf['min_fee_siap_lp']:.2f}/jam")
    has_token = bool(conf.get("telegram_bot_token") and conf.get("telegram_chat_id"))
    print(f"✈️ Telegram Bot  : {'Siap Terhubung' if has_token else 'Token belum diset (Mode Dry-Run)'}")
    print("=" * 65 + "\n")

    if args.dry_run or args.once:
        run_single_scan(conf, dry_run=args.dry_run)
        return

    # Jalankan Telegram interactive command listener di background
    if has_token:
        t_poll = threading.Thread(target=telegram_poller_thread, args=(conf,), daemon=True)
        t_poll.start()

    # Jalankan scan pertama segera saat bot dinyalakan
    run_single_scan(conf, dry_run=not has_token)

    # Loop penjadwalan tiap interval_sec
    while True:
        try:
            sleep_time = conf["interval_sec"]
            print(f"[{time.strftime('%H:%M:%S')}] Menunggu {sleep_time} detik untuk scan berikutnya...\n")
            time.sleep(sleep_time)
            run_single_scan(conf, dry_run=not has_token)
        except KeyboardInterrupt:
            print("\n[!] Bot dihentikan oleh pengguna.")
            break


if __name__ == "__main__":
    main()
