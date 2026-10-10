#!/usr/bin/env python3
"""Chop Radar Solana — Telegram LP Reporter Bot (GMGN Edition).
Auto-scans Solana tokens from GMGN every 5 minutes and sends structured LP reports to Telegram.

Data Source: GMGN Solana Open API (https://openapi.gmgn.ai) + Automatic Fallback to Meteora DLMM.
Matches Dashboard V5 logic 100%: ER, Symmetric Volatility 5m/1h, On-Chain Safety & Microstate Machine.

Report Format:
🟢 SIAP LP (Fee >= $1/h & MC >= $500k)
• TOKEN ➔ $X.XX/h │ MC $X.XX │ ER X.X

📡 ABSORPTION RADAR (MC >= $500k)
• TOKEN ➔ $X.XX/h │ MC $X.XX │ 🎯 Status
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import slow_cook_scorer

# ================= TIMEZONE WIB (UTC+7) =================
WIB = timezone(timedelta(hours=7))

def now_wib() -> datetime:
    """Mengembalikan objek datetime saat ini dalam zona waktu WIB (UTC+7)."""
    return datetime.now(WIB)

def get_wib_str(ts: float | int | None = None, fmt: str = "%H:%M:%S") -> str:
    """Format timestamp (epoch second) ke string WIB. Default waktu sekarang."""
    if ts is None:
        dt = datetime.now(WIB)
    else:
        dt = datetime.fromtimestamp(ts, tz=WIB)
    return dt.strftime(fmt)

def get_wib_time_short(ts: float | int | None = None) -> str:
    """Mengembalikan format jam:menit WIB (contoh: '09:05')."""
    return get_wib_str(ts, fmt="%H:%M")

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
GMGN_KEY = "gmgn_dbf41c754819342e147a702c1482dc0b"

QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# ================= TOKENIZED STOCK FILTER =================
# Daftar ticker saham AS / ETF yang di-tokenisasi di Robinhood Chain.
# Koin-koin ini bukan meme coin — pergerakannya sangat lambat dan yield LP-nya receh.
TOKENIZED_STOCK_SYMBOLS: frozenset[str] = frozenset({
    "META", "NVDA", "GOOGL", "GOOG", "SPY", "SPCX", "MU", "MSTR",
    "GLD", "HIMS", "AAPL", "GME", "AMC", "TSLA", "MSFT", "AMZN",
    "QQQ", "COIN", "AMD", "NFLX", "PLTR", "BABA", "DIS", "INTC",
    "BRK", "JPM", "V", "WMT", "SLV", "USO", "TLT", "IWM", "VIX",
    "NVDL", "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SOXL", "BITX",
    "HOOD", "RBLX", "SNAP", "LYFT", "UBER", "ABNB", "RIVN", "F",
})

# Substring yang selalu ada di nama resmi tokenized equity dari GMGN
_STOCK_NAME_MARKERS: tuple[str, ...] = (
    "• robinhood token",
    "robinhood token",
    "common stock",
    "etf trust",
    "class a common stock",
    "class b common stock",
)


def is_tokenized_stock(token: dict) -> bool:
    """Deteksi apakah token adalah saham/ETF AS yang di-tokenisasi di Robinhood Chain.

    Menggunakan 2 pengecekan:
    1. Nama token (pattern GMGN seperti "NVIDIA • Robinhood Token")
    2. Ticker simbol yang dikenal sebagai saham AS (hanya di-apply untuk chain RH)
    """
    name = str(token.get("name") or "").lower()
    symbol = str(token.get("symbol") or "").upper()
    chain = str(token.get("chain") or "").upper()

    # Cek marker di nama token (berlaku untuk semua chain)
    if any(m in name for m in _STOCK_NAME_MARKERS):
        return True

    # Cek ticker saham bursa (hanya RH chain agar tidak salah block meme coin SOL)
    if chain == "RH" and symbol in TOKENIZED_STOCK_SYMBOLS:
        return True

    return False


# ================= ATH CACHE (Break ATH LP) =================
# Menyimpan All-Time-High MC per token. Diupdate tiap scan, persist ke sol-hp-cache.json.
ATH_CACHE: dict[str, dict] = {}  # { addr: { "symbol": str, "ath_mcap": float, "last_seen": int } }

DEFAULT_CONFIG = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "data_source": "GMGN",      # "GMGN" (default) atau "METEORA"
    "chain_mode": "RH",         # "RH" (default), "SOL", atau "BOTH"
    "gmgn_api_key": GMGN_KEY,   # GMGN Open API Key
    "interval_sec": 300,        # 5 menit
    "position_usd": 100,        # modal posisi $100
    "min_liq": 20000,           # TVL pool min $20k
    "min_mcap": 500000.0,       # Market cap minimal $500k ($500,000)
    "max_mcap": 500000000.0,    # Filter token raksasa / native ($500M)
    "min_age_hours": 12.0,      # Usia minimal token 12 jam (hard filter anti-sniper)
    "min_fee_siap_lp": 0.50,    # Hanya tampilkan Siap LP jika fee/hour >= $0.50
    "min_fee_absorb": 0.50,     # Hanya tampilkan Absorption Radar jika fee/hour >= $0.50
    "min_fee_break_ath": 0.50,  # Hanya tampilkan Break ATH LP jika fee/hour >= $0.50
    "break_ath_min_scans": 3,   # Minimal 3 scan berturut-turut (15 menit)
    "break_ath_min_buy": 50.0,  # Minimal buy ratio 50%
    "break_ath_min_mcap": 500000.0,   # Min Mcap $500k (sama dengan LP biasa)
    "break_ath_min_ath": 500000.0,    # Min ATH yang ditembus > $500k
    "filter_stocks": True,      # Filter tokenized stocks/ETF Robinhood (META, NVDA, GOOGL, dll.)
    "min_vl": 0.5,              # V/L 24h min 0.5x (disesuaikan untuk koin MCap $1M+)
    "max_5m": 15.0,             # volatilitas 5m max 15%
    "max_1h": 20.0,             # volatilitas 1h max 20% (simetris ketat)
    "max_drop_5m": -4.0,        # Asymmetric Downside Guard 5m (drop maks -4%)
    "max_drop_1h": -8.0,        # Asymmetric Downside Guard 1h (drop maks -8%)
    "min_buy_ratio": 46.0,      # Order flow hard gate (min 46% buy, anti-panic dump)
    "max_ath_drawdown": -85.0,  # Batas ATH Drawdown (drop maks -85%, anti-zombie trap)
    "max_er": 20.0,             # Efficiency Ratio max 20
    "min_absorb_score": 65.0,
    "momentum_5m_min_vol": 200000.0,  # Min Volume 5m > $200k
    "runner_t2_min_vol_5m": 200000.0, # Min Volume 5m Runner Tier 2 >= $200k
    "momentum_5m_min_liq": 10000.0,   # Min Liquidity >= $10k
    "momentum_5m_min_fee": 0.50,      # Min Fee run-rate >= $0.50/jam
    "top_n_display": 12,        # batas tampilan list token per kategori
    "st_period": 10,            # Supertrend ATR period (default TradingView)
    "st_multiplier": 3.0,       # Supertrend multiplier
    "akashi_origin_mcap": 55000.0, # Origin pump.fun launch MCap
    "akashi_fibo_low": 0.236,    # Batas bawah Akashi Zone (Fibo 0.236)
    "akashi_fibo_high": 0.382,   # Batas atas Akashi Zone (Fibo 0.382)
    "max_bundler_rate": 0.55,   # Maksimal sniped bundle rate block 0 (55%)
    "max_dev_hold": 20.0,       # Maksimal dev team hold (20% untuk LP)
    "max_top10": 45.0,          # Maksimal top 10 whale hold (45% untuk LP)
    "max_insider": 15.0,        # Maksimal insider rat trader (15%)
    "min_holders": 150,         # Minimal holders untuk LP & Tier 1 (150)
    "bonus_stage_min_ath": 250000.0,      # Min New ATH Market Cap $250k
    "bonus_stage_max_age_hours": 48.0,    # Usia token < 2 hari
    "bonus_stage_min_vl": 1.0,            # Turnover V/L min 1.0x
    "bonus_stage_max_retrace_dist": 3.5,  # Retrace zone (0% s/d +3.5% di atas garis ST 15m)
    "bonus_stage_st_period": 10,          # Supertrend ATR period 15m
    "bonus_stage_st_multiplier": 3.0,     # Supertrend multiplier 15m
    "bonus_stage_max_dev_hold": 10.0,     # Dev hold max 10%
    "bonus_stage_max_top10": 35.0,        # Top 10 whale hold max 35%
    "bonus_stage_max_top70_sniper": 15.0, # Sniper hold rate di top 70 max 15%
    "bonus_stage_max_insider": 12.0,      # Insider rate max 12%
    "bonus_stage_max_bundler": 0.50,      # Bundler max 50%
    "bonus_stage_min_holders": 150,       # Minimal 150 holders
    "bonus_stage_min_vol": 50000.0,       # Min volume $50k
    "bonus_stage_min_buy": 48.0,          # Min buy ratio 48%
    "hourly_surge_min_smart_dual": 5,      # Dual inflow: min +5 smart money
    "hourly_surge_min_kol_dual": 2,        # Dual inflow: min +2 KOL
    "hourly_surge_min_smart_solo": 10,     # Pure smart: min +10 smart money
    "hourly_surge_smart_growth_pct": 30.0, # Pure smart: min +30% growth (jika base >= 15)
    "hourly_surge_min_kol_solo": 3,        # Pure KOL: min +3 KOL
    "hourly_surge_min_mcap": 100000.0,     # Min MCap $100k
    "hourly_surge_max_mcap": 25000000.0,   # Max MCap $25M
    "hourly_surge_min_buy_ratio": 48.0,    # Min buy ratio 48%
    "hourly_surge_max_dev_hold": 20.0,     # Max dev hold 20%
    "hourly_surge_max_top10": 45.0,        # Max top 10 whale hold 45%
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

    # 1. Coba baca dari file .env (default env)
    file_env = load_env_file(ENV_FILE)
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
    if "CHAIN_MODE" in file_env:
        conf["chain_mode"] = file_env["CHAIN_MODE"].upper()
    elif "CHAIN" in file_env:
        conf["chain_mode"] = file_env["CHAIN"].upper()
    if "GMGN_API_KEY" in file_env:
        conf["gmgn_api_key"] = file_env["GMGN_API_KEY"].strip()

    # 2. Coba baca dari file filter runtime (sol-hp-filters.json diprioritaskan agar sync dengan Web)
    for fn in ["sol-hp-filters.json", "v5-filters.json", "hp-filters.json"]:
        fp = BASE_DIR / fn
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if data.get("telegram_token"):
                        conf["telegram_bot_token"] = str(data["telegram_token"]).strip()
                    if data.get("telegram_chat_id"):
                        conf["telegram_chat_id"] = str(data["telegram_chat_id"]).strip()
                    if data.get("chain_mode"):
                        conf["chain_mode"] = str(data["chain_mode"]).upper().strip()
                    for k in [
                        "min_liq", "min_vl", "max_5m", "max_1h", "max_er", "position",
                        "min_fee_siap_lp", "min_fee_break_ath", "momentum_5m_min_vol", "runner_t2_min_vol_5m",
                        "momentum_5m_min_liq", "momentum_5m_min_fee", "min_mcap", "max_mcap",
                        "min_age_hours", "max_drop_1h", "max_drop_5m", "min_buy_ratio", "max_ath_drawdown",
                        "bonus_stage_min_ath", "bonus_stage_max_age_hours", "bonus_stage_min_vl",
                        "bonus_stage_max_retrace_dist", "bonus_stage_st_period", "bonus_stage_st_multiplier",
                        "bonus_stage_max_dev_hold", "bonus_stage_max_top10", "bonus_stage_max_top70_sniper",
                        "bonus_stage_max_insider", "bonus_stage_max_bundler", "bonus_stage_min_holders",
                        "bonus_stage_min_vol", "bonus_stage_min_buy"
                    ]:
                        if k in data:
                            val = float(data[k])
                            if k in ("min_fee_siap_lp", "min_fee_break_ath") and val in (1.0, 3.0):
                                val = 0.50
                            if k == "min_vl" and val in (0.6, 2.0):
                                val = 0.5
                            if k == "max_1h" and val == 80.0:
                                val = 20.0
                            if k == "min_mcap" and val == 1000000.0:
                                val = 500000.0
                            if k == "min_age_hours" and val == 24.0:
                                val = 12.0
                            if k == "break_ath_min_mcap" and val == 1000000.0:
                                val = 500000.0
                            conf[k if k != "position" else "position_usd"] = val
                    break
            except Exception:
                pass

    # 3. Timpa dengan Environment Variables sistem operasi (jika di-set eksplisit di terminal / docker / PM2)
    conf["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", conf["telegram_bot_token"])
    conf["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", conf["telegram_chat_id"])
    if os.getenv("GMGN_API_KEY"):
        conf["gmgn_api_key"] = os.environ["GMGN_API_KEY"].strip()
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
    if os.getenv("MIN_AGE_HOURS"):
        conf["min_age_hours"] = float(os.environ["MIN_AGE_HOURS"])
    if os.getenv("MIN_FEE_SIAP_LP"):
        conf["min_fee_siap_lp"] = float(os.environ["MIN_FEE_SIAP_LP"])
    if os.getenv("MIN_VL"):
        conf["min_vl"] = float(os.environ["MIN_VL"])
    if os.getenv("MAX_1H"):
        conf["max_1h"] = float(os.environ["MAX_1H"])
    if os.getenv("MIN_BUY_RATIO"):
        conf["min_buy_ratio"] = float(os.environ["MIN_BUY_RATIO"])
    if os.getenv("MAX_ATH_DRAWDOWN"):
        conf["max_ath_drawdown"] = float(os.environ["MAX_ATH_DRAWDOWN"])
    if os.getenv("MAX_DROP_1H"):
        conf["max_drop_1h"] = float(os.environ["MAX_DROP_1H"])
    if os.getenv("MAX_DROP_5M"):
        conf["max_drop_5m"] = float(os.environ["MAX_DROP_5M"])

    return conf


# ================= DATA FETCHING ENGINES =================

def fetch_gmgn_tokens(chain: str = "sol", api_key: str = "", limit: int = 50) -> list[dict]:
    """Mengambil data token dari GMGN Open API (chain: 'sol' atau 'robinhood')."""
    key = api_key.strip() if api_key else GMGN_KEY
    api_chain = "robinhood" if chain.lower() in ("rh", "robinhood") else "sol"
    chain_tag = "RH" if api_chain == "robinhood" else "SOL"
    qs = urllib.parse.urlencode({
        "orderby": "volume",
        "direction": "desc",
    })
    url = f"https://gmgn.ai/defi/quotation/v1/rank/{api_chain}/swaps/1h?{qs}"
    req = urllib.request.Request(
        url,
        headers={
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
                rows = []
                if isinstance(d1, dict):
                    d2 = d1.get("data") or d1
                    if isinstance(d2, dict):
                        rows = d2.get("rank") or []
                    elif isinstance(d2, list):
                        rows = d2
                elif isinstance(d1, list):
                    rows = d1
                if rows:
                    for r in rows:
                        r["chain"] = chain_tag
                    return rows[:limit]
            return []
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                backoff = 4.0 * (attempt + 1)
                print(f"[GMGN] 429 Rate Limit ({chain_tag}) — jeda {backoff}s lalu retry (percobaan {attempt + 1}/2)...", file=sys.stderr)
                time.sleep(backoff)
                continue
            print(f"[GMGN] HTTP Error {err.code} ({chain_tag}): {err.reason}", file=sys.stderr)
            break
        except Exception as e:
            print(f"[GMGN] Fetch Error ({chain_tag}): {e}", file=sys.stderr)
            break
    return []


def fetch_gmgn_sol_tokens(api_key: str = "", limit: int = 50) -> list[dict]:
    """Alias helper untuk pemindaian khusus Solana."""
    return fetch_gmgn_tokens(chain="sol", api_key=api_key, limit=limit)


def fetch_gmgn_rh_tokens(api_key: str = "", limit: int = 50) -> list[dict]:
    """Alias helper untuk pemindaian khusus Robinhood."""
    return fetch_gmgn_tokens(chain="robinhood", api_key=api_key, limit=limit)


def fetch_gmgn_trending_5m(chain: str = "sol", limit: int = 100) -> list[dict]:
    """Mengambil trending tokens 5 menit dari GMGN quotation rank (chain: 'sol' atau 'robinhood')."""
    api_chain = "robinhood" if chain.lower() in ("rh", "robinhood") else "sol"
    chain_tag = "RH" if api_chain == "robinhood" else "SOL"
    url = f"https://gmgn.ai/defi/quotation/v1/rank/{api_chain}/swaps/5m?orderby=volume&direction=desc"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
        },
    )
    for attempt in range(3):
        try:
            with OPENER.open(req, timeout=22) as res:
                data = json.loads(res.read().decode())
                d1 = data.get("data") or data
                rows = []
                if isinstance(d1, dict):
                    rows = d1.get("rank") or []
                elif isinstance(d1, list):
                    rows = d1
                if rows:
                    for r in rows:
                        r["chain"] = chain_tag
                    return rows[:limit]
            return []
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                backoff = 3.0 * (attempt + 1)
                time.sleep(backoff)
                continue
            print(f"[GMGN 5m] HTTP Error {err.code} ({chain_tag}): {err.reason}", file=sys.stderr)
            break
        except Exception as e:
            print(f"[GMGN 5m] Fetch Error ({chain_tag}): {e}", file=sys.stderr)
            break
    return []


def fetch_gmgn_trending_24h(chain: str = "sol", limit: int = 100) -> list[dict]:
    """Mengambil top trending tokens 24h by volume dari GMGN quotation rank (chain: 'sol' atau 'robinhood')."""
    api_chain = "robinhood" if chain.lower() in ("rh", "robinhood") else "sol"
    chain_tag = "RH" if api_chain == "robinhood" else "SOL"
    url = f"https://gmgn.ai/defi/quotation/v1/rank/{api_chain}/swaps/24h?orderby=volume&direction=desc"
    req = urllib.request.Request(
        url,
        headers={
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
                rows = []
                if isinstance(d1, dict):
                    rows = d1.get("rank") or []
                elif isinstance(d1, list):
                    rows = d1
                if rows:
                    for r in rows:
                        r["chain"] = chain_tag
                    return rows[:limit]
            return []
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                backoff = 3.0 * (attempt + 1)
                time.sleep(backoff)
                continue
            print(f"[GMGN 24h] HTTP Error {err.code} ({chain_tag}): {err.reason}", file=sys.stderr)
            break
        except Exception as e:
            print(f"[GMGN 24h] Fetch Error ({chain_tag}): {e}", file=sys.stderr)
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


# ================= GECKOTERMINAL 15M OHLCV & SUPERTREND (Bonus Stage) =================

GECKO_15M_CACHE: dict[str, dict] = {}  # { pool_addr: { "ohlcv": list, "ts": float } }

def fetch_geckoterminal_15m_ohlcv(pool_addr: str) -> list:
    """Mengambil 30 candle 15m terakhir dari GeckoTerminal API dengan in-memory cache 90 detik."""
    if not pool_addr:
        return []
    now = time.time()
    cached = GECKO_15M_CACHE.get(pool_addr)
    if cached and (now - cached.get("ts", 0)) < 90:
        return cached.get("ohlcv", [])

    url = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_addr}/ohlcv/minute?aggregate=15&limit=30"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            ohlcv = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            if isinstance(ohlcv, list) and ohlcv:
                GECKO_15M_CACHE[pool_addr] = {"ohlcv": ohlcv, "ts": now}
                return ohlcv
    except Exception:
        pass
    return []


def compute_supertrend_15m(ohlcv_list: list, period: int = 10, multiplier: float = 3.0) -> tuple[str | None, float | None, float]:
    """Menghitung TradingView Supertrend 15m dari data candlestick GeckoTerminal [ts, o, h, l, c, v].
    Mengembalikan (direction ('bull'/'bear'), supertrend_value, distance_pct).
    """
    if not ohlcv_list or len(ohlcv_list) < period + 1:
        return None, None, 0.0

    # Candle GeckoTerminal berurutan newest first -> balik ke kronologis
    candles = list(reversed(ohlcv_list))
    n = len(candles)

    trs = [
        max(
            float(candles[i][2]) - float(candles[i][3]),
            abs(float(candles[i][2]) - float(candles[i - 1][4])),
            abs(float(candles[i][3]) - float(candles[i - 1][4])),
        )
        for i in range(1, n)
    ]

    atrs = [None] * (period - 1)
    atrs.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)

    dir_trend = None
    st_val = None

    for i, atr in enumerate(atrs):
        if atr is None:
            continue
        idx = i + 1
        h = float(candles[idx][2])
        l = float(candles[idx][3])
        c = float(candles[idx][4])
        hl2 = (h + l) / 2.0
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr

        if st_val is None:
            if c >= hl2:
                dir_trend, st_val = "bull", lower
            else:
                dir_trend, st_val = "bear", upper
        else:
            if dir_trend == "bull":
                lower = max(lower, st_val)
                if c < lower:
                    dir_trend, st_val = "bear", upper
                else:
                    st_val = lower
            else:
                upper = min(upper, st_val)
                if c > upper:
                    dir_trend, st_val = "bull", lower
                else:
                    st_val = upper

    last_close = float(candles[-1][4]) if candles else 0.0
    dist_pct = ((last_close - st_val) / st_val * 100.0) if (st_val and st_val > 0) else 0.0
    return dir_trend, st_val, round(dist_pct, 2)



# ================= ATH CACHE FUNCTIONS (Break ATH LP) =================

def load_ath_cache() -> None:
    """Muat ATH cache dari sol-hp-cache.json["ath_cache"] saat bot startup."""
    global ATH_CACHE
    fp = BASE_DIR / "sol-hp-cache.json"
    if not fp.exists():
        return
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        stored = data.get("ath_cache")
        if isinstance(stored, dict):
            ATH_CACHE.update(stored)
            print(f"[ATH Cache] Dimuat {len(ATH_CACHE)} entri dari {fp.name}")
    except Exception as e:
        print(f"[ATH Cache] Gagal muat cache: {e}", file=sys.stderr)


def save_ath_cache() -> None:
    """Simpan ATH cache ke key 'ath_cache' di sol-hp-cache.json secara append-safe."""
    fp = BASE_DIR / "sol-hp-cache.json"
    try:
        data: dict = {}
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["ath_cache"] = ATH_CACHE
        fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[ATH Cache] Gagal simpan: {e}", file=sys.stderr)


# ================= SHARED SCAN CACHE (Multi-Process & Anti-429 Rate Limit) =================
GLOBAL_SCAN_CACHE: dict[str, Any] = {}
SCAN_LOCK = threading.Lock()
SCAN_CACHE_TTL_SEC = 50  # Cache scan berlaku selama 50s agar Bot dan Web tidak double-scrape GMGN


def save_scan_cache(scan_data: dict[str, Any]) -> None:
    """Simpan hasil scan ke key 'last_scan' di sol-hp-cache.json secara append-safe."""
    fp = BASE_DIR / "sol-hp-cache.json"
    try:
        data: dict = {}
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        clean_data = dict(scan_data)
        if "gaps" in clean_data and len(clean_data["gaps"]) > 60:
            clean_data["gaps"] = clean_data["gaps"][:60]
        # scored_tokens tidak perlu ditulis ke disk agar ukuran cache tetap hemat
        clean_data.pop("scored_tokens", None)
        data["last_scan"] = clean_data
        fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Scan Cache] Gagal simpan ke {fp.name}: {e}", file=sys.stderr)


def get_cached_scan_data(target_chain: str = "") -> dict[str, Any] | None:
    """Muat hasil scan terakhir dari memory atau disk sol-hp-cache.json jika masih fresh (< 50s)."""
    global GLOBAL_SCAN_CACHE
    now = time.time()
    t_chain = target_chain.upper().strip()

    # 1. Cek memory cache
    if GLOBAL_SCAN_CACHE:
        age = now - GLOBAL_SCAN_CACHE.get("scanned_timestamp", 0)
        c_mode = str(GLOBAL_SCAN_CACHE.get("chain_mode", "")).upper().strip()
        if age < SCAN_CACHE_TTL_SEC and (not t_chain or c_mode == t_chain):
            return GLOBAL_SCAN_CACHE

    # 2. Cek disk cache
    fp = BASE_DIR / "sol-hp-cache.json"
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            last_scan = data.get("last_scan")
            if isinstance(last_scan, dict):
                age = now - last_scan.get("scanned_timestamp", 0)
                c_mode = str(last_scan.get("chain_mode", "")).upper().strip()
                if age < SCAN_CACHE_TTL_SEC and (not t_chain or c_mode == t_chain):
                    GLOBAL_SCAN_CACHE = last_scan
                    return last_scan
        except Exception:
            pass
    return None


# ================= 24H SIGNAL HISTORY LOGGING (WIB EDITION) =================
SIGNAL_HISTORY_FILE = BASE_DIR / "signal-history.json"
SIGNAL_RETENTION_SEC = 86400  # 24 jam rolling window
SIGNAL_HISTORY: list[dict[str, Any]] = []


def load_signal_history() -> None:
    """Muat riwayat sinyal dari signal-history.json atau sol-hp-cache.json saat startup."""
    global SIGNAL_HISTORY
    raw_events: list[dict[str, Any]] = []

    # 1. Coba baca dari signal-history.json
    if SIGNAL_HISTORY_FILE.exists():
        try:
            data = json.loads(SIGNAL_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "events" in data and isinstance(data["events"], list):
                raw_events = data["events"]
            elif isinstance(data, list):
                raw_events = data
        except Exception as e:
            print(f"[Signal History] Gagal muat {SIGNAL_HISTORY_FILE.name}: {e}", file=sys.stderr)

    # 2. Fallback baca dari sol-hp-cache.json
    if not raw_events:
        fp_cache = BASE_DIR / "sol-hp-cache.json"
        if fp_cache.exists():
            try:
                c_data = json.loads(fp_cache.read_text(encoding="utf-8"))
                stored = c_data.get("signal_history")
                if isinstance(stored, list):
                    raw_events = stored
            except Exception:
                pass

    # Prune event yang lebih tua dari 24 jam
    cutoff = int(time.time()) - SIGNAL_RETENTION_SEC
    SIGNAL_HISTORY = [e for e in raw_events if int(e.get("timestamp", 0)) >= cutoff]
    print(f"[Signal History] Dimuat {len(SIGNAL_HISTORY)} event sinyal aktif (24h rolling window)")


def save_signal_history() -> None:
    """Simpan riwayat sinyal ke signal-history.json dan sol-hp-cache.json secara atomik."""
    global SIGNAL_HISTORY
    now_ts = int(time.time())
    cutoff = now_ts - SIGNAL_RETENTION_SEC
    # Auto-prune saat menyimpan
    clean_history = [e for e in SIGNAL_HISTORY if int(e.get("timestamp", 0)) >= cutoff]
    SIGNAL_HISTORY = clean_history

    # 1. Tulis ke signal-history.json
    try:
        payload = {
            "updated_at": now_ts,
            "updated_at_wib": get_wib_str(now_ts, "%Y-%m-%d %H:%M:%S WIB"),
            "count": len(clean_history),
            "events": clean_history,
        }
        SIGNAL_HISTORY_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Signal History] Gagal simpan ke {SIGNAL_HISTORY_FILE.name}: {e}", file=sys.stderr)

    # 2. Simpan juga ke sol-hp-cache.json
    fp_cache = BASE_DIR / "sol-hp-cache.json"
    try:
        c_data: dict = {}
        if fp_cache.exists():
            try:
                c_data = json.loads(fp_cache.read_text(encoding="utf-8"))
            except Exception:
                c_data = {}
        c_data["signal_history"] = clean_history
        fp_cache.write_text(json.dumps(c_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Signal History] Gagal simpan ke cache disk: {e}", file=sys.stderr)


def record_signal_events(scan_results: dict[str, Any], scan_ts: int | None = None) -> list[dict[str, Any]]:
    """Mencatat sinyal baru yang muncul di strategi aktif (5M Momentum, Siap LP, CTO, ATH, Absorption).
    Mencegah pencatatan berulang pada interval scan 5m yang sama untuk token & strategi yang sama.
    """
    global SIGNAL_HISTORY
    now_ts = scan_ts or int(time.time())
    scan_boundary = int((now_ts // 300) * 300)
    cutoff = now_ts - SIGNAL_RETENTION_SEC

    # Kategori strategi aktif yang dicatat
    categories = [
        ("runner_momentum", "RUNNER MOMENTUM", scan_results.get("runner_momentum", [])),
        ("bonus_stage", "BONUS STAGE", scan_results.get("bonus_stage", [])),
        ("akashi_zone", "AKASHI ZONE", scan_results.get("akashi_zone", [])),
        ("siap_lp", "SIAP LP", scan_results.get("siap_lp", [])),
        ("smart_lp", "SMART LP", scan_results.get("smart_lp", [])),
        ("slow_cook_lp", "SLOW COOK", scan_results.get("slow_cook_lp", [])),
        ("dip_chop", "30% DIP", scan_results.get("dip_chop", [])),
        ("momentum_5m", "5M MOMENTUM", scan_results.get("momentum_5m", [])),
        ("absorption", "ABSORPTION", scan_results.get("absorption", [])),
        ("break_ath", "BREAK ATH", scan_results.get("break_ath", [])),
    ]

    # Set event yang sudah ada di scan_boundary ini untuk menghindari duplikat: (addr, strategy_key, scan_boundary)
    existing_keys = {
        (
            str(e.get("address") or "").strip().lower(),
            str(e.get("strategy_key") or "").strip().lower(),
            int(e.get("scan_boundary", int(e.get("timestamp", 0)) // 300 * 300)),
        )
        for e in SIGNAL_HISTORY
    }

    time_wib = get_wib_str(now_ts, "%H:%M")
    date_wib = get_wib_str(now_ts, "%d/%m")

    new_added = 0
    for strat_key, strat_title, token_list in categories:
        if not token_list:
            continue
        for t in token_list:
            addr = str(t.get("address") or "").strip()
            if not addr:
                continue
            key = (addr.lower(), strat_key.lower(), scan_boundary)
            if key in existing_keys:
                continue
            existing_keys.add(key)

            sym = str(t.get("symbol") or "?").strip()
            chain = str(t.get("chain") or "SOL").upper()
            url = t.get("url") or (f"https://fomo.family/token/{addr}" if chain == "RH" else f"https://gmgn.ai/sol/token/{addr}")

            event = {
                "id": f"{chain}_{sym}_{strat_key}_{now_ts}",
                "address": addr,
                "symbol": sym,
                "name": str(t.get("name") or sym),
                "logo": t.get("logo", ""),
                "chain": chain,
                "strategy": strat_title,
                "strategy_key": strat_key,
                "timestamp": now_ts,
                "scan_boundary": scan_boundary,
                "time_wib": time_wib,
                "date_wib": date_wib,
                "mcap": float(t.get("mcap") or 0.0),
                "vl": float(t.get("vl") or 0.0),
                "liq": float(t.get("liq") or 0.0),
                "url": url,
            }
            SIGNAL_HISTORY.append(event)
            new_added += 1

    # Auto-prune
    SIGNAL_HISTORY = [e for e in SIGNAL_HISTORY if int(e.get("timestamp", 0)) >= cutoff]

    if new_added > 0:
        save_signal_history()
        print(f"[{get_wib_str()}] [Signal History] +{new_added} sinyal baru dicatat. Total 24h: {len(SIGNAL_HISTORY)}")

    return SIGNAL_HISTORY


def get_aggregated_signal_history(retention_sec: int = SIGNAL_RETENTION_SEC) -> list[dict[str, Any]]:
    """Mengagregasi riwayat sinyal per (strategy_key, address) selama 24 jam terakhir.
    Menghitung jumlah kemunculan (count) dan daftar jam kemunculan WIB (misal '09:05, 20:30, 23:20 WIB').
    """
    global SIGNAL_HISTORY
    cutoff = int(time.time()) - retention_sec
    valid_events = [e for e in SIGNAL_HISTORY if int(e.get("timestamp", 0)) >= cutoff]

    today_date = get_wib_str(fmt="%d/%m")

    # Kelompokkan berdasarkan (strategy_key, address)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in valid_events:
        addr = str(e.get("address") or "").strip().lower()
        s_key = str(e.get("strategy_key") or "").strip().lower()
        if not addr or not s_key:
            continue
        g_key = (s_key, addr)
        if g_key not in groups:
            groups[g_key] = []
        groups[g_key].append(e)

    aggregated: list[dict[str, Any]] = []
    for (s_key, addr), evts in groups.items():
        # Urutkan berdasarkan waktu
        evts.sort(key=lambda x: int(x.get("timestamp", 0)))
        first_evt = evts[0]
        last_evt = evts[-1]

        # Bangun daftar jam unik berurutan
        time_strings: list[str] = []
        seen_times = set()
        for ev in evts:
            tw = ev.get("time_wib") or get_wib_str(ev.get("timestamp"), "%H:%M")
            dw = ev.get("date_wib") or get_wib_str(ev.get("timestamp"), "%d/%m")
            # Jika beda hari dengan hari ini, tambahkan tanggal (misal '29/09 23:20')
            full_t = f"{dw} {tw}" if dw != today_date else tw
            if full_t not in seen_times:
                seen_times.add(full_t)
                time_strings.append(full_t)

        summary_times = ", ".join(time_strings) + " WIB" if time_strings else ""
        time_strings_short = time_strings[-3:] if len(time_strings) > 3 else time_strings
        summary_times_short = ", ".join(time_strings_short) + " WIB" if time_strings_short else ""

        aggregated.append({
            "address": last_evt.get("address", addr),
            "symbol": last_evt.get("symbol", "?"),
            "name": last_evt.get("name", ""),
            "logo": last_evt.get("logo", ""),
            "chain": last_evt.get("chain", "SOL"),
            "strategy": last_evt.get("strategy", s_key.upper()),
            "strategy_key": s_key,
            "count": len(evts),
            "first_seen_ts": int(first_evt.get("timestamp", 0)),
            "last_seen_ts": int(last_evt.get("timestamp", 0)),
            "first_seen_wib": time_strings[0] if time_strings else "",
            "last_seen_wib": time_strings[-1] if time_strings else "",
            "timestamps_wib": time_strings,
            "summary_times": summary_times,
            "summary_times_short": summary_times_short,
            "mcap": last_evt.get("mcap", 0.0),
            "vl": last_evt.get("vl", 0.0),
            "liq": last_evt.get("liq", 0.0),
            "url": last_evt.get("url", ""),
        })

    # Urutkan berdasarkan waktu kemunculan terakhir (terbaru di atas)
    aggregated.sort(key=lambda x: -x["last_seen_ts"])
    return aggregated


def get_token_signal_history_map(retention_sec: int = SIGNAL_RETENTION_SEC) -> dict[str, dict[str, Any]]:
    """Menghasilkan peta lookup riwayat sinyal tercepat per address token: { address_lower: aggregated_item }."""
    agg = get_aggregated_signal_history(retention_sec)
    m: dict[str, dict[str, Any]] = {}
    for it in agg:
        a = str(it.get("address") or "").strip().lower()
        if a and (a not in m or it.get("count", 1) > m[a].get("count", 1)):
            m[a] = it
    return m


def build_telegram_history_report(retention_hours: int = 24) -> str:
    """Membuat laporan riwayat sinyal yang muncul selama 24 jam terakhir dalam zona waktu WIB."""
    aggregated = get_aggregated_signal_history(retention_hours * 3600)
    now_str = get_wib_str(fmt="%H:%M WIB")

    if not aggregated:
        return (
            "📜 <b>RIWAYAT SINYAL 24 JAM TERAKHIR (WIB)</b>\n\n"
            f"<i>Belum ada sinyal aktif yang terdeteksi dalam {retention_hours} jam terakhir (per {now_str}).</i>\n\n"
            "<i>Sinyal akan otomatis tercatat saat token memenuhi kriteria: Siap LP, CTO, 5M Momentum, Break ATH, atau Absorption.</i>"
        )

    categories = [
        ("runner_momentum", "<b>RUNNER</b>"),
        ("bonus_stage", "🎮 <b>BONUS STAGE</b>"),
        ("akashi_zone", "<b>AKASHI ZONE</b>"),
        ("momentum_5m", "⚡ <b>5M MOMENTUM</b>"),
        ("siap_lp", "🟢 <b>SIAP LP (Chop Sideways)</b>"),
        ("break_ath", "🚀 <b>BREAK ATH LP</b>"),
        ("absorption", "📡 <b>ABSORPTION RADAR</b>"),
    ]

    lines = [
        "📜 <b>RIWAYAT SINYAL 24 JAM TERAKHIR (WIB)</b>",
        ""
    ]

    total_tokens = 0
    total_occurrences = 0

    for cat_key, cat_title in categories:
        items = [x for x in aggregated if x.get("strategy_key") == cat_key]
        if not items:
            continue

        lines.append(cat_title)
        for it in items[:10]:
            sym = html.escape(str(it.get("symbol") or "?"))
            sym_link = f'<a href="{it.get("url")}">{sym}</a>' if it.get("url") else sym
            chain = str(it.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            count = it.get("count", 1)
            vl = it.get("vl", 0.0)
            mc_str = _usd(it.get("mcap", 0.0))
            times_str = it.get("summary_times_short") or it.get("summary_times", "")

            lines.append(f"{badge} {sym_link} ({count}x) │ V/L {vl:.1f}x │ MC {mc_str}")
            lines.append(f"  🕒 {times_str}")
            lines.append("")
            total_tokens += 1
            total_occurrences += count

        if len(items) > 10:
            lines.append(f"<i>...dan {len(items) - 10} token lainnya</i>\n")

    lines.append(f"<i>Total: {total_occurrences} sinyal ({total_tokens} token unik) tercatat per {now_str}.</i>")
    return "\n".join(lines).strip()


# ================= 1H SMART MONEY & KOL SURGE TRACKER (GMGN 24H) =================
HOURLY_TRACKER_FILE = BASE_DIR / "hourly-smart-tracker.json"
LAST_PROCESSED_SURGE_HOUR: int = -1


def load_hourly_snapshots() -> dict[str, Any]:
    """Memuat snapshot historis per jam dari hourly-smart-tracker.json."""
    if not HOURLY_TRACKER_FILE.exists():
        return {"snapshots": {}, "updated_at": 0, "last_processed_hour": -1}
    try:
        data = json.loads(HOURLY_TRACKER_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"snapshots": {}, "updated_at": 0, "last_processed_hour": -1}
        if "snapshots" not in data or not isinstance(data["snapshots"], dict):
            data["snapshots"] = {}
        return data
    except Exception as e:
        print(f"[Hourly Tracker] Gagal membaca snapshot: {e}", file=sys.stderr)
        return {"snapshots": {}, "updated_at": 0, "last_processed_hour": -1}


def save_hourly_snapshots(data: dict[str, Any], max_retention_hours: int = 25) -> None:
    """Menyimpan snapshot historis per jam dengan auto-prune snapshot > 25 jam."""
    now_ts = int(time.time())
    data["updated_at"] = now_ts
    snapshots = data.get("snapshots", {})
    cutoff = now_ts - (max_retention_hours * 3600)

    # Auto-pruning
    pruned_snapshots = {}
    for k_ts, v_snap in snapshots.items():
        try:
            if int(k_ts) >= cutoff:
                pruned_snapshots[k_ts] = v_snap
        except (ValueError, TypeError):
            pass
    data["snapshots"] = pruned_snapshots

    try:
        HOURLY_TRACKER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Hourly Tracker] Gagal menyimpan snapshot ke disk: {e}", file=sys.stderr)


def record_hourly_snapshot(tokens: list[dict], timestamp: int | None = None) -> str:
    """Mencatat snapshot token GMGN 24h jam ini ke file disk."""
    now_ts = timestamp or int(time.time())
    hour_key = str(int((now_ts // 3600) * 3600))

    snap_dict: dict[str, dict[str, Any]] = {}
    for r in tokens:
        addr = str(r.get("address") or "").strip()
        if not addr:
            continue
        symbol = str(r.get("symbol") or "?").strip()
        name = str(r.get("name") or symbol).strip()
        smart = int(num(r, "smart_degen_count"))
        kol = int(num(r, "renowned_count"))
        mcap = num(r, "market_cap", "marketcap", "usd_market_cap")
        vol24h = num(r, "volume", "volume_24h")
        price = num(r, "price")
        p1 = num(r, "price_change_percent1h")
        buys = int(num(r, "buys"))
        sells = int(num(r, "sells"))
        holders = int(num(r, "holder_count", "holders"))
        dev_team_hold = num(r, "dev_team_hold_rate")
        top10_rate = num(r, "top_10_holder_rate")
        ren_mint = r.get("renounced_mint")
        ren_freeze = r.get("renounced_freeze_account") if r.get("renounced_freeze_account") is not None else r.get("renounced_freeze")
        is_hp = check_is_honeypot(r)
        is_w = check_is_wash(r)
        created_ts = int(num(r, "created_timestamp", "open_timestamp"))

        snap_dict[addr] = {
            "symbol": symbol,
            "name": name,
            "smart": smart,
            "kol": kol,
            "mcap": mcap,
            "vol24h": vol24h,
            "price": price,
            "p1": p1,
            "buys": buys,
            "sells": sells,
            "holders": holders,
            "dev_team_hold_rate": dev_team_hold,
            "top_10_holder_rate": top10_rate,
            "renounced_mint": ren_mint,
            "renounced_freeze_account": ren_freeze,
            "is_honeypot": is_hp,
            "is_wash": is_w,
            "created_timestamp": created_ts,
        }

    db = load_hourly_snapshots()
    db["snapshots"][hour_key] = snap_dict
    save_hourly_snapshots(db)
    return hour_key


def evaluate_hourly_smart_kol_surge(
    current_tokens: list[dict],
    conf: dict[str, Any],
    timestamp: int | None = None,
) -> list[dict[str, Any]]:
    """Mengevaluasi lonjakan drastis Smart Money & KOL berdasarkan perbandingan snapshot T vs T-1."""
    now_ts = timestamp or int(time.time())
    current_hour_ts = int((now_ts // 3600) * 3600)

    db = load_hourly_snapshots()
    snapshots = db.get("snapshots", {})
    if not snapshots:
        return []

    # Cari snapshot pembanding T-1 (~1 jam yang lalu)
    prev_key = str(current_hour_ts - 3600)
    prev_snapshot = snapshots.get(prev_key)

    if not prev_snapshot:
        candidate_keys = []
        for k in snapshots.keys():
            try:
                k_int = int(k)
                if k_int < current_hour_ts and (current_hour_ts - k_int) <= 10800:
                    candidate_keys.append(k_int)
            except (ValueError, TypeError):
                pass
        if candidate_keys:
            best_k = min(candidate_keys, key=lambda x: abs(x - (current_hour_ts - 3600)))
            prev_snapshot = snapshots.get(str(best_k))

    if not prev_snapshot:
        return []

    min_smart_dual = int(conf.get("hourly_surge_min_smart_dual", 5))
    min_kol_dual = int(conf.get("hourly_surge_min_kol_dual", 2))
    min_smart_solo = int(conf.get("hourly_surge_min_smart_solo", 10))
    smart_growth_pct = float(conf.get("hourly_surge_smart_growth_pct", 30.0))
    min_kol_solo = int(conf.get("hourly_surge_min_kol_solo", 3))
    min_mcap = float(conf.get("hourly_surge_min_mcap", 100000.0))
    max_mcap = float(conf.get("hourly_surge_max_mcap", 25000000.0))
    min_buy_ratio = float(conf.get("hourly_surge_min_buy_ratio", 48.0))
    max_dev_hold = float(conf.get("hourly_surge_max_dev_hold", 20.0))
    max_top10 = float(conf.get("hourly_surge_max_top10", 45.0))

    surge_tokens: list[dict[str, Any]] = []

    for r in current_tokens:
        addr = str(r.get("address") or "").strip()
        if not addr:
            continue

        mcap = num(r, "market_cap", "marketcap", "usd_market_cap")
        if mcap < min_mcap or mcap > max_mcap:
            continue

        # Hard Gate: On-Chain Safety & Anti-Rug
        if check_is_honeypot(r) or check_is_wash(r):
            continue

        dev_team_hold = round(num(r, "dev_team_hold_rate") * 100, 1)
        if dev_team_hold > max_dev_hold:
            continue

        top10_rate = round(num(r, "top_10_holder_rate") * 100, 1)
        if top10_rate > max_top10:
            continue

        # Renounced Mint & Freeze Authority (Khusus Solana)
        ren_mint = r.get("renounced_mint")
        ren_freeze = r.get("renounced_freeze_account") if r.get("renounced_freeze_account") is not None else r.get("renounced_freeze")
        if ren_mint not in (1, True, "1"):
            continue
        if ren_freeze not in (1, True, "1"):
            continue

        # Order flow & price check
        buys = int(num(r, "buys"))
        sells = int(num(r, "sells"))
        total_trades = buys + sells
        buy_ratio = (buys / total_trades * 100.0) if total_trades > 0 else 50.0
        if buy_ratio < min_buy_ratio:
            continue

        p1 = num(r, "price_change_percent1h")
        if p1 < -10.0:
            continue

        curr_smart = int(num(r, "smart_degen_count"))
        curr_kol = int(num(r, "renowned_count"))
        vol24h = num(r, "volume", "volume_24h")
        created_ts = int(num(r, "created_timestamp", "open_timestamp"))

        # Hitung Delta Perubahan 1 Jam
        if addr in prev_snapshot:
            prev_item = prev_snapshot[addr]
            prev_smart = int(prev_item.get("smart", 0))
            prev_kol = int(prev_item.get("kol", 0))
            d_smart = curr_smart - prev_smart
            d_kol = curr_kol - prev_kol
            d_vol = vol24h - float(prev_item.get("vol24h", 0.0))
            growth_smart = ((d_smart / max(prev_smart, 1)) * 100.0) if prev_smart > 0 else 0.0
        else:
            # Token baru masuk top 100 GMGN 24h
            # Hanya lolos jika usia token < 2 jam
            if created_ts and (now_ts - created_ts) <= 7200:
                prev_smart = 0
                prev_kol = 0
                d_smart = curr_smart
                d_kol = curr_kol
                d_vol = vol24h
                growth_smart = 100.0
            else:
                continue

        # Evaluasi Kriteria Lonjakan Drastis
        is_dual = (d_smart >= min_smart_dual and d_kol >= min_kol_dual)
        is_smart_surge = (d_smart >= min_smart_solo) or (prev_smart >= 15 and growth_smart >= smart_growth_pct and d_smart >= 6)
        is_kol_surge = (d_kol >= min_kol_solo)

        if not (is_dual or is_smart_surge or is_kol_surge):
            continue

        tag = "[🚀 DUAL]" if is_dual else ("[🧠 SMART]" if is_smart_surge else "[👑 KOL]")
        rank_weight = 3 if is_dual else (2 if is_smart_surge else 1)

        surge_tokens.append({
            "address": addr,
            "symbol": str(r.get("symbol") or "?").strip(),
            "name": str(r.get("name") or "").strip(),
            "chain": "SOL",
            "smart": curr_smart,
            "kol": curr_kol,
            "d_smart": d_smart,
            "d_kol": d_kol,
            "prev_smart": prev_smart,
            "prev_kol": prev_kol,
            "growth_smart": round(growth_smart, 1),
            "mcap": mcap,
            "vol24h": vol24h,
            "d_vol": d_vol,
            "p1": p1,
            "buy_ratio": round(buy_ratio, 1),
            "tag": tag,
            "rank_weight": rank_weight,
        })

    surge_tokens.sort(key=lambda x: (-x["rank_weight"], -x["d_smart"], -x["d_kol"], -x["vol24h"]))
    return surge_tokens


def format_hourly_surge_report(surge_tokens: list[dict[str, Any]], scan_time_wib: str) -> str:
    """Format laporan Telegram ultra-minimalis 1 baris per token sesuai aturan GEMINI.md."""
    lines = [
        "<b>🧠 1H SMART & KOL SURGE</b>",
        f"<i>🕒 Snapshot {scan_time_wib} WIB · GMGN 24h Top Volume</i>",
        "",
    ]
    for t in surge_tokens[:15]:
        sym = html.escape(t["symbol"])
        addr = t["address"]
        gmgn_url = f"https://gmgn.ai/sol/token/{addr}"
        sym_link = f'<a href="{gmgn_url}">{sym}</a>'
        d_smart = t["d_smart"]
        tot_smart = t["smart"]
        d_kol = t["d_kol"]
        tot_kol = t["kol"]
        mc_str = _usd(t["mcap"])
        vol_str = _usd(t["vol24h"])
        p1 = t["p1"]

        lines.append(
            f"🔸 {sym_link} │ 🧠 Smart +{d_smart} ({tot_smart}) │ 👑 KOL +{d_kol} ({tot_kol}) │ MC {mc_str} │ Vol {vol_str} ({p1:+.0f}% 1h)"
        )
    return "\n".join(lines).strip()


def run_hourly_smart_kol_tracker(conf: dict[str, Any], dry_run: bool = False, force: bool = False) -> list[dict[str, Any]]:
    """Eksekusi pemindaian lonjakan per jam GMGN 24h, kalkulasi delta, dan kirim pesan terpisah ke Telegram."""
    wib_now_str = get_wib_str(fmt="%H:%M")
    print(f"[{get_wib_str()}] [Hourly Surge Engine] Memulai pemindaian GMGN 24h Solana...")

    raw_tokens = fetch_gmgn_trending_24h(chain="sol", limit=100)
    if not raw_tokens:
        print(f"[{get_wib_str()}] [Hourly Surge Engine] Gagal mengambil data 24h dari GMGN.", file=sys.stderr)
        return []

    # Evaluasi lonjakan dibandingkan snapshot T-1
    surge_tokens = evaluate_hourly_smart_kol_surge(raw_tokens, conf)

    # Simpan snapshot jam saat ini ke disk
    record_hourly_snapshot(raw_tokens)

    # Simpan juga ke sol-hp-cache.json agar Web Dashboard dapat membacanya
    try:
        fp_cache = BASE_DIR / "sol-hp-cache.json"
        if fp_cache.exists():
            c_data = json.loads(fp_cache.read_text(encoding="utf-8"))
        else:
            c_data = {}
        c_data["hourly_surge"] = {
            "timestamp": int(time.time()),
            "time_wib": wib_now_str,
            "count": len(surge_tokens),
            "tokens": surge_tokens,
        }
        fp_cache.write_text(json.dumps(c_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e_cache:
        print(f"[Hourly Surge Engine] Gagal update sol-hp-cache.json: {e_cache}", file=sys.stderr)

    if surge_tokens:
        report_text = format_hourly_surge_report(surge_tokens, wib_now_str)
        token = conf.get("telegram_bot_token") or ""
        chat_id = conf.get("telegram_chat_id") or ""

        if dry_run or not token or not chat_id:
            print("\n" + "=" * 55)
            print("📢 [PREVIEW PESAN TELEGRAM TERPISAH (HOURLY SURGE)]:")
            print("=" * 55)
            print(report_text)
            print("=" * 55 + "\n")
        else:
            ok, err = send_telegram_message(token, chat_id, report_text)
            if ok:
                print(f"[{get_wib_str()}] ✅ Berhasil mengirim pesan terpisah 1H SURGE ({len(surge_tokens)} token) ke Telegram!")
            else:
                print(f"[{get_wib_str()}] ❌ Gagal kirim Telegram 1H SURGE: {err}", file=sys.stderr)
    else:
        print(f"[{get_wib_str()}] [Hourly Surge Engine] Snapshot tersimpan. Tidak ada token yang melonjak drastis (0 token). Skip Telegram.")

    return surge_tokens


def ensure_hourly_snapshot_baseline(conf: dict[str, Any]) -> None:
    """Memastikan terdapat snapshot awal saat bot baru dinyalakan agar jam berikutnya (:00) dapat membandingkan delta."""
    db = load_hourly_snapshots()
    snapshots = db.get("snapshots", {})
    now_ts = int(time.time())

    has_recent = False
    for k in snapshots.keys():
        try:
            if now_ts - int(k) <= 7200:
                has_recent = True
                break
        except (ValueError, TypeError):
            pass

    if not has_recent:
        print(f"[{get_wib_str()}] [Hourly Tracker] Inisialisasi baseline snapshot pertama dari GMGN 24h...")
        tokens = fetch_gmgn_trending_24h(chain="sol", limit=100)
        if tokens:
            record_hourly_snapshot(tokens)
            print(f"[{get_wib_str()}] [Hourly Tracker] Baseline awal tersimpan ({len(tokens)} token). Perhitungan lonjakan akan aktif pada jam berikutnya (:00 WIB).")


def handle_manual_surge_command(conf: dict[str, Any], chat_id: str) -> None:
    """Menangani permintaan on-demand status lonjakan 1H Smart Money & KOL di Telegram."""
    token = conf.get("telegram_bot_token") or ""
    surge_tokens = run_hourly_smart_kol_tracker(conf, dry_run=False, force=True)
    if not surge_tokens and token and chat_id:
        now_wib_str = get_wib_str(fmt="%H:%M WIB")
        send_telegram_message(
            token,
            chat_id,
            f"ℹ️ <b>1H SMART & KOL SURGE</b>\n\nBelum ada token GMGN 24h yang melonjak drastis pada perbandingan snapshot 1 jam terakhir (per {now_wib_str}).\n\n<i>Bot otomatis memantau dan mengirim alert terpisah setiap jam di menit :00 WIB.</i>",
        )


def update_ath_cache(scored_tokens: list[dict]) -> None:
    """Update ATH cache dan lacak siklus Break ATH (consecutive scans) per token.
    Prune otomatis: hapus entry tidak terlihat > 72 jam."""
    global ATH_CACHE
    now = int(time.time())
    for t in scored_tokens:
        addr = str(t.get("address") or "").strip()
        mcap = float(t.get("mcap") or 0.0)
        gmgn_ath = float(t.get("ath_mcap") or 0.0)
        symbol = str(t.get("symbol") or "?")
        chain = str(t.get("chain") or "SOL").upper()

        if not addr or mcap <= 0:
            continue

        existing = ATH_CACHE.get(addr)
        if existing is None:
            # Token baru: jika MC saat ini di bawah ATH GMGN, baseline adalah gmgn_ath.
            # Jika MC saat ini sudah di puncak, baseline adalah mcap.
            baseline_ath = gmgn_ath if gmgn_ath > mcap * 1.02 else mcap
            is_breaking = (mcap >= baseline_ath * 1.02)
            ATH_CACHE[addr] = {
                "symbol": symbol,
                "chain": chain,
                "ath_mcap": baseline_ath,
                "peak_mcap": mcap,
                "break_scans": 1 if is_breaking else 0,
                "first_break_ts": now if is_breaking else 0,
                "last_seen": now,
            }
        else:
            existing["symbol"] = symbol
            existing["chain"] = chain
            existing["last_seen"] = now
            baseline_ath = float(existing.get("ath_mcap") or 0.0)
            if baseline_ath <= 0:
                baseline_ath = gmgn_ath if gmgn_ath > 0 else mcap
                existing["ath_mcap"] = baseline_ath

            # Evaluasi breakout dengan buffer +2% dari ATH lama
            if mcap >= baseline_ath * 1.02:
                existing["break_scans"] = existing.get("break_scans", 0) + 1
                if not existing.get("first_break_ts"):
                    existing["first_break_ts"] = now
                existing["peak_mcap"] = max(existing.get("peak_mcap", 0.0), mcap)
            elif mcap >= baseline_ath * 0.98:
                # Retest zone (antara -2% s/d +2% dari ATH)
                # Tahan status scans tanpa reset (sedang menguji support ATH lama)
                pass
            else:
                # Dump / jatuh kembali di bawah ATH (< 98% ATH lama)
                # Jika sempat mencetak peak baru yang signifikan, perbarui baseline ATH
                if existing.get("peak_mcap", 0.0) > baseline_ath * 1.05:
                    existing["ath_mcap"] = existing["peak_mcap"]
                existing["break_scans"] = 0
                existing["first_break_ts"] = 0
                existing["peak_mcap"] = mcap

    # Prune: hapus token yang tidak muncul > 72 jam (259200 detik)
    cutoff = now - 259200
    stale = [a for a, v in ATH_CACHE.items() if v.get("last_seen", 0) < cutoff]
    for a in stale:
        del ATH_CACHE[a]

    # Persist perubahan cache
    save_ath_cache()


def check_is_honeypot(row: dict[str, Any]) -> bool:
    """Deteksi mutlak apakah token merupakan honeypot atau memiliki sell tax ekstrem."""
    val = row.get("is_honeypot")
    if val is True or val == 1 or str(val).lower() in ("true", "1"):
        return True
    if bool(row.get("cannot_sell")):
        return True
    try:
        if float(row.get("sell_tax") or 0.0) >= 50.0:
            return True
    except (ValueError, TypeError):
        pass
    return False


def check_is_wash(row: dict[str, Any]) -> bool:
    """Deteksi apakah token terindikasi wash trading."""
    val = row.get("is_wash_trading") or row.get("is_wash")
    if val is True or val == 1 or str(val).lower() in ("true", "1"):
        return True
    return False


# ================= BREAK ATH LP SCORER =================

def score_break_ath_candidates(
    scored_tokens: list[dict],
    conf: dict,
) -> list[dict]:
    """Filter token yang berhasil break ATH dan bertahan konsisten (default >= 3 scan / 15 menit).

    Kriteria lolos:
    1. break_scans >= min_scans (default: 3 scan berturut-turut = 15 menit)
    2. fee_hour >= min_fee (default: $1.00/jam)
    3. liq >= min_liq (default: $20,000)
    4. buy_ratio >= min_buy (default: 50%)
    5. Bukan tokenized stock
    6. Lolos on-chain safety: top10 <= 60%, insider <= 25%, not honeypot
    """
    min_scans = int(conf.get("break_ath_min_scans", 3))
    min_vl = float(conf.get("min_vl", 0.5))
    min_liq = float(conf.get("min_liq", 20000.0))
    min_buy = float(conf.get("break_ath_min_buy", 50.0))
    min_mcap = float(conf.get("break_ath_min_mcap") or conf.get("min_mcap", 500000.0))
    max_mcap = float(conf.get("max_mcap", 500000000.0))
    min_ath = float(conf.get("break_ath_min_ath") or 500000.0)
    min_age_hours = float(conf.get("min_age_hours", 12.0))

    candidates: list[dict] = []
    now = int(time.time())

    for t in scored_tokens:
        addr = str(t.get("address") or "").strip()
        if not addr:
            continue

        # Exclude tokenized stock & quote mints
        if is_tokenized_stock(t) or addr in QUOTE_MINTS:
            continue

        cached = ATH_CACHE.get(addr)
        if not cached:
            continue

        scans = int(cached.get("break_scans") or 0)
        if scans < min_scans:
            continue

        mcap = float(t.get("mcap") or 0.0)
        if not (min_mcap <= mcap <= max_mcap):
            continue

        if float(t.get("age_hours") or 0.0) < min_age_hours:
            continue

        ath_old = float(cached.get("ath_mcap") or 0.0)
        if ath_old < min_ath:
            continue

        liq = float(t.get("liq") or 0.0)
        if liq < min_liq:
            continue

        vl = float(t.get("vl") or 0.0)
        if vl < min_vl:
            continue

        fee_h = float(t.get("fee_hour") or 0.0)

        buy_ratio = float(t.get("buy_ratio") or 50.0)
        if buy_ratio < min_buy:
            continue

        # On-Chain Safety
        if t.get("is_honeypot") or t.get("is_wash") or float(t.get("top10_rate") or 0) > 60.0 or float(t.get("insider_rate") or 0) > 25.0:
            continue

        breakout_pct = round(((mcap - ath_old) / ath_old * 100), 1) if ath_old > 0 else 0.0

        first_ts = int(cached.get("first_break_ts") or now)
        duration_mins = max(15, int((now - first_ts) // 60))

        chain = str(t.get("chain") or "SOL").upper()
        badge = "🔹" if chain == "RH" else "🔸"
        price_val = float(t.get("price") or 0.0)

        candidates.append({
            "symbol": t.get("symbol") or "?",
            "name": t.get("name") or "",
            "address": addr,
            "chain": chain,
            "chain_badge": badge,
            "url": t.get("url") or "#",
            "mcap": mcap,
            "ath_mcap": ath_old,
            "peak_mcap": float(cached.get("peak_mcap") or mcap),
            "breakout_pct": breakout_pct,
            "break_scans": scans,
            "duration_mins": duration_mins,
            "fee_hour": fee_h,
            "fee_24h": float(t.get("fee_24h") or 0.0),
            "liq": liq,
            "vol": float(t.get("vol") or 0.0),
            "vl": vl,
            "buy_ratio": buy_ratio,
            "price": price_val,
            "range_low": round(price_val * 0.80, 8),
            "range_high": round(price_val * 1.20, 8),
            "range_pct": 20.0,
        })

    # Urutkan: perputaran volume/likuiditas (V/L) tertinggi dulu
    candidates.sort(key=lambda x: -x["vl"])
    return candidates


# ================= 5M MOMENTUM SCORER =================

def score_5m_momentum_candidates(
    tokens_5m: list[dict],
    conf: dict[str, Any],
    scored_map: dict[str, dict] | None = None,
) -> list[dict]:
    """Filter kandidat strategi ⚡ 5M MOMENTUM:
    1. vol_5m >= min_vol (default: $200,000)
    2. p5 > 0.0% (pump up / momentum naik)
    3. liq >= min_liq (default: $10,000)
    4. V/L >= min_vl (disinkronkan dengan 1h V/L jika ada di database scan agar seragam 100%)
    5. buy_ratio >= min_buy_ratio (default: 46.0%, anti-panic dump)
    6. On-Chain Safety: top10 <= 45%, dev <= 20%, insider <= 10%, bundler <= 55%, rug <= 25%, no wash, no honeypot
    7. Bukan tokenized stock & bukan native quote mint
    """
    min_vol = float(conf.get("momentum_5m_min_vol", 200000.0))
    min_liq = float(conf.get("momentum_5m_min_liq", 10000.0))
    min_vl = float(conf.get("min_vl", 0.5))
    min_mcap = float(conf.get("min_mcap", 500000.0))
    max_mcap = float(conf.get("max_mcap", 500000000.0))
    min_age_hours = float(conf.get("min_age_hours", 12.0))
    max_ath_drawdown = float(conf.get("max_ath_drawdown", -85.0))
    min_buy_ratio = float(conf.get("min_buy_ratio", 46.0))
    max_bundler_rate = float(conf.get("max_bundler_rate", 0.55))
    pos = float(conf.get("position_usd", 100.0))

    candidates: list[dict] = []
    seen_addrs = set()

    for r in tokens_5m:
        addr = str(r.get("address") or "").strip()
        if not addr or addr in seen_addrs or addr in QUOTE_MINTS:
            continue

        symbol = str(r.get("symbol") or "?").strip()
        if symbol.upper() in ("SOL", "WSOL", "USDC", "USDT"):
            continue

        if is_tokenized_stock(r):
            continue

        mcap = num(r, "market_cap", "marketcap")
        if not (min_mcap <= mcap <= max_mcap):
            continue

        open_ts = num(r, "open_timestamp", "creation_timestamp")
        age_hours = round((time.time() - open_ts) / 3600.0, 1) if open_ts > 0 else 9999.0
        if age_hours < min_age_hours:
            continue

        ath_mcap = num(r, "history_highest_market_cap")
        ath_drawdown = round(((mcap - ath_mcap) / ath_mcap * 100.0), 1) if ath_mcap > 0 else 0.0
        if ath_drawdown < max_ath_drawdown:
            continue

        vol_5m = num(r, "volume")
        if vol_5m < min_vol:
            continue

        p5 = num(r, "price_change_percent5m")
        if p5 <= 0.0:  # Harus 'pump up' (momentum positif)
            continue

        liq = num(r, "liquidity")
        if liq < min_liq:
            continue

        # V/L Calculation: Jika token sudah ada di database scan 1h (scored_map),
        # gunakan vl 1h riil agar nilainya 100% identik di 5M Momentum, Siap, CTO, Gaps & Web.
        # Jika belum ada di list 1h, gunakan perputaran volume 5m terhadap likuiditas.
        token_1h = scored_map.get(addr) if scored_map else None
        if token_1h and float(token_1h.get("vl") or 0.0) > 0:
            vl_val = float(token_1h.get("vl") or 0.0)
        else:
            vl_val = round((vol_5m * 12.0) / liq, 2) if liq > 0 else 0.0

        if vl_val < min_vl:
            continue

        buys = int(num(r, "buys"))
        sells = int(num(r, "sells"))
        total_tx = buys + sells
        buy_ratio = round((buys / total_tx * 100), 1) if total_tx > 0 else 50.0
        if buy_ratio < min_buy_ratio:
            continue

        # On-Chain Security & Multi-Layer Anti-Rug
        top10_rate = round(num(r, "top_10_holder_rate") * 100, 1)
        dev_team_hold = round(num(r, "dev_team_hold_rate") * 100, 1)
        insider_rate = round(num(r, "rat_trader_amount_rate") * 100, 1)
        bundler_rate = num(r, "bundler_rate")
        is_wash = check_is_wash(r)
        is_honeypot = check_is_honeypot(r)

        if is_wash or is_honeypot:
            continue
        if top10_rate > 45.0 or dev_team_hold > 20.0 or insider_rate > 10.0:
            continue
        if bundler_rate > max_bundler_rate:
            continue

        # Estimasi fee: fee 5m + run-rate fee/jam
        share = (pos / liq) if liq > 0 else 0.0
        fee_5m = round(vol_5m * 0.01 * share, 4)
        fee_hour = round(fee_5m * 12.0, 4)

        chain = str(r.get("chain") or "SOL").upper()
        if chain == "RH":
            gmgn_url = f"https://gmgn.ai/robinhood/token/{addr}"
            badge = "🔹"
        else:
            gmgn_url = f"https://gmgn.ai/sol/token/{addr}"
            badge = "🔸"

        p1 = num(r, "price_change_percent1h")
        er = round(abs(p1) / vl_val, 2) if vl_val > 0 else 99.0

        score = 80.0
        if dev_team_hold <= 5.0 and insider_rate <= 5.0:
            score += 10.0
        if top10_rate <= 30.0:
            score += 10.0

        candidates.append({
            "symbol": symbol,
            "name": str(r.get("name") or symbol).strip(),
            "address": addr,
            "chain": chain,
            "chain_badge": badge,
            "price": num(r, "price"),
            "liq": liq,
            "vol_5m": vol_5m,
            "vol": vol_5m * 12.0,
            "vl": vl_val,
            "fee_5m": fee_5m,
            "fee_hour": fee_hour,
            "fee_24h": round(fee_hour * 24, 2),
            "p5": p5,
            "p1": p1,
            "mcap": mcap,
            "age_hours": age_hours,
            "ath_drawdown": ath_drawdown,
            "buys": buys,
            "sells": sells,
            "buy_ratio": buy_ratio,
            "top10_rate": top10_rate,
            "dev_team_hold": dev_team_hold,
            "insider_rate": insider_rate,
            "score": score,
            "er": er,
            "url": gmgn_url,
            "gmgn": gmgn_url,
        })
        seen_addrs.add(addr)

    candidates.sort(key=lambda x: (-x["vl"], -x["vol_5m"]))
    return candidates


# ================= RUNNER MOMENTUM SCORER (SPOT ENTRY) =================

def score_runner_momentum_candidates(
    tokens: list[dict],
    conf: dict[str, Any],
) -> list[dict]:
    """Filter kandidat strategi 🚀 RUNNER MOMENTUM (Spot Entry):
    Mendukung 2 Tier Karakter Token:
    Tier 1 (🏛️ Wave 2 / Established Runner):
      - MC: $1M <= mcap <= $10M
      - Usia: age_hours >= 12.0
      - Liq: liq >= $25k
      - V/L: vl >= 1.0x
      - Buy Ratio: buy_ratio >= 50.0%
      - Drawdown: ath_drawdown >= -70.0%
      - Downside guard: p5 >= -3.0% and p1 >= -8.0%
      - Momentum catalyst: p5 > 0.0% OR micro_state in ("ABSORPTION", "REACCUMULATION") OR er <= 8.0
      - Safety: dev_team_hold <= 10.0%, top10_rate <= 40.0%, not honeypot, not wash, not rug risk
      - Tag: runner_tier = "wave2", runner_label = "🏛️ Wave 2"

    Tier 2 (⚡ Fresh Pump / Pump.fun Breakout):
      - MC: $50k <= mcap < $1M
      - Usia: age_hours < 24.0
      - Liq: liq >= $8k
      - Vol 5m: vol_5m >= $200k
      - V/L: vl >= 2.0x
      - Buy Ratio: buy_ratio >= 52.0%
      - Price momentum: p5 > 0.0% and p1 >= -3.0%
      - Safety pump.fun: bundler_rate <= 0.55, dev_team_hold <= 15.0%, not honeypot, not wash
        (jika SOL dan renounced_mint ada, renounced_mint != 0)
      - Tag: runner_tier = "fresh", runner_label = "⚡ Fresh Pump"
    """
    candidates = []
    seen = set()

    for t in tokens:
        addr = str(t.get("address") or "").strip()
        if not addr or addr in seen or addr in QUOTE_MINTS:
            continue
        if str(t.get("symbol") or "").upper() in ("SOL", "WSOL", "USDC", "USDT"):
            continue
        if is_tokenized_stock(t):
            continue
        if t.get("is_honeypot") or t.get("is_wash"):
            continue

        mcap = float(t.get("mcap") or 0.0)
        age_hours = float(t.get("age_hours") or 0.0)
        liq = float(t.get("liq") or 0.0)
        vl = float(t.get("vl") or 0.0)
        p5 = float(t.get("p5") or 0.0)
        p1 = float(t.get("p1") or 0.0)
        buy_ratio = float(t.get("buy_ratio") or 50.0)
        ath_drawdown = float(t.get("ath_drawdown") or 0.0)
        er = float(t.get("er") or 999.0)
        dev_hold = float(t.get("dev_team_hold") or 0.0)
        top10 = float(t.get("top10_rate") or 0.0)
        insider = float(t.get("insider_rate") or 0.0)
        bundler = float(t.get("bundler_rate") or 0.0)
        chain = str(t.get("chain") or "SOL").upper()
        renounced_mint = t.get("renounced_mint")
        renounced_freeze = t.get("renounced_freeze_account")
        freeze_auth = t.get("freeze_authority")
        freezable = t.get("freezable")
        is_freeze_bad = (
            chain == "SOL"
            and (
                (renounced_freeze is not None and str(renounced_freeze) == "0")
                or (freeze_auth is not None and str(freeze_auth) not in ("0", "None", "", "null") and freeze_auth is not False)
                or (freezable is True or str(freezable).lower() in ("true", "1"))
            )
        )
        vol = float(t.get("vol") or 0.0)
        vol_5m = float(t.get("vol_5m") or (vol / 12.0) if vol else 0.0)
        m_state = str(t.get("micro_state") or "")

        matched_tier = None
        tier_label = ""

        # --- Check Tier 1: 🏛️ Established Runner ($1M - $10M) ---
        if (
            1000000.0 <= mcap <= 10000000.0
            and age_hours >= 12.0
            and liq >= 25000.0
            and vl >= 1.0
            and buy_ratio >= 50.0
            and ath_drawdown >= -70.0
            and p5 >= -3.0
            and p1 >= -8.0
            and dev_hold <= 10.0
            and top10 <= 40.0
            and insider <= 10.0
            and not is_freeze_bad
            and not t.get("is_rug_risk")
            and (p5 > 0.0 or m_state in ("ABSORPTION", "REACCUMULATION") or er <= 8.0)
        ):
            matched_tier = "wave2"
            tier_label = "🏛️ Wave 2"

        # --- Check Tier 2: ⚡ Fresh Breakout ($50k - $1M) ---
        elif (
            50000.0 <= mcap < 1000000.0
            and age_hours < 24.0
            and liq >= 8000.0
            and vol_5m >= float(conf.get("runner_t2_min_vol_5m", 200000.0))
            and vl >= 2.0
            and buy_ratio >= 52.0
            and p5 > 0.0
            and p1 >= -3.0
            and bundler <= 0.55
            and dev_hold <= 10.0
            and insider <= 15.0
            and not is_freeze_bad
            and not (chain == "SOL" and renounced_mint is not None and str(renounced_mint) == "0")
        ):
            matched_tier = "fresh"
            tier_label = "⚡ Fresh Pump"

        if matched_tier:
            t_copy = dict(t)
            t_copy["runner_tier"] = matched_tier
            t_copy["runner_label"] = tier_label
            t_copy["vol_5m"] = vol_5m
            narrs = list(t_copy.get("narratives") or [])
            if tier_label not in narrs:
                narrs.insert(0, tier_label)
            t_copy["narratives"] = narrs
            candidates.append(t_copy)
            seen.add(addr)

    deduped = deduplicate_best_tokens(candidates)
    deduped.sort(key=lambda x: (-x.get("vl", 0.0), -x.get("p5", 0.0)))
    return deduped


# ================= BONUS STAGE SCORER (15M SUPERTREND RETRACE) =================

def score_bonus_stage_candidates(
    tokens: list[dict],
    conf: dict[str, Any],
    dex_data: dict[str, dict] | None = None,
) -> list[dict]:
    """Filter kandidat strategi 🎮 BONUS STAGE (15M Supertrend Retrace):
    1. Hard Filter Screening:
       - ATH MC >= $250k (ath_mcap >= 250,000)
       - Token Age < 2 hari (age_hours <= 48.0)
       - Good Volume: V/L >= 1.0x, vol >= $50,000, buy_ratio >= 48.0%
       - Healthy Distribution (Top 75 avg buy normal & Anti-Rug):
         top70_sniper <= 15.0%, top10 <= 35.0%, dev_hold <= 10.0%, insider <= 12.0%,
         bundler <= 50.0%, renounced_mint == 1, renounced_freeze == 1, min 150 holders,
         no wash, no honeypot, no rug risk.
       - TIDAK ADA batasan drawdown dari New ATH (koreksi berapapun diperbolehkan selama tren 15m ST Bullish & retrace).
    2. Sinyal Retrace Supertrend 15m:
       - Trend 15m Supertrend wajib BULLISH (direction == 'bull')
       - Retrace Zone: Harga berada di atas garis ST dengan jarak 0.0% s/d +3.5% (atau candle low menguji garis ST support).
    """
    min_ath = float(conf.get("bonus_stage_min_ath", 250000.0))
    max_age_hours = float(conf.get("bonus_stage_max_age_hours", 48.0))
    min_vl = float(conf.get("bonus_stage_min_vl", 1.0))
    min_vol = float(conf.get("bonus_stage_min_vol", 50000.0))
    min_buy_ratio = float(conf.get("bonus_stage_min_buy", 48.0))
    max_retrace_dist = float(conf.get("bonus_stage_max_retrace_dist", 3.5))
    max_dev_hold = float(conf.get("bonus_stage_max_dev_hold", 10.0))
    max_top10 = float(conf.get("bonus_stage_max_top10", 35.0))
    max_top70_sniper = float(conf.get("bonus_stage_max_top70_sniper", 15.0))
    max_insider = float(conf.get("bonus_stage_max_insider", 12.0))
    max_bundler = float(conf.get("bonus_stage_max_bundler", 0.50))
    min_holders = int(conf.get("bonus_stage_min_holders", 150))
    st_period = int(conf.get("bonus_stage_st_period", 10))
    st_multiplier = float(conf.get("bonus_stage_st_multiplier", 3.0))

    dex_map = dict(dex_data) if dex_data else {}
    candidates: list[dict] = []
    seen: set[str] = set()

    # Kumpulkan kandidat yang lolos screening awal
    pre_candidates = []
    for t in tokens:
        addr = str(t.get("address") or "").strip().lower()
        if not addr or addr in seen or addr in QUOTE_MINTS or is_tokenized_stock(t):
            continue

        ath_mcap = float(t.get("ath_mcap", 0.0) or 0.0)
        age_hours = float(t.get("age_hours", 9999.0) or 9999.0)
        vl = float(t.get("vl", 0.0) or 0.0)
        vol = float(t.get("vol", 0.0) or 0.0)
        vol_5m = float(t.get("vol_5m", 0.0) or 0.0)
        buy_ratio = float(t.get("buy_ratio", 50.0) or 50.0)
        top10 = float(t.get("top10_rate", 100.0) or 100.0)
        dev_hold = float(t.get("dev_team_hold", 100.0) or 100.0)
        insider = float(t.get("insider_rate", 100.0) or 100.0)
        bundler = float(t.get("bundler_rate", 1.0) or 1.0)
        top70_sniper = float(t.get("top70_sniper_hold_rate", 0.0) or 0.0)
        holders = int(t.get("holders", 0) or 0)

        # Hard Filter Screening
        if ath_mcap < min_ath:
            continue
        if age_hours > max_age_hours:
            continue
        if vl < min_vl:
            continue
        if vol < min_vol and (vol_5m * 12.0) < min_vol:
            continue
        if buy_ratio < min_buy_ratio:
            continue
        if dev_hold > max_dev_hold:
            continue
        if top10 > max_top10:
            continue
        if insider > max_insider:
            continue
        if bundler > max_bundler:
            continue
        if top70_sniper > max_top70_sniper:
            continue
        if holders > 0 and holders < min_holders:
            continue
        if t.get("is_rug_risk") or t.get("is_wash") or t.get("is_honeypot"):
            continue

        pre_candidates.append(t)

    if not pre_candidates:
        return []

    # Ambil data pairAddress untuk kandidat yang belum ada di dex_map
    missing_addrs = [c.get("address") for c in pre_candidates if c.get("address") not in dex_map]
    if missing_addrs:
        try:
            extra_dex = fetch_dexscreener_batch(missing_addrs)
            dex_map.update(extra_dex)
        except Exception:
            pass

    # Evaluasi Supertrend 15m untuk setiap kandidat
    for t in pre_candidates:
        addr = str(t.get("address") or "")
        pair_info = dex_map.get(addr) or {}
        pair_addr = pair_info.get("pairAddress") or t.get("pair_address")

        if not pair_addr:
            continue

        ohlcv = fetch_geckoterminal_15m_ohlcv(pair_addr)
        if not ohlcv or len(ohlcv) < st_period + 1:
            continue

        direction, st_val, dist_pct = compute_supertrend_15m(ohlcv, period=st_period, multiplier=st_multiplier)

        # 1. Tren wajib BULLISH
        if direction != "bull" or st_val is None or st_val <= 0:
            continue

        price = float(t.get("price") or 0.0)
        if price <= 0:
            price = float(ohlcv[0][4]) if ohlcv else 0.0

        # Ambil low candle terakhir untuk deteksi wick test support
        last_candle_low = float(ohlcv[0][3]) if ohlcv else price

        # 2. Sinyal Retrace: Harga berada di area support ST (0% s/d +max_retrace_dist%)
        # ATAU low candle menguji garis support ST dan harga close bertahan di atasnya
        is_retrace = (
            (0.0 <= dist_pct <= max_retrace_dist)
            or (last_candle_low <= st_val * 1.01 and price >= st_val)
        )

        if not is_retrace:
            continue

        t_copy = dict(t)
        t_copy["strategy_key"] = "bonus_stage"
        t_copy["st_trend"] = "bull"
        t_copy["st_val"] = st_val
        t_copy["st_retrace_pct"] = dist_pct
        t_copy["pair_address"] = pair_addr
        t_copy["status_label"] = f"Bonus Stage ({dist_pct:+.1f}%)"

        candidates.append(t_copy)
        seen.add(addr.lower())

    deduped = deduplicate_best_tokens(candidates)
    deduped.sort(key=lambda x: (-x.get("vl", 0.0), x.get("st_retrace_pct", 999.0)))
    return deduped



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

    # On-Chain Security & Multi-Layer Anti-Rug (GMGN 97 Indicators)
    chain = str(row.get("chain") or "SOL").upper()
    top10_rate = round(num(row, "top_10_holder_rate") * 100, 1)
    dev_team_hold = round(num(row, "dev_team_hold_rate") * 100, 1)
    insider_rate = round(num(row, "rat_trader_amount_rate") * 100, 1)
    is_wash = check_is_wash(row)
    is_honeypot = check_is_honeypot(row)
    top70_sniper_hold_rate = round(num(row, "top70_sniper_hold_rate") * 100, 2)

    bundler_rate = num(row, "bundler_rate")
    holder_count = int(num(row, "holder_count", "holders"))
    renounced_mint = row.get("renounced_mint")
    renounced_freeze = row.get("renounced_freeze_account")
    freeze_auth = row.get("freeze_authority")
    freezable = row.get("freezable")
    max_bundler_rate = float(conf.get("max_bundler_rate", 0.55))
    min_holders = int(conf.get("min_holders", 150))
    max_dev_hold = float(conf.get("max_dev_hold", 20.0))
    max_top10 = float(conf.get("max_top10", 45.0))
    max_insider = float(conf.get("max_insider", 15.0))

    is_freeze_bad = (
        chain == "SOL"
        and (
            (renounced_freeze is not None and str(renounced_freeze) == "0")
            or (freeze_auth is not None and str(freeze_auth) not in ("0", "None", "", "null") and freeze_auth is not False)
            or (freezable is True or str(freezable).lower() in ("true", "1"))
        )
    )

    is_rug_risk = False
    rug_reasons: list[str] = []
    if bundler_rate > max_bundler_rate:
        is_rug_risk = True
        rug_reasons.append(f"Cabal Bundler ({bundler_rate * 100:.0f}%)")
    if chain == "SOL" and renounced_mint is not None and str(renounced_mint) == "0":
        is_rug_risk = True
        rug_reasons.append("Mint Not Renounced")
    if is_freeze_bad:
        is_rug_risk = True
        rug_reasons.append("Freeze Auth Active")
    if 0 < holder_count < min_holders:
        is_rug_risk = True
        rug_reasons.append(f"Holders Rendah ({holder_count})")
    if dev_team_hold > max_dev_hold:
        is_rug_risk = True
        rug_reasons.append(f"Dev Hold ({dev_team_hold:.1f}% > {max_dev_hold:.0f}%)")
    if top10_rate > max_top10:
        is_rug_risk = True
        rug_reasons.append(f"Top 10 Whale ({top10_rate:.1f}% > {max_top10:.0f}%)")
    if insider_rate > max_insider:
        is_rug_risk = True
        rug_reasons.append(f"Insider ({insider_rate:.1f}% > {max_insider:.0f}%)")

    # ── Narrative Classification ─────────────────────────────────────────────
    cto_flag = int(num(row, "cto_flag"))
    is_cto = (cto_flag == 1)

    narratives: list[str] = []

    smart_degen_count = int(num(row, "smart_degen_count"))
    bluechip_pct = num(row, "bluechip_owner_percentage")

    twitter_username = str(row.get("twitter_username") or "").strip()
    twitter_url = f"https://x.com/{twitter_username}" if twitter_username else ""
    telegram_url = str(row.get("telegram") or "").strip()
    website_url = str(row.get("website") or "").strip()
    twitter_change_flag = int(num(row, "twitter_change_flag"))

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
    if not is_wash and not is_honeypot and not is_rug_risk and top10_rate <= 45.0:
        pts_safety += 5.0
        if dev_team_hold <= 20.0 and insider_rate <= 10.0:
            pts_safety += 3.0
        if smart_degen_count > 0:
            pts_safety += 2.0

    score = round(min(100.0, max(0.0, pts_vl + pts_er + pts_vol + pts_flow + pts_safety)), 1)

    ath_mcap = num(row, "history_highest_market_cap")
    ath_drawdown = round(((mcap - ath_mcap) / ath_mcap * 100.0), 1) if ath_mcap > 0 else 0.0

    min_vl = float(conf.get("min_vl", 0.5))
    max_5m = float(conf.get("max_5m", 15.0))
    max_1h = float(conf.get("max_1h", 20.0))
    max_drop_5m = float(conf.get("max_drop_5m", -4.0))
    max_drop_1h = float(conf.get("max_drop_1h", -8.0))
    min_buy_ratio = float(conf.get("min_buy_ratio", 46.0))
    max_ath_drawdown = float(conf.get("max_ath_drawdown", -85.0))
    min_liq = float(conf.get("min_liq", 20000.0))

    if is_wash or is_honeypot or is_rug_risk or top10_rate > 60.0 or insider_rate > 25.0:
        micro_state = "DISTRIBUTION"
        status_label = "Rug / Dangerous" if is_rug_risk else "Distribution / Toxic"
    elif ath_drawdown < max_ath_drawdown:
        micro_state = "DISTRIBUTION"
        status_label = f"Zombie / Drop ATH ({ath_drawdown:.0f}%)"
    elif p1 > max_1h or (p5 > max_5m and buy_ratio >= 65.0):
        micro_state = "EXPANSION"
        status_label = "Expansion / Runner"
    elif p1 < max_drop_1h or p5 < max_drop_5m or buy_ratio < min_buy_ratio:
        micro_state = "DISTRIBUTION"
        status_label = "Distribution / Dump"
    elif score >= 70.0 and vl >= min_vl and abs(p5) <= max_5m and abs(p1) <= max_1h and liq >= min_liq:
        micro_state = "ABSORPTION"
        status_label = "Absorption (Prime LP)"
    elif score >= 50.0 and vl >= min_vl and abs(p5) <= max_5m * 1.3:
        micro_state = "REACCUMULATION"
        status_label = "Ugly Reaccumulation"
    else:
        micro_state = "NEUTRAL"
        status_label = "Chopping Sideways"

    # Validasi filter kriteria Chop Sideways LP (Anti-Burn & Anti-Drill-Down)
    is_chop = (
        liq >= min_liq
        and vl >= min_vl
        and abs(p5) <= max_5m
        and p5 >= max_drop_5m
        and abs(p1) <= max_1h
        and p1 >= max_drop_1h
        and buy_ratio >= min_buy_ratio
        and ath_drawdown >= max_ath_drawdown
        and er <= float(conf.get("max_er", 20.0))
        and not is_wash
        and not is_honeypot
        and not is_rug_risk
    )

    if chain == "RH":
        gmgn_url = f"https://gmgn.ai/robinhood/token/{addr}"
        dex_url = f"https://fomo.family/token/{addr}"
    else:
        gmgn_url = f"https://gmgn.ai/sol/token/{addr}"
        dex_url = f"https://dexscreener.com/solana/{addr}"

    # Usia token dalam jam (dari open_timestamp atau creation_timestamp)
    open_ts = num(row, "open_timestamp", "creation_timestamp")
    age_hours = round((time.time() - open_ts) / 3600.0, 1) if open_ts > 0 else 9999.0

    return {
        "symbol": symbol,
        "name": name,
        "address": addr,
        "chain": chain,
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
        "is_honeypot": is_honeypot,
        "is_wash": is_wash,
        "is_rug_risk": is_rug_risk,
        "bundler_rate": bundler_rate,
        "rug_reasons": rug_reasons,
        "is_cto": is_cto,
        "narratives": narratives,
        "smart_degen_count": smart_degen_count,
        "bluechip_owner_pct": round(bluechip_pct * 100, 1),
        "twitter_url": twitter_url,
        "telegram_url": telegram_url,
        "website_url": website_url,
        "twitter_change_flag": twitter_change_flag,
        "url": gmgn_url,
        "gmgn": gmgn_url,
        "dexscreener": dex_url,
        "ath_mcap": ath_mcap,       # ATH MC dari GMGN (history_highest_market_cap)
        "ath_drawdown": ath_drawdown, # Drawdown dari ATH (%)
        "age_hours": age_hours,     # Usia token dalam jam (dari open_timestamp)
        "top10_rate": top10_rate,
        "dev_team_hold": dev_team_hold,
        "insider_rate": insider_rate,
        "top70_sniper_hold_rate": top70_sniper_hold_rate,
        "buys": buys,
        "sells": sells,
        "holders": holder_count,
        "logo": str(row.get("logo") or row.get("image_url") or row.get("logo_url") or ""),
    }


# ================= TELEGRAM COMMUNICATION =================

def send_telegram_raw(token: str, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None, parse_mode: str = "HTML") -> tuple[bool, str]:
    """Mengirim pesan tunggal via Telegram API dengan fallback otomatis tanpa HTML jika entitas parsing gagal."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode("utf-8")
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
        body_err = e.read().decode("utf-8", "replace")[:250]
        # Fallback: jika parse_mode HTML error karena entitas, coba kirim tanpa parse_mode
        if parse_mode and ("can't parse entities" in body_err.lower() or "bad request" in body_err.lower()):
            clean_text = re.sub(r"<[^>]+>", "", text)
            return send_telegram_raw(token, chat_id, clean_text, reply_markup=reply_markup, parse_mode="")
        return False, f"HTTP {e.code}: {body_err}"
    except Exception as e:
        return False, str(e)


def send_telegram_message(token: str, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Kirim pesan Telegram via HTTP REST API (HTML parse mode) dengan opsional reply_markup.
    Mendukung auto-chunking jika teks melebihi 4000 karakter dan fallback HTML entity error.
    """
    if not token or not chat_id:
        return False, "Token atau Chat ID belum diisi"
    if not text:
        return False, "Pesan kosong"

    # Jika panjang teks wajar (<= 4000 karakter), kirim langsung
    if len(text) <= 4000:
        return send_telegram_raw(token, chat_id, text, reply_markup=reply_markup)

    # Auto-chunking jika melebihi batas 4000 karakter Telegram
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > 3900 and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    last_idx = len(chunks) - 1
    for idx, ch in enumerate(chunks):
        markup = reply_markup if idx == last_idx else None
        ok, err = send_telegram_raw(token, chat_id, ch, reply_markup=markup)
        if not ok:
            return False, err
        if idx < last_idx:
            time.sleep(0.5)

    return True, ""


def edit_telegram_message(token: str, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Mengedit teks dan tombol inline pesan Telegram yang sudah terkirim secara in-place."""
    if not token or not chat_id or not message_id:
        return False, "Parameter editMessageText tidak lengkap"
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode("utf-8")
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
    except Exception as e:
        return False, str(e)


def answer_callback_query(token: str, callback_query_id: str, text: str = "", show_alert: bool = False) -> tuple[bool, str]:
    """Merespons callback_query dari inline button agar tidak spinning di Telegram client."""
    if not token or not callback_query_id:
        return False, "Token atau callback_query_id kosong"
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    body: dict[str, Any] = {
        "callback_query_id": callback_query_id,
    }
    if text:
        body["text"] = text
        body["show_alert"] = show_alert
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode())
            return (True, "") if data.get("ok") else (False, str(data.get("description", "Unknown error")))
    except Exception as e:
        return False, str(e)


def save_persistent_chain_mode(new_mode: str) -> None:
    """Menyimpan mode chain terpilih ke sol-hp-filters.json secara persisten."""
    fp = BASE_DIR / "sol-hp-filters.json"
    try:
        data: dict[str, Any] = {}
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["chain_mode"] = new_mode
        fp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[{get_wib_str()}] [Config] chain_mode disimpan ke {fp.name}: {new_mode}")
    except Exception as e:
        print(f"[WARN] Gagal menyimpan chain_mode ke {fp.name}: {e}", file=sys.stderr)


def build_chain_inline_markup(current_mode: str) -> dict[str, Any]:
    """Menghasilkan Inline Keyboard Toggle Menu dengan indikator centang ✅ pada mode aktif."""
    mode = (current_mode or "RH").upper()
    c_rh = " ✅" if mode == "RH" else ""
    c_sol = " ✅" if mode == "SOL" else ""
    c_both = " ✅" if mode == "BOTH" else ""

    return {
        "inline_keyboard": [
            [
                {"text": f"🔹 RH Only{c_rh}", "callback_data": "set_chain_rh"},
                {"text": f"🔸 SOL Only{c_sol}", "callback_data": "set_chain_sol"},
            ],
            [
                {"text": f"🔸🔹 SOL + RH (Dual){c_both}", "callback_data": "set_chain_both"},
            ],
            [
                {"text": "⚡ Scan Sekarang", "callback_data": "action_scan"},
                {"text": "🧠 1H Surge", "callback_data": "action_surge"},
                {"text": "📜 History 24h", "callback_data": "action_history"},
            ],
            [
                {"text": "🔄 Refresh Status", "callback_data": "action_refresh"},
            ],
        ]
    }


def build_chain_reply_keyboard() -> dict[str, Any]:
    """Menghasilkan Persistent Quick-Keyboard di bilah bawah layar HP."""
    return {
        "keyboard": [
            [{"text": "🔹 RH Only"}, {"text": "🔸 SOL Only"}, {"text": "🔸🔹 SOL + RH"}],
            [{"text": "⚡ Scan Sekarang"}, {"text": "🧠 1H Surge"}, {"text": "📜 History 24h"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def build_chain_menu_text(current_mode: str, interval_sec: int = 300) -> str:
    """Menghasilkan pesan status pengaturan toggle menu."""
    mode = (current_mode or "RH").upper()
    if mode == "RH":
        label = "🔹 <b>ROBINHOOD Only</b> (Default)"
        desc = "Bot hanya memindai pool & meme coin Robinhood."
    elif mode == "SOL":
        label = "🔸 <b>SOLANA Only</b>"
        desc = "Bot hanya memindai pool & meme coin Solana."
    else:
        label = "🔸 <b>SOLANA</b> & 🔹 <b>ROBINHOOD</b> (Dual-Chain)"
        desc = "Bot memindai kedua chain secara bersamaan."

    mins = interval_sec // 60
    return (
        "⚙️ <b>PENGATURAN MONITORING RADAR LP</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Mode Aktif : {label}\n"
        f"Interval   : <b>Tiap {mins} Menit</b>\n"
        f"Keterangan : <i>{desc}</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap tombol di bawah untuk mengganti mode atau memindai langsung:</i>"
    )


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


def _tlink(item: dict) -> str:
    """Helper untuk menghasilkan link HTML token yang aman dan bersih."""
    s = html.escape(str(item.get("symbol") or "?"))
    u = html.escape(str(item.get("url") or item.get("gmgn") or "#"), quote=True)
    return f'<a href="{u}">{s}</a>'


def generate_report(
    tokens: list[dict[str, Any]],
    conf: dict[str, Any],
    source_name: str = "GMGN",
    sw_candidates: list[dict] | None = None,
    break_ath_candidates: list[dict] | None = None,
    momentum_5m_candidates: list[dict] | None = None,
    runner_list: list[dict] | None = None,
    bonus_list: list[dict] | None = None,
    siap_list: list[dict] | None = None,
    akashi_list: list[dict] | None = None,
    slow_cook_list: list[dict] | None = None,
    dip_list: list[dict] | None = None,
    smart_list: list[dict] | None = None,
    absorption_list: list[dict] | None = None,
    gaps_list: list[dict] | None = None,
) -> str:
    """Membuat pesan Telegram sesuai format yang rapi & terstruktur:
    🟢 SIAP LP (Fee >= $1/h & MC >= $500k)
    • TOKEN ➔ Fee/h │ MC │ ER

    🔴 AKASHI ZONE (Fibo 0.236 - 0.382)
    • TOKEN ➔ V/L │ MC │ Fibo Level

    ⚡ 5M MOMENTUM (5m Vol > $100k & Pump Up)
    • TOKEN ➔ Fee/h │ 5m Vol │ MC

    📡 ABSORPTION RADAR (MC >= $500k)
    • TOKEN ➔ Fee/h │ MC │ Status

    🚀 BREAK ATH LP (15m+ Confirmed, Fee >= $1/h)
    • TOKEN ➔ Fee/h │ MC │ Durasi

    ⚠️ GAPS RADAR (Kompak 1 baris tanpa alasan)
    """
    if siap_list is not None and absorption_list is not None and gaps_list is not None:
        siap_lp = siap_list
        absorption = absorption_list
        gaps = gaps_list
    else:
        min_mcap = float(conf.get("min_mcap", 500000.0))
        max_mcap = float(conf.get("max_mcap", 500000000.0))
        min_fee_siap_lp = float(conf.get("min_fee_siap_lp", 1.0))
        min_fee_absorb = float(conf.get("min_fee_absorb", 0.50))
        min_vl = float(conf.get("min_vl", 0.5))
        do_filter_stocks = bool(conf.get("filter_stocks", True))

        # Filter dasar: Hanya token meme/kandidat dalam rentang Mcap (dan bukan SOL/USDC/USDT native)
        filtered = [
            p for p in tokens
            if p.get("address") not in QUOTE_MINTS
            and p.get("symbol", "").upper() not in ("SOL", "WSOL", "USDC", "USDT")
            and min_mcap <= p.get("mcap", 0.0) <= max_mcap
            and not (do_filter_stocks and is_tokenized_stock(p))
            and not p.get("is_honeypot")
            and not p.get("is_wash")
            and not p.get("is_rug_risk")
        ]

        # 1. Kategori Siap LP: lolos kriteria CHOP 100%
        siap_candidates = [
            p for p in filtered
            if p.get("is_chop")
        ]
        siap_lp = deduplicate_best_tokens(siap_candidates)
        siap_lp.sort(key=lambda x: -x.get("vl", 0.0))

        siap_addrs = {p["address"] for p in siap_lp if p.get("address")}
        absorb_candidates = [
            p for p in filtered
            if p.get("address") not in siap_addrs
            and (p.get("micro_state") in ("ABSORPTION", "REACCUMULATION") or p.get("score", 0.0) >= conf.get("min_absorb_score", 65.0))
            and p.get("vl", 0.0) >= min_vl
            and not p.get("is_honeypot")
            and not p.get("is_wash")
        ]
        absorption = deduplicate_best_tokens(absorb_candidates)
        absorption.sort(key=lambda x: -x.get("vl", 0.0))

        # Gaps logic
        min_liq = float(conf.get("min_liq", 20000.0))
        min_vl = float(conf.get("min_vl", 0.5))
        max_5m = float(conf.get("max_5m", 15.0))
        max_1h = float(conf.get("max_1h", 20.0))
        max_er = float(conf.get("max_er", 20.0))

        gaps_candidates = []
        for p in filtered:
            if p.get("address") in siap_addrs or p.get("is_honeypot") or p.get("is_wash"):
                continue
            reasons = []
            if p.get("is_rug_risk"):
                reasons.extend(p.get("rug_reasons", []))
            liq = p.get("liq", 0.0)
            vl = p.get("vl", 0.0)
            p5 = p.get("p5", 0.0)
            p1 = p.get("p1", 0.0)
            er = p.get("er", 999.0)

            if liq < min_liq:
                reasons.append(f"Liq &lt; {_usd(min_liq)}")
            if vl < min_vl:
                reasons.append(f"V/L &lt; {min_vl:.1f}x")
            if abs(p5) > max_5m:
                reasons.append(f"5m &gt; {max_5m:.0f}%")
            if abs(p1) > max_1h:
                reasons.append(f"1h &gt; {max_1h:.0f}%")
            if er > max_er:
                reasons.append(f"ER &gt; {max_er:.1f}")

            if reasons:
                p_copy = dict(p)
                p_copy["gap_reasons"] = reasons
                gaps_candidates.append(p_copy)

        gaps = deduplicate_best_tokens(gaps_candidates)
        gaps.sort(key=lambda x: (-x.get("vl", 0.0), -x.get("vol", 0.0)))

    chain_mode = str(conf.get("chain_mode", "BOTH")).upper()
    top_limit = conf.get("top_n_display", 10)

    mode_label = "Solana + Robinhood" if chain_mode == "BOTH" else ("Robinhood" if chain_mode == "RH" else "Solana")
    mode_icon = "🔸🔹" if chain_mode == "BOTH" else ("🔹" if chain_mode == "RH" else "🔸")

    lines = [
        f"{mode_icon} {mode_label}",
    ]

    # 0. 🚀 RUNNER MOMENTUM (Spot Entry)
    rn_list = runner_list or []
    if rn_list:
        lines.append("")
        lines.append("<b>RUNNER</b>")
        for rn in rn_list[:top_limit]:
            sym_link = _tlink(rn)
            vl = rn.get("vl", 0.0)
            mc_str = _usd(rn.get('mcap', 0.0))
            vol5_val = float(rn.get("vol_5m", 0.0) or 0.0)
            vol5_str = _usd(vol5_val)
            tier = html.escape(str(rn.get("runner_label") or "🚀 Runner"))
            p5_val = rn.get("p5", 0.0)
            p5_str = f" (+{p5_val:.0f}% 5m)" if p5_val > 0 else ""
            chain = str(rn.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} │ Vol5m {vol5_str} │ [{tier}]{p5_str}")

        if len(rn_list) > top_limit:
            lines.append(f"<i>...dan {len(rn_list) - top_limit} token Runner lainnya</i>")

    # 0b. 🎮 BONUS STAGE (15M Supertrend Retrace)
    b_list = bonus_list or []
    if b_list:
        lines.append("")
        lines.append("<b>🎮 BONUS STAGE (15M Supertrend Retrace)</b>")
        for b in b_list[:top_limit]:
            sym_link = _tlink(b)
            vl = float(b.get("vl", 0.0) or 0.0)
            mc_str = _usd(b.get("mcap", 0.0))
            dist_pct = float(b.get("st_retrace_pct", 0.0) or 0.0)
            ath_str = _usd(b.get("ath_mcap", 0.0))
            chain = str(b.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} │ ST {dist_pct:+.1f}% (ATH {ath_str})")

        if len(b_list) > top_limit:
            lines.append(f"<i>...dan {len(b_list) - top_limit} token Bonus Stage lainnya</i>")

    if siap_lp:
        lines.append("")
        lines.append("<b>SIAP LP (Chop Sideways)</b>")
        for idx, t in enumerate(siap_lp[:top_limit]):
            sym_link = _tlink(t)
            vl = t.get("vl", 0.0)
            mc_str = _usd(t.get('mcap', 0.0))
            chain = str(t.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str}")

        if len(siap_lp) > top_limit:
            lines.append(f"<i>...dan {len(siap_lp) - top_limit} pool lainnya</i>")

    # 1a-2. 🔴 AKASHI ZONE (Fibonacci Retracement 0.236 - 0.382)
    ak_list = akashi_list or []
    if ak_list:
        lines.append("")
        lines.append("<b>AKASHI ZONE</b>")
        for ak in ak_list[:top_limit]:
            sym_link = _tlink(ak)
            vl = ak.get("vl", 0.0)
            mc_str = _usd(ak.get('mcap', 0.0))
            f_ratio = ak.get("fibo_ratio", 0.0)
            chain = str(ak.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} (Fibo {f_ratio:.3f})")

        if len(ak_list) > top_limit:
            lines.append(f"<i>...dan {len(ak_list) - top_limit} pool Akashi lainnya</i>")

    # 1c. 🍲 SLOW COOK LP (Premium Mid-Long Term)
    sc_list = slow_cook_list or []
    if sc_list:
        lines.append("")
        lines.append("<b>🍲 SLOW COOK LP (Medium-Long Term Premium)</b>")
        for sc in sc_list[:top_limit]:
            sym_link = _tlink(sc)
            vl = sc.get("vl", 0.0)
            mc_str = _usd(sc.get('mcap', 0.0))
            chain = str(sc.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            score = sc.get("slow_cook_score", 0)
            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} │ Skor {score}/100")

        if len(sc_list) > top_limit:
            lines.append(f"<i>...dan {len(sc_list) - top_limit} pool Slow Cook lainnya</i>")

    # 1d. 📉 30% DIP CHOP
    d_list = dip_list or []
    if d_list:
        lines.append("")
        lines.append("<b>📉 30% DIP CHOP (Tight Range at Bottom)</b>")
        for d in d_list[:top_limit]:
            sym_link = _tlink(d)
            vl = d.get("vl", 0.0)
            mc_str = _usd(d.get('mcap', 0.0))
            drop = d.get("ath_drawdown", 0.0)
            chain = str(d.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} │ Drop {drop:.1f}%")

        if len(d_list) > top_limit:
            lines.append(f"<i>...dan {len(d_list) - top_limit} pool 30% Dip lainnya</i>")

    # 1e. SMART LP (Smart Money Concentration)
    sm_list = smart_list or []
    if sm_list:
        lines.append("")
        lines.append("<b>🧠 SMART LP (Smart Money)</b>")
        for sm in sm_list[:top_limit]:
            sym_link = _tlink(sm)
            vl = sm.get("vl", 0.0)
            mc_str = _usd(sm.get('mcap', 0.0))
            sd_count = int(sm.get("smart_degen_count", 0) or 0)
            rn_count = int(sm.get("renowned_count", 0) or 0)
            chain = str(sm.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🔸"
            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} │ SM {sd_count} │ KOL {rn_count}")

        if len(sm_list) > top_limit:
            lines.append(f"<i>...dan {len(sm_list) - top_limit} pool Smart LP lainnya</i>")

    # 2. 5M MOMENTUM
    m5_list = momentum_5m_candidates or []
    if m5_list:
        lines.append("")
        lines.append("<b>5M MOMENTUM</b>")
        for m in m5_list[:6]:
            sym_link = _tlink(m)
            vl = m.get("vl", 0.0)
            mc_str = _usd(m.get("mcap", 0.0))
            vol5_str = _usd(m.get("vol_5m", 0.0))
            p5 = m.get("p5", 0.0)
            p5_str = f"+{p5:.1f}%" if p5 > 0 else f"{p5:.1f}%"
            badge = "🔹" if str(m.get("chain", "SOL")).upper() == "RH" else "🔸"
            b_ratio = round(m.get("buy_ratio", 50.0))
            buys = m.get("buys", 0)
            sells = m.get("sells", 0)
            tx_str = f"🟢 {b_ratio}% Buy"
            if buys > 0 or sells > 0:
                tx_str += f" ({buys}/{sells})"

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str}")
            lines.append(f"  ⚡ 5m {p5_str} │ 🌊 Vol5m {vol5_str} │ {tx_str}")
            lines.append("")

    # 3. BREAK ATH LP
    bath_list = break_ath_candidates or []
    if bath_list:
        lines.append("")
        lines.append("<b>BREAK ATH LP</b>")
        for b in bath_list[:6]:
            sym_link = _tlink(b)
            vl = b.get("vl", 0.0)
            mc_str   = _usd(b.get('mcap', 0.0))
            dur_str  = f"{b.get('duration_mins', 0)}m"
            badge = "🔹" if str(b.get("chain", "SOL")).upper() == "RH" else "🔸"
            pct_sign = "+" if b.get("breakout_pct", 0) >= 0 else ""
            b_ratio  = round(b.get("buy_ratio", 50.0))

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str} ({pct_sign}{b.get('breakout_pct', 0):.0f}%)")
            lines.append(f"  ⏱ {dur_str} │ 🌊 Vol {_usd(b.get('vol', 0.0))} │ 🟢 {b_ratio}% Buy")
            lines.append("")

    # 4. ABSORPTION RADAR
    if absorption:
        lines.append("")
        lines.append("<b>ABSORPTION RADAR</b>")
        for t in absorption[:top_limit]:
            sym_link = _tlink(t)
            vl = t.get("vl", 0.0)
            mc_str = f"MC {_usd(t.get('mcap', 0.0))}"
            status = html.escape(str(t.get("status_label", "")).strip())
            badge = "🔹" if str(t.get("chain", "SOL")).upper() == "RH" else "🔸"

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ {mc_str} │ {status}")
        if len(absorption) > top_limit:
            lines.append(f"<i>...dan {len(absorption) - top_limit} token lainnya</i>")

    # 5. GAPS RADAR (Kompak 1 baris per token)
    if gaps:
        lines.append("")
        lines.append("<b>GAPS RADAR</b>")
        for g in gaps[:5]:
            sym_link = _tlink(g)
            vl = g.get("vl", 0.0)
            mc_str = _usd(g.get('mcap', 0.0))
            badge = "🔹" if str(g.get("chain", "SOL")).upper() == "RH" else "🔸"

            lines.append(f"{badge} {sym_link} │ V/L {vl:.1f}x │ MC {mc_str}")

    report_body = "\n".join(lines).strip()
    return f"{report_body}"


# ================= SCAN ROUTINE =================

def execute_full_scan(conf: dict[str, Any], force: bool = False, override_chain: str = "") -> dict[str, Any]:
    """Single Source of Truth: Menjalankan pemindaian multi-chain GMGN terpadu.
    Mendukung caching atomik (TTL 50s) agar Bot dan Web tidak melakukan double-scrape ke GMGN.
    """
    global GLOBAL_SCAN_CACHE
    chain_mode = (override_chain or conf.get("chain_mode", "BOTH")).upper()

    # 1. Cek cache jika tidak dipaksa (force=False)
    if not force:
        cached = get_cached_scan_data(chain_mode)
        if cached:
            return cached

    with SCAN_LOCK:
        # Cek ulang di dalam lock (double-checked locking)
        if not force:
            cached = get_cached_scan_data(chain_mode)
            if cached:
                return cached

        t0 = time.time()
        source = conf.get("data_source", "GMGN").upper()
        api_key = conf.get("gmgn_api_key", GMGN_KEY)
        min_mcap = float(conf.get("min_mcap", 500000.0))
        max_mcap = float(conf.get("max_mcap", 500000000.0))
        min_age_hours = float(conf.get("min_age_hours", 12.0))
        min_fee_siap_lp = float(conf.get("min_fee_siap_lp", 0.50))
        min_fee_absorb = float(conf.get("min_fee_absorb", 0.50))
        min_liq = float(conf.get("min_liq", 20000.0))
        min_vl = float(conf.get("min_vl", 0.5))
        max_5m = float(conf.get("max_5m", 15.0))
        max_1h = float(conf.get("max_1h", 20.0))
        max_drop_5m = float(conf.get("max_drop_5m", -4.0))
        max_drop_1h = float(conf.get("max_drop_1h", -8.0))
        min_buy_ratio = float(conf.get("min_buy_ratio", 46.0))
        max_ath_drawdown = float(conf.get("max_ath_drawdown", -85.0))
        max_er = float(conf.get("max_er", 20.0))
        interval = int(conf.get("interval_sec", 300))

        print(f"[{get_wib_str()}] [Engine Scan] Memulai pemindaian via {source} (Chain: {chain_mode})...")

        scored_tokens: list[dict[str, Any]] = []

        if source == "GMGN":
            # 1. Fetch Solana
            if chain_mode in ("BOTH", "SOL"):
                raw_sol = fetch_gmgn_tokens("sol", api_key=api_key, limit=50)
                if raw_sol:
                    for r in raw_sol:
                        r["chain"] = "SOL"
                        scored_tokens.append(score_gmgn_token(r, conf))
                    print(f"[{get_wib_str()}] Berhasil mengambil {len(raw_sol)} token dari GMGN Solana.")
                else:
                    print(f"[{get_wib_str()}] GMGN SOL tidak merespons, beralih ke Meteora Fallback...")
                    try:
                        pools_raw = fetch_meteora_dlmm_pools(limit=50, min_tvl=int(min_liq))
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
                                "chain": "SOL",
                                "price": d.get("priceUsd") or 0.0,
                                "liquidity": float(p.get("tvl") or 0.0),
                                "volume": float(p.get("volume", {}).get("1h") or 0.0),
                                "price_change_percent5m": float(d.get("priceChange", {}).get("m5") or 0.0),
                                "price_change_percent1h": float(d.get("priceChange", {}).get("h1") or 0.0),
                                "market_cap": float(d.get("marketCap") or d.get("fdv") or 0.0),
                                "buys": int(d.get("txns", {}).get("h1", {}).get("buys") or 0),
                                "sells": int(d.get("txns", {}).get("h1", {}).get("sells") or 0),
                            }, conf)
                            t_score["meteora"] = f"https://app.meteora.ag/dlmm/{p.get('address')}"
                            t_score["url"] = t_score.get("gmgn") or f"https://gmgn.ai/sol/token/{m_addr}"
                            scored_tokens.append(t_score)
                    except Exception as e:
                        print(f"[Meteora Fallback Error]: {e}", file=sys.stderr)

            # 2. Fetch Robinhood
            if chain_mode in ("BOTH", "RH"):
                if chain_mode == "BOTH":
                    time.sleep(1.2)
                raw_rh = fetch_gmgn_tokens("robinhood", api_key=api_key, limit=50)
                if raw_rh:
                    for r in raw_rh:
                        r["chain"] = "RH"
                        scored_tokens.append(score_gmgn_token(r, conf))
                    print(f"[{get_wib_str()}] Berhasil mengambil {len(raw_rh)} token dari GMGN Robinhood.")
                else:
                    print(f"[{get_wib_str()}] GMGN Robinhood tidak merespons atau kosong.")
        else:
            pools_raw = fetch_meteora_dlmm_pools(limit=50, min_tvl=int(min_liq))
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
                    "chain": "SOL",
                    "price": d.get("priceUsd") or 0.0,
                    "liquidity": float(p.get("tvl") or 0.0),
                    "volume": float(p.get("volume", {}).get("1h") or 0.0),
                    "price_change_percent5m": float(d.get("priceChange", {}).get("m5") or 0.0),
                    "price_change_percent1h": float(d.get("priceChange", {}).get("h1") or 0.0),
                    "market_cap": float(d.get("marketCap") or d.get("fdv") or 0.0),
                    "buys": int(d.get("txns", {}).get("h1", {}).get("buys") or 0),
                    "sells": int(d.get("txns", {}).get("h1", {}).get("sells") or 0),
                }, conf)
                t_score["meteora"] = f"https://app.meteora.ag/dlmm/{p.get('address')}"
                t_score["url"] = t_score.get("gmgn") or f"https://gmgn.ai/sol/token/{m_addr}"
                scored_tokens.append(t_score)

        # Filter dasar (Hard filter: MCap >= min_mcap, Age >= min_age_hours, No Rug/Honeypot/Wash)
        do_filter_stocks = bool(conf.get("filter_stocks", True))
        filtered = [
            p for p in scored_tokens
            if p.get("address") not in QUOTE_MINTS
            and p.get("symbol", "").upper() not in ("SOL", "WSOL", "USDC", "USDT")
            and min_mcap <= p.get("mcap", 0.0) <= max_mcap
            and p.get("age_hours", 0.0) >= min_age_hours
            and not (do_filter_stocks and is_tokenized_stock(p))
            and not p.get("is_honeypot")
            and not p.get("is_wash")
            and not p.get("is_rug_risk")
        ]

        # 1. Siap LP (100% lolos Chop Sideways)
        siap_candidates = [
            p for p in filtered
            if p.get("is_chop")
        ]
        siap_lp = deduplicate_best_tokens(siap_candidates)
        siap_lp.sort(key=lambda x: -x.get("vl", 0.0))

        # 1c. 🍲 SLOW COOK LP (Medium-Long Term Premium)
        slow_cook_candidates = []
        for p in filtered:
            # We want minimum $1M MC and decent V/L for Slow Cook too
            if p.get("vl", 0.0) >= min_vl and p.get("mcap", 0.0) >= 1_000_000.0:
                is_passed, reason, score = slow_cook_scorer.evaluate_slow_cook(p)
                p["slow_cook_score"] = score
                p["slow_cook_reason"] = reason
                if is_passed:
                    slow_cook_candidates.append(p)
        slow_cook_lp = deduplicate_best_tokens(slow_cook_candidates)
        slow_cook_lp.sort(key=lambda x: -x.get("slow_cook_score", 0.0))


        # 1d. 📉 30% DIP CHOP (Healthy Correction & Tight Consolidation)
        dip_candidates = [
            p for p in filtered
            if -45.0 <= p.get("ath_drawdown", 0.0) <= -20.0
            and abs(p.get("p1", 0.0)) <= 4.0
            and abs(p.get("p5", 0.0)) <= 1.5
            and p.get("er", 999.0) <= 10.0
            and p.get("buy_ratio", 0.0) >= 48.0
            and p.get("vl", 0.0) >= 0.4
        ]
        dip_chop = deduplicate_best_tokens(dip_candidates)
        dip_chop.sort(key=lambda x: -x.get("vl", 0.0))

        # 1e. 🧠 SMART LP (Smart Money Concentration)
        smart_lp_candidates = [
            p for p in filtered
            if int(p.get("smart_degen_count", 0) or 0) >= 50
            and int(p.get("renowned_count", 0) or 0) >= 5
            and p.get("vl", 0.0) >= min_vl
            and p.get("er", 999.0) <= max_er
            and p.get("buy_ratio", 50.0) >= min_buy_ratio
            and p.get("ath_drawdown", 0.0) >= max_ath_drawdown
            and not p.get("is_honeypot")
            and not p.get("is_wash")
            and not p.get("is_rug_risk")
        ]
        smart_lp = deduplicate_best_tokens(smart_lp_candidates)
        smart_lp.sort(key=lambda x: -int(x.get("smart_degen_count", 0) or 0))

        # 1g. 🔴 AKASHI ZONE (Fibonacci Retracement 0.236 - 0.382)
        origin_mcap = float(conf.get("akashi_origin_mcap", 55000.0))
        fibo_low = float(conf.get("akashi_fibo_low", 0.236))
        fibo_high = float(conf.get("akashi_fibo_high", 0.382))

        akashi_candidates = []
        for p in filtered:
            ath = float(p.get("ath_mcap", 0.0) or 0.0)
            mc = float(p.get("mcap", 0.0) or 0.0)
            if ath > origin_mcap and mc >= origin_mcap:
                ratio = (mc - origin_mcap) / (ath - origin_mcap)
                if fibo_low <= ratio <= fibo_high and p.get("vl", 0.0) >= min_vl:
                    p_copy = dict(p)
                    p_copy["fibo_ratio"] = round(ratio, 3)
                    akashi_candidates.append(p_copy)

        akashi_zone = deduplicate_best_tokens(akashi_candidates)
        akashi_zone.sort(key=lambda x: -x.get("vl", 0.0))

        # 2. Absorption Radar
        siap_addrs = {p["address"] for p in siap_lp if p.get("address")}
        absorb_candidates = [
            p for p in filtered
            if p.get("address") not in siap_addrs
            and (p.get("micro_state") in ("ABSORPTION", "REACCUMULATION") or p.get("score", 0.0) >= conf.get("min_absorb_score", 65.0))
            and p.get("vl", 0.0) >= min_vl
            and p.get("ath_drawdown", 0.0) >= max_ath_drawdown
            and p.get("buy_ratio", 50.0) >= min_buy_ratio
            and not p.get("is_honeypot")
            and not p.get("is_wash")
            and not p.get("is_rug_risk")
        ]
        absorption = deduplicate_best_tokens(absorb_candidates)
        absorption.sort(key=lambda x: -x.get("vl", 0.0))

        # 3. Break ATH
        try:
            update_ath_cache(scored_tokens)
        except Exception as ath_err:
            print(f"[{get_wib_str()}] [ATH] Update cache error: {ath_err}", file=sys.stderr)
        break_ath = score_break_ath_candidates(scored_tokens, conf)

        # 4. Gaps
        gaps_candidates = []
        for p in scored_tokens:
            if (
                p.get("address") in QUOTE_MINTS
                or p.get("symbol", "").upper() in ("SOL", "WSOL", "USDC", "USDT")
                or not (min_mcap <= p.get("mcap", 0.0) <= max_mcap)
                or p.get("age_hours", 0.0) < min_age_hours
                or (do_filter_stocks and is_tokenized_stock(p))
            ):
                continue
            if p.get("address") in siap_addrs or p.get("is_honeypot") or p.get("is_wash"):
                continue
            reasons = []
            if p.get("is_rug_risk"):
                reasons.extend(p.get("rug_reasons", []))
            liq = p.get("liq", 0.0)
            vl = p.get("vl", 0.0)
            p5 = p.get("p5", 0.0)
            p1 = p.get("p1", 0.0)
            er = p.get("er", 999.0)
            buy_r = p.get("buy_ratio", 50.0)
            ath_dd = p.get("ath_drawdown", 0.0)

            if liq < min_liq:
                reasons.append(f"Liq rendah ({_usd(liq)} &lt; {_usd(min_liq)})")
            if vl < min_vl:
                reasons.append(f"V/L rendah ({vl:.1f}x &lt; {min_vl:.1f}x)")
            if p1 < max_drop_1h:
                reasons.append(f"1h dump ({p1:+.1f}% &lt; {max_drop_1h:.0f}%)")
            elif abs(p1) > max_1h:
                reasons.append(f"1h goyang ({p1:+.1f}% &gt; {max_1h:.0f}%)")
            if p5 < max_drop_5m:
                reasons.append(f"5m dump ({p5:+.1f}% &lt; {max_drop_5m:.0f}%)")
            elif abs(p5) > max_5m:
                reasons.append(f"5m goyang ({p5:+.1f}% &gt; {max_5m:.0f}%)")
            if buy_r < min_buy_ratio:
                reasons.append(f"Buy% rendah ({buy_r:.1f}% &lt; {min_buy_ratio:.0f}%)")
            if ath_dd < max_ath_drawdown:
                reasons.append(f"ATH drop dalam ({ath_dd:.0f}% &lt; {max_ath_drawdown:.0f}%)")
            if er > max_er:
                reasons.append(f"ER melebar ({er:.1f} &gt; {max_er:.1f})")

            if reasons:
                p_copy = dict(p)
                p_copy["gap_reasons"] = reasons
                gaps_candidates.append(p_copy)

        gaps = deduplicate_best_tokens(gaps_candidates)
        gaps.sort(key=lambda x: (-x.get("vl", 0.0), -x.get("vol", 0.0)))

        # 5. 5M Momentum
        momentum_5m = []
        raw_5m_list = []
        try:
            if chain_mode in ("BOTH", "SOL"):
                r_sol = fetch_gmgn_trending_5m("sol", limit=100)
                if r_sol:
                    raw_5m_list.extend(r_sol)
            if chain_mode in ("BOTH", "RH"):
                if chain_mode == "BOTH":
                    time.sleep(0.5)
                r_rh = fetch_gmgn_trending_5m("robinhood", limit=100)
                if r_rh:
                    raw_5m_list.extend(r_rh)
            if raw_5m_list:
                scored_map = {str(t.get("address") or ""): t for t in scored_tokens if t.get("address")}
                momentum_5m = score_5m_momentum_candidates(raw_5m_list, conf, scored_map=scored_map)
        except Exception as e_5m:
            print(f"[WARN] Fetch 5M Momentum error: {e_5m}", file=sys.stderr)
            momentum_5m = []

        # 6. 🚀 RUNNER MOMENTUM (Spot Entry: Established Wave 2 + Fresh Breakouts)
        raw_5m_vol_map = {str(r.get("address") or ""): num(r, "volume") for r in raw_5m_list if r.get("address")}
        all_runner_pool = list(scored_tokens)
        seen_runner_addrs = {str(t.get("address") or "") for t in scored_tokens if t.get("address")}
        for r in raw_5m_list:
            r_addr = str(r.get("address") or "")
            if r_addr and r_addr not in seen_runner_addrs:
                try:
                    sc = score_gmgn_token(r, conf)
                    sc["vol_5m"] = num(r, "volume")
                    sc["vol"] = sc["vol_5m"] * 12.0
                    all_runner_pool.append(sc)
                    seen_runner_addrs.add(r_addr)
                except Exception:
                    pass

        for t in all_runner_pool:
            t_addr = str(t.get("address") or "")
            if t_addr in raw_5m_vol_map:
                t["vol_5m"] = raw_5m_vol_map[t_addr]
            elif not t.get("vol_5m"):
                t["vol_5m"] = round(float(t.get("vol") or 0.0) / 12.0, 2)

        runner_momentum = score_runner_momentum_candidates(all_runner_pool, conf)

        # 7. 🎮 BONUS STAGE (15M Supertrend Retrace)
        bonus_stage = score_bonus_stage_candidates(all_runner_pool, conf)

        all_active_tokens = siap_lp + akashi_zone + runner_momentum + bonus_stage + momentum_5m + absorption + smart_lp
        top_yield = max([p.get("fee_hour", 0.0) for p in all_active_tokens], default=0.0)
        top_vl = max([float(p.get("vl", 0.0) or 0.0) for p in all_active_tokens], default=0.0)

        now_epoch = time.time()
        now_str = get_wib_str(now_epoch, "%H:%M:%S WIB")
        next_boundary = int((now_epoch // interval + 1) * interval)

        result = {
            "ok": True,
            "scanned_at": now_str,
            "scanned_timestamp": int(now_epoch),
            "next_scan_timestamp": next_boundary,
            "total_scanned": len(scored_tokens),
            "chain_mode": chain_mode,
            "scored_tokens": scored_tokens,
            "runner_momentum": runner_momentum,
            "bonus_stage": bonus_stage,
            "siap_lp": siap_lp,
            "akashi_zone": akashi_zone,
            "slow_cook_lp": slow_cook_lp,
            "dip_chop": dip_chop,
            "smart_lp": smart_lp,
            "momentum_5m": momentum_5m,
            "absorption": absorption,
            "break_ath": break_ath,
            "gaps": gaps,
            "counts": {
                "runner": len(runner_momentum),
                "bonus_stage": len(bonus_stage),
                "siap": len(siap_lp),
                "akashi_zone": len(akashi_zone),
                "slow_cook": len(slow_cook_lp),
                "dip_chop": len(dip_chop),
                "smart_lp": len(smart_lp),
                "momentum_5m": len(momentum_5m),
                "absorption": len(absorption),
                "break_ath": len(break_ath),
                "gaps": len(gaps),
                "total": len(scored_tokens),
            },
            "top_yield": top_yield,
            "top_vl": top_vl,
        }

        # 7. Catat event sinyal ke dalam signal-history.json & sol-hp-cache.json
        try:
            record_signal_events(result, scan_ts=int(now_epoch))
        except Exception as e_sig:
            print(f"[WARN] Record signal events error: {e_sig}", file=sys.stderr)

        signal_history = get_aggregated_signal_history()
        result["signal_history"] = signal_history
        result["counts"]["history"] = len(signal_history)

        GLOBAL_SCAN_CACHE = result
        save_scan_cache(result)

        elapsed = time.time() - t0
        print(
            f"[{get_wib_str()}] [Engine Scan] Selesai dalam {elapsed:.2f}s | "
            f"Total: {len(scored_tokens)} | Runner: {len(runner_momentum)} | Bonus: {len(bonus_stage)} | Siap LP: {len(siap_lp)} | Akashi: {len(akashi_zone)} | 5M: {len(momentum_5m)} | Absorption: {len(absorption)} | History 24h: {len(signal_history)}"
        )
        return result


def run_single_scan(conf: dict[str, Any], dry_run: bool = False, override_chain: str = "", force: bool = False) -> None:
    """Melakukan 1 siklus scan menggunakan execute_full_scan, memformat pesan, dan mengirim ke Telegram."""
    source = conf.get("data_source", "GMGN").upper()
    chain_mode = (override_chain or conf.get("chain_mode", "BOTH")).upper()

    scan_conf = dict(conf)
    scan_conf["chain_mode"] = chain_mode

    scan_res = execute_full_scan(scan_conf, force=force, override_chain=chain_mode)

    report_text = generate_report(
        scan_res.get("scored_tokens", []),
        scan_conf,
        source_name=source,
        break_ath_candidates=scan_res.get("break_ath", []),
        momentum_5m_candidates=scan_res.get("momentum_5m", []),
        runner_list=scan_res.get("runner_momentum", []),
        bonus_list=scan_res.get("bonus_stage", []),
        siap_list=scan_res.get("siap_lp", []),
        akashi_list=scan_res.get("akashi_zone", []),
        slow_cook_list=scan_res.get("slow_cook_lp", []),
        dip_list=scan_res.get("dip_chop", []),
        smart_list=scan_res.get("smart_lp", []),
        absorption_list=scan_res.get("absorption", []),
    )

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
        # Lampirkan quick inline toggle buttons di bawah laporan
        active_mode = scan_conf.get("chain_mode", "RH").upper()
        vps_ip = os.getenv("VPS_IP", "43.173.10.245")
        dashboard_url = f"http://{vps_ip}:8771"
        report_buttons = {
            "inline_keyboard": [
                [
                    {"text": f"🔹 RH{' ✅' if active_mode == 'RH' else ''}", "callback_data": "set_chain_rh"},
                    {"text": f"🔸 SOL{' ✅' if active_mode == 'SOL' else ''}", "callback_data": "set_chain_sol"},
                    {"text": f"🔸🔹 Both{' ✅' if active_mode == 'BOTH' else ''}", "callback_data": "set_chain_both"},
                ],
                [
                    {"text": "⚡ Scan Sekarang", "callback_data": "action_scan"},
                    {"text": "🌐 Dashboard HP", "url": dashboard_url},
                ],
            ]
        }
        ok, err = send_telegram_message(token, chat_id, report_text, reply_markup=report_buttons)
        if ok:
            print(f"[{get_wib_str()}] ✅ Berhasil mengirim report ke Telegram chat {chat_id}!")
        else:
            print(f"[{get_wib_str()}] ❌ Gagal kirim Telegram: {err}", file=sys.stderr)


# ================= INTERACTIVE TELEGRAM LISTENER =================

def telegram_poller_thread(conf: dict[str, Any]) -> None:
    """Mendengarkan chat masuk di Telegram secara polling untuk command, callback query toggle, dan scan."""
    token = conf.get("telegram_bot_token", "")
    if not token:
        return
    offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"
    print("🤖 Bot listener aktif (/menu, /scan, toggle inline & keyboard siap digunakan)")

    while True:
        try:
            params = {"offset": offset, "timeout": 20}
            u = f"{poll_url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(u, headers={"User-Agent": "ChopRadarBot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.loads(res.read().decode())
                for item in data.get("result", []):
                    offset = max(offset, item["update_id"] + 1)

                    # 1. Tangani Interaksi Tombol Inline (Callback Query)
                    cq = item.get("callback_query")
                    if cq:
                        cq_id = str(cq.get("id") or "")
                        c_data = str(cq.get("data") or "")
                        c_msg = cq.get("message") or {}
                        c_cid = str(c_msg.get("chat", {}).get("id") or "")
                        c_mid = c_msg.get("message_id")

                        if c_data.startswith("set_chain_"):
                            target_mode = c_data.replace("set_chain_", "").upper()
                            conf["chain_mode"] = target_mode
                            save_persistent_chain_mode(target_mode)

                            if target_mode == "RH":
                                toast = "✅ Mode aktif: 🔹 Robinhood Only"
                            elif target_mode == "SOL":
                                toast = "✅ Mode aktif: 🔸 Solana Only"
                            else:
                                toast = "✅ Mode aktif: 🔸 Solana + 🔹 Robinhood"

                            answer_callback_query(token, cq_id, text=toast)

                            # Perbarui pesan inline
                            new_text = build_chain_menu_text(target_mode, conf.get("interval_sec", 300))
                            new_markup = build_chain_inline_markup(target_mode)
                            edit_telegram_message(token, c_cid, c_mid, new_text, reply_markup=new_markup)

                        elif c_data == "action_scan":
                            cur_mode = conf.get("chain_mode", "RH").upper()
                            answer_callback_query(token, cq_id, text=f"⏳ Memulai scan GMGN ({cur_mode})...")
                            threading.Thread(target=run_single_scan, args=(conf, False, cur_mode, True), daemon=True).start()

                        elif c_data in ("action_history", "cmd_history"):
                            answer_callback_query(token, cq_id, text="📜 Membuka riwayat sinyal 24h...")
                            hist_text = build_telegram_history_report()
                            send_telegram_message(token, c_cid, hist_text)

                        elif c_data in ("action_surge", "cmd_surge"):
                            answer_callback_query(token, cq_id, text="🧠 Memeriksa lonjakan Smart & KOL (1h)...")
                            threading.Thread(target=handle_manual_surge_command, args=(conf, c_cid), daemon=True).start()

                        elif c_data in ("action_refresh", "action_menu"):
                            cur_mode = conf.get("chain_mode", "RH").upper()
                            answer_callback_query(token, cq_id, text="🔄 Menu diperbarui")
                            new_text = build_chain_menu_text(cur_mode, conf.get("interval_sec", 300))
                            new_markup = build_chain_inline_markup(cur_mode)
                            if c_data == "action_menu":
                                send_telegram_message(token, c_cid, new_text, reply_markup=new_markup)
                            else:
                                edit_telegram_message(token, c_cid, c_mid, new_text, reply_markup=new_markup)

                        continue

                    # 2. Tangani Pesan Teks Masuk
                    msg = item.get("message") or {}
                    text = str(msg.get("text") or "").strip().lower()
                    cid = str(msg.get("chat", {}).get("id") or "")

                    if not text:
                        continue

                    # Perintah Scan
                    if text in ("/scan", "⚡ scan sekarang") or text.startswith("/scan"):
                        args_list = text.split()
                        target_chain = ""
                        if len(args_list) > 1:
                            sub = args_list[1].upper()
                            if sub in ("RH", "ROBINHOOD"):
                                target_chain = "RH"
                            elif sub in ("SOL", "SOLANA"):
                                target_chain = "SOL"
                            elif sub in ("BOTH", "ALL"):
                                target_chain = "BOTH"

                        active_chain = (target_chain or conf.get("chain_mode", "RH")).upper()
                        send_telegram_message(token, cid, f"⏳ Sedang memindai data GMGN ({active_chain})...")
                        threading.Thread(target=run_single_scan, args=(conf, False, target_chain, True), daemon=True).start()

                    # Perintah Riwayat Sinyal 24 Jam
                    elif text in ("/history", "/hist", "/log", "/logs", "📜 history 24h", "📜 riwayat sinyal", "history"):
                        hist_text = build_telegram_history_report()
                        send_telegram_message(token, cid, hist_text)

                    # Perintah Lonjakan 1H Smart Money & KOL
                    elif text in ("/surge", "/smart", "/kol", "🧠 1h surge", "1h surge", "surge"):
                        send_telegram_message(token, cid, "⏳ Sedang memindai lonjakan Smart Money & KOL (GMGN 24h)...")
                        threading.Thread(target=handle_manual_surge_command, args=(conf, cid), daemon=True).start()

                    # Quick Button atau Command Ganti Chain
                    elif text in ("🔹 rh only", "rh only", "rh", "/chain rh"):
                        conf["chain_mode"] = "RH"
                        save_persistent_chain_mode("RH")
                        menu_text = build_chain_menu_text("RH", conf.get("interval_sec", 300))
                        send_telegram_message(
                            token,
                            cid,
                            "✅ Mode pemantauan otomatis diubah ke: 🔹 <b>ROBINHOOD Only</b> (Default)\nBot selanjutnya akan memindai chain Robinhood setiap 5 menit.\n\n" + menu_text,
                            reply_markup=build_chain_inline_markup("RH"),
                        )

                    elif text in ("🔸 sol only", "sol only", "sol", "/chain sol"):
                        conf["chain_mode"] = "SOL"
                        save_persistent_chain_mode("SOL")
                        menu_text = build_chain_menu_text("SOL", conf.get("interval_sec", 300))
                        send_telegram_message(
                            token,
                            cid,
                            "✅ Mode pemantauan otomatis diubah ke: 🔸 <b>SOLANA Only</b>\nBot selanjutnya akan memindai chain Solana setiap 5 menit.\n\n" + menu_text,
                            reply_markup=build_chain_inline_markup("SOL"),
                        )

                    elif text in ("🔸🔹 sol + rh", "sol + rh", "both", "/chain both"):
                        conf["chain_mode"] = "BOTH"
                        save_persistent_chain_mode("BOTH")
                        menu_text = build_chain_menu_text("BOTH", conf.get("interval_sec", 300))
                        send_telegram_message(
                            token,
                            cid,
                            "✅ Mode pemantauan otomatis diubah ke: 🔸 <b>SOLANA</b> & 🔹 <b>ROBINHOOD</b> (Dual-Chain)\nBot selanjutnya akan memindai kedua chain setiap 5 menit.\n\n" + menu_text,
                            reply_markup=build_chain_inline_markup("BOTH"),
                        )

                    elif text in ("/menu", "/settings", "⚙️ menu toggle", "⚙️ menu / status", "/chain"):
                        cur_mode = conf.get("chain_mode", "RH").upper()
                        menu_text = build_chain_menu_text(cur_mode, conf.get("interval_sec", 300))
                        send_telegram_message(token, cid, menu_text, reply_markup=build_chain_inline_markup(cur_mode))

                    elif text.startswith("/start") or text.startswith("/help"):
                        cur_mode = conf.get("chain_mode", "RH").upper()
                        mode_desc = "🔹 <b>ROBINHOOD Only</b> (Default)" if cur_mode == "RH" else ("🔸 <b>SOLANA Only</b>" if cur_mode == "SOL" else "🔸 <b>SOLANA</b> & 🔹 <b>ROBINHOOD</b>")
                        help_msg = (
                            "🤖 <b>Chop Radar Bot (Dual-Chain GMGN Edition)</b>\n\n"
                            "Bot otomatis memindai pool & meme coin dari GMGN setiap 5 menit.\n\n"
                            f"<b>Mode Aktif Saat Ini:</b>\n"
                            f"• {mode_desc}\n\n"
                            "<b>Perintah Tersedia:</b>\n"
                            "• <code>/history</code> - Riwayat log sinyal 24 jam terakhir (WIB)\n"
                            "• <code>/menu</code> - Buka toggle menu pengaturan rantai\n"
                            "• <code>/scan</code> - Jalankan pemindaian sesuai mode aktif\n"
                            "• <code>/scan rh</code> - Quick scan khusus Robinhood 🔹\n"
                            "• <code>/scan sol</code> - Quick scan khusus Solana 🔸\n"
                            "• <code>/scan both</code> - Quick scan kedua chain 🔸🔹\n"
                            "• <code>/chain &lt;rh|sol|both&gt;</code> - Ubah mode pemantauan\n"
                            "• <code>/help</code> - Tampilkan pesan bantuan ini\n\n"
                            "<i>Ditenagai oleh GMGN Market API.</i>"
                        )
                        # Kirim persistent reply keyboard di bilah bawah dan inline menu toggle
                        send_telegram_message(token, cid, help_msg, reply_markup=build_chain_reply_keyboard())
                        menu_text = build_chain_menu_text(cur_mode, conf.get("interval_sec", 300))
                        send_telegram_message(token, cid, menu_text, reply_markup=build_chain_inline_markup(cur_mode))
        except Exception:
            time.sleep(5)
        time.sleep(1)


# ================= MAIN RUNNER =================

def main() -> None:
    parser = argparse.ArgumentParser(description="Chop Radar Multi-Chain Telegram Bot (GMGN Edition)")
    parser.add_argument("--dry-run", action="store_true", help="Jalankan 1x scan tanpa kirim Telegram (cetak di terminal)")
    parser.add_argument("--once", action="store_true", help="Jalankan 1x scan dan kirim Telegram, lalu berhenti")
    parser.add_argument("--test-hourly", action="store_true", help="Uji coba 1x evaluasi snapshot 1H Smart Money & KOL surge")
    parser.add_argument("--token", type=str, default="", help="Telegram Bot Token")
    parser.add_argument("--chat-id", type=str, default="", help="Telegram Chat ID")
    parser.add_argument("--interval", type=int, default=0, help="Interval scan dalam detik (default: 300 / 5 menit)")
    parser.add_argument("--source", type=str, default="", help="Data source: GMGN atau METEORA")
    parser.add_argument("--chain", type=str, default="", help="Chain mode: BOTH, SOL, atau RH")
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
    if args.chain:
        conf["chain_mode"] = args.chain.upper()

    print("=" * 65)
    print(f"🚀 Chop Radar ({conf['chain_mode']} - {conf['data_source']}) — Telegram Bot Reporter")
    print(f"🌐 Chain Mode    : {conf['chain_mode']} (🟠 SOL / 🟢 RH)")
    print(f"⏱️ Jadwal Scan    : Setiap {conf['interval_sec'] // 60} menit ({conf['interval_sec']} detik)")
    print(f"💰 Posisi Modal  : ${conf['position_usd']:.0f} USD")
    print(f"🛡️ Target Min TVL: ${conf['min_liq']:,.0f}")
    print(f"📊 Filter Mcap   : ≥ {_usd(conf['min_mcap'])}")
    print(f"📊 Filter Siap LP: V/L ≥ {conf['min_vl']:.1f}x · Buy% ≥ {conf['min_buy_ratio']:.0f}%")
    print(f"⚡ 5M Momentum   : Vol5m ≥ ${conf.get('momentum_5m_min_vol', 200000.0)/1000:.0f}k · Pump Up · Liq ≥ ${conf.get('momentum_5m_min_liq', 10000.0)/1000:.0f}k")
    print(f"🧠 1H Surge Mode : Top 100 GMGN 24h Solana (Pesan Terpisah di Menit :00 WIB)")
    has_token = bool(conf.get("telegram_bot_token") and conf.get("telegram_chat_id"))
    print(f"✈️ Telegram Bot  : {'Siap Terhubung' if has_token else 'Token belum diset (Mode Dry-Run)'}")
    print("=" * 65 + "\n")

    # Muat ATH cache & Signal History dari disk (persist dari sesi sebelumnya)
    load_ath_cache()
    load_signal_history()

    if args.test_hourly:
        print("[TEST] Menjalankan uji evaluasi 1H Smart Money & KOL Surge...")
        run_hourly_smart_kol_tracker(conf, dry_run=True, force=True)
        return

    if args.dry_run or args.once:
        run_single_scan(conf, dry_run=args.dry_run)
        return

    # Jalankan Telegram interactive command listener di background
    if has_token:
        t_poll = threading.Thread(target=telegram_poller_thread, args=(conf,), daemon=True)
        t_poll.start()

    # Pastikan baseline snapshot per jam tersedia di awal
    try:
        ensure_hourly_snapshot_baseline(conf)
    except Exception as e_base:
        print(f"[{get_wib_str()}] [ERROR] Initial hourly snapshot baseline failed: {e_base}", file=sys.stderr)

    # Jalankan scan pertama segera saat bot dinyalakan (dengan exception guard)
    try:
        run_single_scan(conf, dry_run=not has_token)
    except Exception as e_init:
        print(f"[{get_wib_str()}] [ERROR] Initial scan failed: {e_init}", file=sys.stderr)

    # Loop penjadwalan tersinkronisasi kelipatan jam 5 menit (:00, :05, :10, dst)
    global LAST_PROCESSED_SURGE_HOUR
    while True:
        try:
            conf.update(get_config())
            interval = int(conf.get("interval_sec", 300))
            now = time.time()
            next_boundary = int((now // interval + 1) * interval)
            sleep_time = max(1.0, next_boundary - now)
            next_time_str = get_wib_str(next_boundary, "%H:%M:%S WIB")
            print(f"[{get_wib_str()}] Menunggu {sleep_time:.1f}s hingga kelipatan 5 menit berikutnya ({next_time_str})...\n")
            time.sleep(sleep_time)
            conf.update(get_config())
            run_single_scan(conf, dry_run=not has_token)

            # Evaluasi 1H Smart Money & KOL Surge (Khusus Menit :00 Jam Dinding WIB)
            wib_dt = now_wib()
            is_hourly_boundary = (next_boundary % 3600 == 0) or (wib_dt.minute == 0)
            if is_hourly_boundary and LAST_PROCESSED_SURGE_HOUR != wib_dt.hour:
                LAST_PROCESSED_SURGE_HOUR = wib_dt.hour
                try:
                    run_hourly_smart_kol_tracker(conf, dry_run=not has_token)
                except Exception as e_surge:
                    print(f"[{get_wib_str()}] [ERROR] Hourly surge check failed: {e_surge}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[!] Bot dihentikan oleh pengguna.")
            break
        except Exception as e_loop:
            print(f"[{get_wib_str()}] [ERROR] Scheduled scan failed: {e_loop}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
