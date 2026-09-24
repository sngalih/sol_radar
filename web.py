#!/usr/bin/env python3
"""Chop LP Terminal — Mobile Web Dashboard (Port 8771).
100% Parameter & Logic Parity with Telegram Bot (bot_sol_lp.py).
Multi-Chain: Solana + Robinhood via GMGN Open API.
Mobile-First SPA: Optimized specifically for iPhone Safari & smartphone screens.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure stdout handles UTF-8 smoothly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Import shared data fetching, config, and scoring engine directly from bot_sol_lp
try:
    import bot_sol_lp
except ImportError as e:
    print(f"[FATAL] Gagal mengimpor bot_sol_lp: {e}", file=sys.stderr)
    sys.exit(1)

HOST = "0.0.0.0"
PORT = 8771
FILTER_FILE = BASE_DIR / "sol-hp-filters.json"
CACHE_FILE = BASE_DIR / "sol-hp-cache.json"

# Lock for thread-safe state access
state_lock = threading.Lock()

# Global server state
app_state: dict[str, Any] = {
    "scanning": False,
    "scanned_at": None,
    "scanned_timestamp": 0,
    "next_scan_timestamp": 0,
    "total_scanned": 0,
    "siap_lp": [],
    "momentum_5m": [],
    "absorption": [],
    "break_ath": [],
    "gaps": [],
    "counts": {"siap": 0, "momentum_5m": 0, "absorption": 0, "break_ath": 0, "gaps": 0, "total": 0},
    "top_yield": 0.0,
    "filters": {},
}


def load_persistent_filters() -> dict[str, Any]:
    """Memuat filter dari sol-hp-filters.json atau gunakan default dari bot_sol_lp."""
    conf = dict(bot_sol_lp.get_config())
    # Ensure mobile-specific defaults match bot
    defaults = {
        "position_usd": 100.0,
        "min_fee_siap_lp": 1.0,
        "min_fee_break_ath": 1.0,
        "momentum_5m_min_vol": 100000.0,
        "momentum_5m_min_liq": 10000.0,
        "momentum_5m_min_fee": 1.0,
        "min_liq": 20000.0,
        "min_mcap": 500000.0,
        "max_mcap": 500000000.0,
        "min_vl": 2.0,
        "max_5m": 15.0,
        "max_1h": 80.0,
        "max_er": 20.0,
        "min_absorb_score": 65.0,
        "interval_sec": 300,
        "chain_mode": "RH",
    }
    for k, v in defaults.items():
        if k not in conf or conf[k] is None:
            conf[k] = v

    if FILTER_FILE.exists():
        try:
            saved = json.loads(FILTER_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                # Mapping filter lama jika ada
                if "position" in saved and "position_usd" not in saved:
                    saved["position_usd"] = saved["position"]
                if "interval" in saved and "interval_sec" not in saved:
                    saved["interval_sec"] = saved["interval"]
                if "min_absorb_mcap" in saved and ("min_mcap" not in saved or saved.get("min_mcap") == 0):
                    saved["min_mcap"] = saved["min_absorb_mcap"]
                if saved.get("min_fee_siap_lp") == 3.0:
                    saved["min_fee_siap_lp"] = 1.0
                if saved.get("min_fee_break_ath") == 3.0:
                    saved["min_fee_break_ath"] = 1.0

                for k, v in saved.items():
                    if k in conf and v is not None:
                        try:
                            conf[k] = type(conf[k])(v)
                        except (ValueError, TypeError):
                            conf[k] = v
        except Exception as e:
            print(f"[WARN] Gagal membaca {FILTER_FILE.name}: {e}", file=sys.stderr)

    return conf


def save_persistent_filters(new_filters: dict[str, Any]) -> None:
    """Menyimpan filter ke sol-hp-filters.json secara persisten."""
    try:
        FILTER_FILE.write_text(json.dumps(new_filters, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan filter: {e}", file=sys.stderr)


def perform_scan() -> None:
    """Eksekusi pemindaian token SOL + RH menggunakan engine bot_sol_lp."""
    global app_state

    with state_lock:
        if app_state["scanning"]:
            return
        app_state["scanning"] = True

    t0 = time.time()
    conf = dict(app_state["filters"])
    api_key = conf.get("gmgn_api_key", bot_sol_lp.GMGN_KEY)
    chain_mode = str(conf.get("chain_mode", "BOTH")).upper()
    min_mcap = float(conf.get("min_mcap", 500000.0))
    max_mcap = float(conf.get("max_mcap", 500000000.0))
    min_fee_siap_lp = float(conf.get("min_fee_siap_lp", 1.0))
    min_liq = float(conf.get("min_liq", 20000.0))
    min_vl = float(conf.get("min_vl", 2.0))
    max_5m = float(conf.get("max_5m", 15.0))
    max_1h = float(conf.get("max_1h", 80.0))
    max_er = float(conf.get("max_er", 20.0))

    scored_tokens: list[dict[str, Any]] = []

    try:
        # 1. Fetch Solana
        if chain_mode in ("BOTH", "SOL"):
            raw_sol = bot_sol_lp.fetch_gmgn_tokens("sol", api_key=api_key, limit=50)
            if raw_sol:
                for r in raw_sol:
                    r["chain"] = "SOL"
                    scored_tokens.append(bot_sol_lp.score_gmgn_token(r, conf))
            else:
                # Fallback ke Meteora DLMM jika GMGN Solana kosong
                try:
                    p_raw = bot_sol_lp.fetch_meteora_dlmm_pools(limit=40, min_tvl=int(min_liq))
                    m_addrs = [
                        (p.get("token_x") or {}).get("address", "") if (p.get("token_x") or {}).get("address", "") not in bot_sol_lp.QUOTE_MINTS else (p.get("token_y") or {}).get("address", "")
                        for p in p_raw
                    ]
                    dex_data = bot_sol_lp.fetch_dexscreener_batch(m_addrs)
                    for p in p_raw:
                        tx = (p.get("token_x") or {}).get("address", "")
                        ty = (p.get("token_y") or {}).get("address", "")
                        m_addr = tx if tx not in bot_sol_lp.QUOTE_MINTS else ty
                        d = dex_data.get(m_addr, {})
                        t_score = bot_sol_lp.score_gmgn_token({
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
                        t_score["url"] = f"https://app.meteora.ag/dlmm/{p.get('address')}"
                        scored_tokens.append(t_score)
                except Exception as e:
                    print(f"[Meteora Fallback Error]: {e}", file=sys.stderr)

        # 2. Fetch Robinhood (jika BOTH atau RH)
        if chain_mode in ("BOTH", "RH"):
            if chain_mode == "BOTH":
                time.sleep(1.2)  # Jeda aman anti-rate limit
            raw_rh = bot_sol_lp.fetch_gmgn_tokens("robinhood", api_key=api_key, limit=50)
            if raw_rh:
                for r in raw_rh:
                    r["chain"] = "RH"
                    scored_tokens.append(bot_sol_lp.score_gmgn_token(r, conf))

        # Filter dasar (Exclude native tokens, batasi rentang MCAP, dan block tokenized stocks)
        do_filter_stocks = bool(conf.get("filter_stocks", True))
        filtered = [
            p for p in scored_tokens
            if p.get("address") not in bot_sol_lp.QUOTE_MINTS
            and p.get("symbol", "").upper() not in ("SOL", "WSOL", "USDC", "USDT")
            and min_mcap <= p.get("mcap", 0.0) <= max_mcap
            and not (do_filter_stocks and bot_sol_lp.is_tokenized_stock(p))
        ]

        min_fee_absorb = float(conf.get("min_fee_absorb", 0.50))

        # 1. Kategori Siap LP (100% lolos Chop Sideways, Fee >= min_fee_siap_lp)
        siap_candidates = [
            p for p in filtered
            if p.get("is_chop") and p.get("fee_hour", 0.0) >= min_fee_siap_lp
        ]
        siap_lp = bot_sol_lp.deduplicate_best_tokens(siap_candidates)
        siap_lp.sort(key=lambda x: -x.get("fee_hour", 0.0))

        # 2. Kategori Absorption Radar (Microstate ABSORPTION / REACCUMULATION atau score tinggi)
        # Opsi B: fee_hour >= min_fee_absorb agar koin "mati yield" seperti saham tidak nongol
        absorb_candidates = [
            p for p in filtered
            if (p.get("micro_state") in ("ABSORPTION", "REACCUMULATION") or p.get("score", 0.0) >= conf.get("min_absorb_score", 65.0))
            and p.get("fee_hour", 0.0) >= min_fee_absorb
        ]
        absorption = bot_sol_lp.deduplicate_best_tokens(absorb_candidates)
        absorption.sort(key=lambda x: -x.get("fee_hour", 0.0))

        # 3. Kategori Break ATH LP (15m+ Confirmed)
        try:
            bot_sol_lp.update_ath_cache(scored_tokens)
        except Exception:
            pass
        break_ath = bot_sol_lp.score_break_ath_candidates(scored_tokens, conf)

        # 4. Kategori Gaps (Token dalam rentang Mcap yang belum 100% lolos kriteria CHOP Siap LP)
        siap_addrs = {p["address"] for p in siap_lp if p.get("address")}
        gaps_candidates = []
        for p in filtered:
            if p.get("address") in siap_addrs:
                continue
            reasons = []
            liq = p.get("liq", 0.0)
            vl = p.get("vl", 0.0)
            p5 = p.get("p5", 0.0)
            p1 = p.get("p1", 0.0)
            er = p.get("er", 999.0)
            fee = p.get("fee_hour", 0.0)

            if liq < min_liq:
                reasons.append(f"Liq rendah ({bot_sol_lp._usd(liq)} < {bot_sol_lp._usd(min_liq)})")
            if vl < min_vl:
                reasons.append(f"V/L rendah ({vl:.1f}x < {min_vl:.1f}x)")
            if abs(p5) > max_5m:
                reasons.append(f"5m goyang ({p5:+.1f}% > {max_5m:.0f}%)")
            if abs(p1) > max_1h:
                reasons.append(f"1h goyang ({p1:+.1f}% > {max_1h:.0f}%)")
            if er > max_er:
                reasons.append(f"ER melebar ({er:.1f} > {max_er:.1f})")
            if fee < min_fee_siap_lp:
                reasons.append(f"Fee rendah (${fee:.2f} < ${min_fee_siap_lp:.2f}/h)")

            p_copy = dict(p)
            p_copy["gap_reasons"] = reasons if reasons else ["Belum memenuhi kriteria"]
            gaps_candidates.append(p_copy)

        gaps = bot_sol_lp.deduplicate_best_tokens(gaps_candidates)
        gaps.sort(key=lambda x: (-x.get("fee_hour", 0.0), -x.get("vol", 0.0)))

        # 5. Kategori ⚡ 5M Momentum (Vol 5m > $100k, Pump Up, Liq >= $10k)
        momentum_5m = []
        try:
            raw_5m_list = []
            if chain_mode in ("BOTH", "SOL"):
                r_sol = bot_sol_lp.fetch_gmgn_trending_5m("sol", limit=100)
                if r_sol:
                    raw_5m_list.extend(r_sol)
            if chain_mode in ("BOTH", "RH"):
                if chain_mode == "BOTH":
                    time.sleep(0.5)
                r_rh = bot_sol_lp.fetch_gmgn_trending_5m("robinhood", limit=100)
                if r_rh:
                    raw_5m_list.extend(r_rh)

            if raw_5m_list:
                momentum_5m = bot_sol_lp.score_5m_momentum_candidates(raw_5m_list, conf)
        except Exception as e_5m:
            print(f"[WARN] Fetch 5M Momentum error: {e_5m}", file=sys.stderr)
            momentum_5m = []

        all_active_for_yield = siap_lp + momentum_5m + absorption
        top_yield = max([p.get("fee_hour", 0.0) for p in all_active_for_yield], default=0.0)

        now = datetime.now()
        now_str = now.strftime("%H:%M:%S")
        interval = int(conf.get("interval_sec", 300))

        with state_lock:
            app_state["scanning"] = False
            app_state["scanned_at"] = now_str
            app_state["scanned_timestamp"] = int(time.time())
            app_state["next_scan_timestamp"] = int(time.time()) + interval
            app_state["total_scanned"] = len(scored_tokens)
            app_state["siap_lp"] = siap_lp
            app_state["momentum_5m"] = momentum_5m
            app_state["absorption"] = absorption
            app_state["break_ath"] = break_ath
            app_state["gaps"] = gaps[:40]  # Limit agar tidak membebani browser HP
            app_state["counts"] = {
                "siap": len(siap_lp),
                "momentum_5m": len(momentum_5m),
                "absorption": len(absorption),
                "break_ath": len(break_ath),
                "gaps": len(gaps),
                "total": len(scored_tokens),
            }
            app_state["top_yield"] = top_yield

        # Simpan cache ringan ke disk
        try:
            CACHE_FILE.write_text(
                json.dumps({
                    "scanned_at": now_str,
                    "counts": app_state["counts"],
                    "top_yield": top_yield,
                    "siap_lp": siap_lp[:20],
                    "absorption": absorption[:20],
                }, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

        print(
            f"[{time.strftime('%H:%M:%S')}] [Web Scan] Berhasil dalam {time.time() - t0:.2f}s | "
            f"Total: {len(scored_tokens)} | Siap LP: {len(siap_lp)} | Absorption: {len(absorption)}"
        )

    except Exception as e:
        print(f"[Web Scan Error]: {e}", file=sys.stderr)
        with state_lock:
            app_state["scanning"] = False


def background_scanner_worker() -> None:
    """Background daemon yang mengeksekusi pemindaian berkala."""
    print(f"🚀 [Web Scanner] Background worker aktif (Interval: {app_state['filters'].get('interval_sec', 300)}s)")
    # Langsung jalankan pemindaian awal saat startup
    perform_scan()

    while True:
        try:
            time.sleep(1)
            now_ts = int(time.time())
            next_ts = app_state.get("next_scan_timestamp", 0)
            if next_ts > 0 and now_ts >= next_ts and not app_state.get("scanning", False):
                perform_scan()
        except Exception as e:
            print(f"[Worker Error]: {e}", file=sys.stderr)
            time.sleep(5)


# ================= MOBILE-FIRST HTML / CSS / JS TEMPLATE =================

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#080c14">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>⚡ Chop LP Radar</title>
  <style>
    /* ===== MODERN DESIGN TOKENS ===== */
    :root {
      --bg: #080c14;
      --card-bg: #0f172a;
      --card-inner: #0b1120;
      --card-border: #1e293b;
      --card-hover: #162238;
      --text-main: #f8fafc;
      --text-sub: #94a3b8;
      --text-dim: #64748b;
      --green: #10b981;
      --green-light: #34d399;
      --green-bg: rgba(16, 185, 129, 0.12);
      --green-glow: rgba(16, 185, 129, 0.25);
      --sol: #f59e0b;
      --sol-light: #fbbf24;
      --sol-bg: rgba(245, 158, 11, 0.12);
      --sol-glow: rgba(245, 158, 11, 0.25);
      --rh: #3b82f6;
      --rh-light: #60a5fa;
      --rh-bg: rgba(59, 130, 246, 0.12);
      --rh-glow: rgba(59, 130, 246, 0.25);
      --blue: #3b82f6;
      --blue-bg: rgba(59, 130, 246, 0.12);
      --red: #f43f5e;
      --red-bg: rgba(244, 63, 94, 0.12);
      --yellow: #f59e0b;
      --yellow-bg: rgba(245, 158, 11, 0.12);
      --cyan: #06b6d4;
      --cyan-light: #38bdf8;
      --cyan-bg: rgba(6, 182, 212, 0.12);
      --cyan-glow: rgba(6, 182, 212, 0.25);
      --safe-top: env(safe-area-inset-top, 0px);
      --safe-bottom: env(safe-area-inset-bottom, 0px);
      --container-max: 1140px;
      --radius-card: 16px;
      --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "SF Mono", "Roboto Mono", ui-monospace, Menlo, monospace;
    }

    html { scroll-behavior: smooth; }

    *, *::before, *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
    }

    body {
      background-color: var(--bg);
      color: var(--text-main);
      font-family: var(--font-sans);
      font-size: 14px;
      line-height: 1.45;
      padding-top: var(--safe-top);
      padding-bottom: calc(var(--safe-bottom) + 32px);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    /* ===== STICKY HEADER ===== */
    .app-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(8, 12, 20, 0.94);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 1px solid var(--card-border);
      padding: 10px 16px;
    }

    .header-inner {
      max-width: var(--container-max);
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .brand-title {
      font-weight: 800;
      font-size: 17px;
      letter-spacing: -0.4px;
      background: linear-gradient(135deg, #ffffff 40%, #94a3b8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }

    .brand-badge {
      font-size: 10px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 6px;
      background: var(--green-bg);
      color: var(--green-light);
      border: 1px solid rgba(16, 185, 129, 0.3);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      font-family: var(--font-mono);
    }

    .status-pulse {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 600;
      color: var(--green-light);
      background: var(--green-bg);
      padding: 4px 10px;
      border-radius: 20px;
      border: 1px solid rgba(16, 185, 129, 0.25);
      font-family: var(--font-mono);
    }

    .pulse-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--green);
      animation: pulse 1.8s infinite;
      flex-shrink: 0;
    }

    .status-pulse.scanning {
      color: var(--yellow);
      background: var(--yellow-bg);
      border-color: rgba(245, 158, 11, 0.3);
    }
    .status-pulse.scanning .pulse-dot { background: var(--yellow); }

    @keyframes pulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.35; transform: scale(1.2); }
      100% { opacity: 1; transform: scale(1); }
    }

    .header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .btn-icon {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      border-radius: 10px;
      padding: 6px 11px;
      font-size: 12px;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
    }
    .btn-icon:hover { background: var(--card-hover); border-color: rgba(255,255,255,0.15); }
    .btn-icon:active { transform: scale(0.95); }

    .btn-scan {
      background: linear-gradient(135deg, #10b981, #059669);
      color: #ffffff;
      border: none;
      box-shadow: 0 2px 8px rgba(16, 185, 129, 0.35);
      font-family: var(--font-mono);
      font-weight: 700;
    }
    .btn-scan:hover { box-shadow: 0 4px 14px rgba(16, 185, 129, 0.5); }

    /* ===== CHAIN SEGMENTED SWITCHER ===== */
    .chain-segmented {
      display: flex;
      background: rgba(11, 17, 32, 0.9);
      padding: 3px;
      border-radius: 11px;
      border: 1px solid var(--card-border);
      gap: 3px;
    }

    .chain-tab {
      flex: 1;
      text-align: center;
      padding: 6px 6px;
      font-size: 11px;
      font-weight: 700;
      color: var(--text-sub);
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      user-select: none;
      white-space: nowrap;
      font-family: var(--font-mono);
    }
    .chain-tab:hover { color: var(--text-main); }

    .chain-tab.active {
      background: var(--card-bg);
      color: var(--text-main);
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
      border: 1px solid rgba(255,255,255,0.1);
    }
    .chain-tab.active[data-chain="sol"] { color: var(--sol-light); border-color: rgba(245,158,11,0.4); background: rgba(245,158,11,0.1); }
    .chain-tab.active[data-chain="rh"]  { color: var(--rh-light);  border-color: rgba(59,130,246,0.4); background: rgba(59,130,246,0.1); }

    /* ===== MAIN CONTAINER ===== */
    .container {
      padding: 14px 16px;
      max-width: var(--container-max);
      margin: 0 auto;
    }

    /* ===== KPI GRID ===== */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      margin-bottom: 14px;
    }

    .kpi-row-bottom {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }

    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 10px 10px;
      text-align: center;
      transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
      position: relative;
      overflow: hidden;
    }
    .kpi-card:hover {
      border-color: rgba(255,255,255,0.15);
      box-shadow: 0 4px 16px rgba(0,0,0,0.3);
      transform: translateY(-1px);
    }

    .kpi-label {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-sub);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
    }

    .kpi-val {
      font-size: 17px;
      font-weight: 800;
      color: var(--text-main);
      font-family: var(--font-mono);
      letter-spacing: -0.3px;
    }
    .kpi-val.green { color: var(--green-light); }
    .kpi-val.yellow { color: var(--yellow); }
    .kpi-val.cyan { color: var(--cyan-light); }

    /* ===== CATEGORY TABS & SEARCH BAR ===== */
    .controls-strip {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 14px;
    }

    .cat-tabs {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
      padding-bottom: 2px;
    }
    .cat-tabs::-webkit-scrollbar { display: none; }

    .cat-tab {
      flex: 1;
      min-width: 82px;
      padding: 9px 8px;
      border-radius: 12px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      font-size: 11px;
      font-weight: 700;
      text-align: center;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 3px;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      user-select: none;
      white-space: nowrap;
    }
    .cat-tab:hover { background: var(--card-hover); color: var(--text-main); }

    .cat-tab .badge-count {
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 10px;
      background: rgba(255,255,255,0.08);
      color: var(--text-main);
      font-weight: 700;
      font-family: var(--font-mono);
    }

    .cat-tab.active { background: #132238; border-color: var(--green); color: var(--green-light); box-shadow: 0 0 12px var(--green-glow); }
    .cat-tab.active[data-cat="momentum_5m"] { border-color: #eab308; color: #fde047; box-shadow: 0 0 12px rgba(234, 179, 8, 0.35); }
    .cat-tab.active[data-cat="absorption"] { border-color: var(--blue); color: var(--rh-light); box-shadow: 0 0 12px var(--rh-glow); }
    .cat-tab.active[data-cat="break_ath"]  { border-color: var(--cyan); color: var(--cyan-light); box-shadow: 0 0 12px var(--cyan-glow); }
    .cat-tab.active[data-cat="gaps"]       { border-color: var(--yellow); color: var(--sol-light); box-shadow: 0 0 12px var(--sol-glow); }

    .cat-tab.active .badge-count                          { background: rgba(16,185,129,0.25); color: var(--green-light); }
    .cat-tab.active[data-cat="momentum_5m"] .badge-count  { background: rgba(234, 179, 8, 0.25); color: #fde047; }
    .cat-tab.active[data-cat="absorption"] .badge-count  { background: rgba(59,130,246,0.25); color: var(--rh-light); }
    .cat-tab.active[data-cat="break_ath"]  .badge-count  { background: rgba(6,182,212,0.25); color: var(--cyan-light); }
    .cat-tab.active[data-cat="gaps"]       .badge-count  { background: rgba(245,158,11,0.25); color: var(--sol-light); }

    /* Controls Right: Search + View Switcher */
    .controls-right {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
    }

    /* Quick Search Input */
    .search-wrap {
      position: relative;
      display: flex;
      align-items: center;
      flex: 1;
    }
    .search-icon {
      position: absolute;
      left: 12px;
      font-size: 13px;
      color: var(--text-dim);
      pointer-events: none;
    }
    .search-input {
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 11px;
      padding: 8px 32px 8px 34px;
      font-size: 12.5px;
      color: #ffffff;
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .search-input:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(59,130,246,0.15);
    }
    .search-clear {
      position: absolute;
      right: 10px;
      background: transparent;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 13px;
      padding: 2px 6px;
      display: none;
    }
    .search-clear.visible { display: block; }

    /* View Switcher: Cards vs Table */
    .view-toggle {
      display: inline-flex;
      background: rgba(11, 17, 32, 0.9);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 2px;
      gap: 2px;
      flex-shrink: 0;
    }
    .btn-view {
      background: transparent;
      border: none;
      color: var(--text-dim);
      padding: 6px 10px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: var(--font-mono);
      transition: all 0.15s;
      user-select: none;
    }
    .btn-view:hover { color: var(--text-main); }
    .btn-view.active {
      background: var(--card-bg);
      color: #ffffff;
      box-shadow: 0 1px 4px rgba(0,0,0,0.5);
      border: 1px solid rgba(255,255,255,0.12);
    }

    /* ===== TOKEN AVATAR (ArcTools Monogram / Image) ===== */
    .tok-avatar-wrap {
      width: 38px;
      height: 38px;
      border-radius: 10px;
      overflow: hidden;
      flex-shrink: 0;
      background: #0e1118;
      border: 1px solid rgba(255, 255, 255, 0.12);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: relative;
    }
    .tok-avatar-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .tok-avatar-mono {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--font-mono);
      font-weight: 800;
      font-size: 13px;
      letter-spacing: 0.03em;
      color: rgba(255, 255, 255, 0.95);
      text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
    }

    /* ===== RANK BADGES ===== */
    .rank-badge {
      font-size: 10px;
      font-weight: 800;
      font-family: var(--font-mono);
      padding: 2px 6px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      line-height: 1.2;
      flex-shrink: 0;
    }
    .rank-gold {
      background: rgba(245, 196, 81, 0.16);
      color: #f5c542;
      border: 1px solid rgba(245, 196, 81, 0.45);
      box-shadow: 0 0 8px rgba(245, 196, 81, 0.2);
    }
    .rank-silver {
      background: rgba(226, 232, 240, 0.14);
      color: #e2e8f0;
      border: 1px solid rgba(226, 232, 240, 0.35);
    }
    .rank-bronze {
      background: rgba(217, 119, 6, 0.14);
      color: #fb923c;
      border: 1px solid rgba(217, 119, 6, 0.35);
    }
    .rank-dim {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text-dim);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }

    /* ===== MICRO METADATA (Age & Venue) ===== */
    .meta-age {
      font-size: 10px;
      font-weight: 700;
      font-family: var(--font-mono);
      color: var(--text-dim);
    }
    .meta-age.fresh {
      color: var(--green-light);
    }
    .venue-pill {
      font-size: 9px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-sub);
      border: 1px solid rgba(255, 255, 255, 0.08);
      font-family: var(--font-mono);
    }

    /* Inline CA Copy */
    .btn-copy-inline {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: var(--text-sub);
      border-radius: 5px;
      padding: 1px 6px;
      font-size: 10.5px;
      font-family: var(--font-mono);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      transition: all 0.15s;
    }
    .btn-copy-inline:hover {
      background: rgba(255, 255, 255, 0.12);
      color: #ffffff;
      border-color: rgba(255, 255, 255, 0.2);
    }
    .btn-copy-inline.copied {
      background: var(--green-bg);
      color: var(--green-light);
      border-color: rgba(16, 185, 129, 0.4);
    }

    /* ===== ORDER FLOW BAR (ArcTools Trade Split) ===== */
    .orderflow-wrap {
      background: rgba(11, 17, 32, 0.75);
      border-radius: 8px;
      padding: 6px 9px;
      margin-bottom: 9px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .orderflow-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      margin-bottom: 5px;
      font-family: var(--font-mono);
    }
    .of-label {
      color: var(--text-dim);
      font-weight: 700;
      text-transform: uppercase;
      font-size: 9.5px;
      letter-spacing: 0.3px;
    }
    .of-ratio {
      font-weight: 700;
      color: var(--text-sub);
      font-size: 10.5px;
    }
    .of-buy-txt {
      color: var(--green-light);
      font-weight: 800;
    }
    .orderflow-bar {
      height: 5px;
      background: rgba(244, 63, 94, 0.25);
      border-radius: 3px;
      overflow: hidden;
      display: flex;
    }
    .of-fill-buy {
      height: 100%;
      background: linear-gradient(90deg, #10b981, #34d399);
      transition: width 0.3s ease;
    }
    .of-fill-sell {
      height: 100%;
      background: linear-gradient(90deg, #f43f5e, #e11d48);
      transition: width 0.3s ease;
    }

    /* ===== SAFETY AUDIT BADGE ===== */
    .safety-block {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-family: var(--font-mono);
      font-size: 10.5px;
      color: var(--text-sub);
    }
    .safety-pill {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 2px 6px;
      border-radius: 5px;
      font-weight: 800;
      font-size: 11px;
      line-height: 1;
      font-family: var(--font-mono);
    }
    .safety-A { background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .safety-B { background: rgba(59, 130, 246, 0.18); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
    .safety-C { background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
    .safety-D { background: rgba(244, 63, 94, 0.18);  color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.4); }
    .safety-sub { color: var(--text-dim); font-size: 10.5px; }

    /* ===== CARD LIST ===== */
    .card-list {
      display: grid;
      grid-template-columns: 1fr;
      gap: 12px;
    }

    /* ===== TOKEN CARD ===== */
    .token-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-card);
      padding: 13px 14px;
      position: relative;
      transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.15s ease;
      overflow: hidden;
    }
    .token-card:hover {
      border-color: rgba(255,255,255,0.18);
      box-shadow: 0 8px 28px rgba(0,0,0,0.4);
      transform: translateY(-1px);
    }
    .token-card.sol-card {
      border-left: 3px solid var(--sol);
      background: linear-gradient(180deg, rgba(245,158,11,0.03) 0%, var(--card-bg) 60px);
    }
    .token-card.rh-card  {
      border-left: 3px solid var(--rh);
      background: linear-gradient(180deg, rgba(59,130,246,0.03) 0%, var(--card-bg) 60px);
    }
    .token-card.ath-card {
      border-left: 3px solid var(--cyan);
      background: linear-gradient(180deg, rgba(6,182,212,0.04) 0%, var(--card-bg) 60px);
    }
    .token-card.momentum-card {
      border-left: 3px solid #eab308;
      background: linear-gradient(180deg, rgba(234,179,8,0.04) 0%, var(--card-bg) 60px);
    }
    .token-card.rank-1 { box-shadow: 0 0 0 1px rgba(245, 196, 81, 0.22); }

    /* Card Top Row */
    .card-row-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 10px;
      gap: 8px;
    }

    .token-info-left {
      display: flex;
      align-items: center;
      gap: 9px;
      min-width: 0;
    }

    .chain-pill {
      font-size: 10px;
      font-weight: 800;
      padding: 2px 6px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      flex-shrink: 0;
      font-family: var(--font-mono);
    }
    .chain-pill.sol { background: var(--sol-bg); color: var(--sol-light); border: 1px solid rgba(245,158,11,0.35); }
    .chain-pill.rh  { background: var(--rh-bg);  color: var(--rh-light);  border: 1px solid rgba(59,130,246,0.35); }

    .token-name-block { min-width: 0; }
    .symbol-row {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .token-symbol {
      font-size: 16px;
      font-weight: 800;
      letter-spacing: -0.2px;
      color: #ffffff;
      line-height: 1.2;
    }

    .token-sub-row {
      display: flex;
      align-items: center;
      gap: 5px;
      margin-top: 3px;
      font-size: 11px;
      color: var(--text-sub);
      flex-wrap: wrap;
    }
    .token-price {
      font-family: var(--font-mono);
      font-weight: 700;
      color: #cbd5e1;
    }
    .token-name {
      max-width: 120px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--text-dim);
    }

    .token-fee-right { text-align: right; flex-shrink: 0; }
    .fee-hour {
      font-size: 19px;
      font-weight: 800;
      color: var(--green-light);
      letter-spacing: -0.3px;
      font-family: var(--font-mono);
      line-height: 1.2;
    }
    .fee-day {
      font-size: 10.5px;
      color: var(--text-sub);
      font-weight: 600;
      font-family: var(--font-mono);
      margin-top: 1px;
    }

    /* Metrics Grid */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: var(--card-inner);
      border-radius: 10px;
      padding: 8px 6px;
      margin-bottom: 9px;
      border: 1px solid rgba(255,255,255,0.05);
      gap: 4px;
    }

    .metric-cell { text-align: center; }
    .m-label {
      font-size: 9.5px;
      font-weight: 700;
      color: var(--text-dim);
      text-transform: uppercase;
      margin-bottom: 3px;
      letter-spacing: 0.3px;
    }
    .m-val {
      font-size: 12.5px;
      font-weight: 700;
      color: var(--text-main);
      font-family: var(--font-mono);
    }

    .er-badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 5px;
      font-weight: 800;
      font-size: 10.5px;
      font-family: var(--font-mono);
    }
    .er-prime { background: rgba(16,185,129,0.25); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }
    .er-good  { background: rgba(59,130,246,0.25); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); }
    .er-mid   { background: rgba(245,158,11,0.25); color: #fbbf24; border: 1px solid rgba(245,158,11,0.3); }
    .er-high  { background: rgba(244,63,94,0.25);  color: #fb7185; border: 1px solid rgba(244,63,94,0.3); }

    /* Break ATH Info Row */
    .ath-info-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 9px;
      padding: 7px 10px;
      background: rgba(6, 182, 212, 0.08);
      border-radius: 8px;
      border: 1px solid rgba(6, 182, 212, 0.22);
      align-items: center;
      justify-content: space-between;
    }
    .ath-tag {
      font-size: 10.5px;
      font-weight: 700;
      color: var(--cyan-light);
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: var(--font-mono);
    }
    .ath-tag .ath-label { color: var(--text-sub); font-weight: 500; }

    /* Volatility & Safety Row */
    .card-row-vol {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 11px;
      font-weight: 600;
      padding: 0 2px;
      margin-bottom: 10px;
      color: var(--text-sub);
      font-family: var(--font-mono);
      flex-wrap: wrap;
      gap: 6px;
    }
    .vol-tag { display: inline-flex; align-items: center; gap: 3px; }
    .vol-pos { color: var(--green-light); font-weight: 700; }
    .vol-neg { color: var(--red); font-weight: 700; }

    /* Card Bottom Row */
    .card-row-bottom {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border-top: 1px dashed rgba(255,255,255,0.07);
      padding-top: 10px;
    }

    .state-pill {
      font-size: 10.5px;
      font-weight: 700;
      padding: 4px 9px;
      border-radius: 7px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 58%;
    }
    .state-absorption { background: var(--blue-bg);  color: var(--rh-light);   border: 1px solid rgba(59,130,246,0.3); }
    .state-chop       { background: var(--green-bg); color: var(--green-light);border: 1px solid rgba(16,185,129,0.3); }
    .state-reaccum    { background: var(--yellow-bg);color: var(--sol-light);  border: 1px solid rgba(245,158,11,0.3); }
    .state-neutral    { background: rgba(255,255,255,0.05); color: var(--text-sub); border: 1px solid rgba(255,255,255,0.1); }
    .state-distrib    { background: var(--red-bg);   color: #fb7185;           border: 1px solid rgba(244,63,94,0.3); }
    .state-ath        { background: var(--cyan-bg);  color: var(--cyan-light); border: 1px solid rgba(6,182,212,0.35); }
    .state-momentum   { background: rgba(234, 179, 8, 0.18); color: #fde047; border: 1px solid rgba(234, 179, 8, 0.4); }

    .card-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }

    .btn-chart {
      background: linear-gradient(135deg, #10b981, #059669);
      color: #ffffff;
      text-decoration: none;
      font-size: 11px;
      font-weight: 700;
      padding: 5px 10px;
      border-radius: 7px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: none;
      box-shadow: 0 2px 6px rgba(16,185,129,0.35);
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .btn-chart:hover { box-shadow: 0 4px 14px rgba(16,185,129,0.55); transform: translateY(-1px); }
    .btn-chart:active { transform: scale(0.95); }

    .btn-chart-secondary {
      background: rgba(255,255,255,0.07);
      color: var(--text-main);
      border: 1px solid rgba(255,255,255,0.12);
      box-shadow: none;
    }
    .btn-chart-secondary:hover { background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.22); }

    /* Gap reasons pills */
    .gap-reasons-box {
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      padding-top: 8px;
      border-top: 1px dashed rgba(255,255,255,0.05);
    }
    .gap-pill {
      font-size: 10px;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 6px;
      background: rgba(244,63,94,0.12);
      color: #fb7185;
      border: 1px solid rgba(244,63,94,0.25);
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: var(--font-mono);
    }

    /* ===== ARCTOOLS DENSE TABLE STYLES ===== */
    .table-container {
      width: 100%;
      overflow-x: auto;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-card);
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
      -webkit-overflow-scrolling: touch;
    }
    .arc-table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 12px;
      min-width: 960px;
    }
    .arc-thead th {
      background: rgba(11, 17, 32, 0.95);
      color: var(--text-dim);
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 11px 10px;
      border-bottom: 1px solid var(--card-border);
      white-space: nowrap;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .arc-tr {
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      transition: background 0.12s ease;
    }
    .arc-tr:hover {
      background: rgba(255, 255, 255, 0.035);
    }
    .arc-tr.rank-1 { box-shadow: inset 3px 0 0 #f5c542; background: rgba(245, 196, 81, 0.02); }
    .arc-tr.rank-2 { box-shadow: inset 3px 0 0 #e2e8f0; }
    .arc-tr.rank-3 { box-shadow: inset 3px 0 0 #fb923c; }

    .arc-td {
      padding: 10px 10px;
      vertical-align: middle;
      font-family: var(--font-sans);
    }
    .arc-td.mono {
      font-family: var(--font-mono);
    }
    .arc-tok-cell {
      display: flex;
      align-items: center;
      gap: 9px;
      min-width: 200px;
    }
    .arc-tok-meta {
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 10.5px;
      color: var(--text-dim);
      margin-top: 2px;
      font-family: var(--font-mono);
    }
    .arc-of-bar-mini {
      width: 60px;
      height: 4px;
      background: rgba(244, 63, 94, 0.3);
      border-radius: 2px;
      overflow: hidden;
      display: inline-flex;
      vertical-align: middle;
      margin-right: 5px;
    }

    /* ===== EMPTY STATE ===== */
    .empty-box {
      background: var(--card-bg);
      border: 1px dashed var(--card-border);
      border-radius: 16px;
      padding: 44px 20px;
      text-align: center;
      color: var(--text-sub);
      grid-column: 1 / -1;
    }
    .empty-icon  { font-size: 38px; margin-bottom: 12px; }
    .empty-title { font-size: 16px; font-weight: 700; color: var(--text-main); margin-bottom: 6px; }
    .empty-desc  { font-size: 13px; line-height: 1.6; max-width: 480px; margin: 0 auto; }
    .empty-btn   { margin-top: 14px; }

    /* ===== FOOTER INFO ===== */
    .footer-info {
      margin-top: 20px;
      text-align: center;
      font-size: 11.5px;
      color: var(--text-dim);
      font-family: var(--font-mono);
    }

    /* ===== MODAL SETTINGS ===== */
    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.76);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      z-index: 200;
      align-items: flex-end;
      justify-content: center;
    }
    .modal-overlay.show { display: flex; }

    .modal-sheet {
      background: #0f172a;
      border-top-left-radius: 22px;
      border-top-right-radius: 22px;
      border: 1px solid var(--card-border);
      border-bottom: none;
      width: 100%;
      max-width: 580px;
      max-height: 88vh;
      overflow-y: auto;
      padding: 20px 18px calc(var(--safe-bottom) + 20px);
      animation: slideUp 0.25s ease-out;
    }

    @keyframes slideUp {
      from { transform: translateY(100%); }
      to   { transform: translateY(0); }
    }
    @keyframes fadeScaleIn {
      from { opacity: 0; transform: scale(0.96); }
      to   { opacity: 1; transform: scale(1); }
    }

    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--card-border);
    }

    .modal-title {
      font-size: 16px;
      font-weight: 800;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .modal-close {
      background: #1e293b;
      border: none;
      color: var(--text-sub);
      width: 30px;
      height: 30px;
      border-radius: 50%;
      font-size: 15px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    }
    .modal-close:hover { background: #334155; color: #ffffff; }

    /* Quick Tuning Presets */
    .preset-strip {
      display: flex;
      gap: 6px;
      margin-bottom: 14px;
    }
    .btn-preset {
      flex: 1;
      background: rgba(255,255,255,0.05);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      padding: 6px 4px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s;
      text-align: center;
    }
    .btn-preset:hover { background: var(--card-hover); color: #ffffff; border-color: rgba(255,255,255,0.2); }

    .form-section-title {
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-sub);
      margin: 16px 0 10px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--card-border);
    }
    .form-section-title:first-of-type { margin-top: 0; }

    .form-grid-2 {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }

    .form-group { margin-bottom: 4px; }

    .form-label {
      display: flex;
      justify-content: space-between;
      font-size: 11.5px;
      font-weight: 600;
      color: var(--text-sub);
      margin-bottom: 4px;
    }

    .form-input {
      width: 100%;
      background: var(--card-inner);
      border: 1px solid var(--card-border);
      border-radius: 9px;
      padding: 9px 11px;
      font-size: 13.5px;
      font-weight: 600;
      color: #ffffff;
      outline: none;
      font-family: var(--font-mono);
      transition: border-color 0.15s;
    }
    .form-input:focus { border-color: var(--green); }

    .modal-actions { display: flex; gap: 10px; margin-top: 20px; }

    .btn-submit {
      flex: 1;
      background: linear-gradient(135deg, #10b981, #059669);
      color: #ffffff;
      border: none;
      padding: 12px 0;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(16,185,129,0.3);
      transition: box-shadow 0.15s;
    }
    .btn-submit:hover { box-shadow: 0 4px 16px rgba(16,185,129,0.5); }

    .btn-reset {
      background: #1e293b;
      color: var(--text-sub);
      border: none;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-reset:hover { background: #334155; color: #ffffff; }

    /* ===== TOAST NOTIFICATION ===== */
    .toast {
      position: fixed;
      top: 18px;
      left: 50%;
      transform: translateX(-50%) translateY(-20px);
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid var(--green);
      color: #ffffff;
      padding: 8px 20px;
      border-radius: 20px;
      font-size: 12.5px;
      font-weight: 700;
      z-index: 300;
      box-shadow: 0 8px 24px rgba(0,0,0,0.6);
      opacity: 0;
      pointer-events: none;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: var(--font-mono);
    }
    .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }

    /* ===== RESPONSIVE MEDIA QUERIES ===== */

    /* Tablet & Desktop Layouts */
    @media (min-width: 768px) {
      .container { padding: 18px 24px; }
      .brand-title { font-size: 19px; }

      /* Desktop Header: Single Horizontal Bar */
      .header-inner {
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
      }
      .chain-segmented {
        max-width: 320px;
        flex: 1;
        margin: 0 16px;
      }
      .chain-tab { padding: 6px 12px; font-size: 12px; }

      /* Desktop KPI: 5 columns in single row */
      .kpi-grid {
        grid-template-columns: repeat(5, 1fr);
        gap: 10px;
      }
      .kpi-row-bottom {
        display: contents;
      }
      .kpi-card { padding: 12px 10px; }
      .kpi-val { font-size: 18px; }

      /* Desktop Controls: Tabs + Search & View Switcher in single row */
      .controls-strip {
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
      }
      .cat-tabs { flex: 1; margin-bottom: 0; }
      .controls-right {
        width: auto;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .search-wrap { width: 230px; }

      /* Card Grid: 2 columns on tablet */
      .card-list {
        grid-template-columns: repeat(2, 1fr);
        gap: 14px;
      }
      .token-name { max-width: 180px; }

      /* Modal: Centered Dialog */
      .modal-overlay { align-items: center; }
      .modal-sheet {
        border-radius: 18px;
        border: 1px solid var(--card-border);
        width: 540px;
        max-width: 90vw;
        max-height: 82vh;
        animation: fadeScaleIn 0.2s ease-out;
      }
      .form-grid-2 { grid-template-columns: repeat(2, 1fr); }
    }

    /* Wide Desktop */
    @media (min-width: 1100px) {
      .card-list {
        grid-template-columns: repeat(3, 1fr);
        gap: 14px;
      }
      .token-name { max-width: 160px; }
      .search-wrap { width: 270px; }
    }
  </style>
</head>
<body>

  <!-- ===== STICKY HEADER ===== -->
  <header class="app-header">
    <div class="header-inner">
      <div class="header-top">
        <div class="brand">
          <span class="brand-title">⚡ CHOP RADAR</span>
          <span class="brand-badge">PRO</span>
        </div>
        <div id="statusPulse" class="status-pulse">
          <span class="pulse-dot"></span>
          <span id="statusText">LIVE</span>
        </div>
      </div>

      <!-- Segmented Chain Switcher -->
      <div class="chain-segmented">
        <div class="chain-tab active" data-chain="all" onclick="setChainFilter('all')">
          🌐 ALL (<span id="cntChainAll">0</span>)
        </div>
        <div class="chain-tab" data-chain="sol" onclick="setChainFilter('sol')">
          🔸 SOL (<span id="cntChainSol">0</span>)
        </div>
        <div class="chain-tab" data-chain="rh" onclick="setChainFilter('rh')">
          🔹 RH (<span id="cntChainRh">0</span>)
        </div>
      </div>

      <div class="header-actions">
        <button id="btnScan" class="btn-icon btn-scan" onclick="triggerScan()" title="Pindai Ulang Data (Hotkey: S)">
          <span>⚡</span>
          <span id="scanCountdown">SCAN</span>
        </button>
        <button class="btn-icon" onclick="openModal()" title="Pengaturan Filter LP">
          <span>⚙️</span>
        </button>
      </div>
    </div>
  </header>

  <!-- ===== MAIN CONTENT ===== -->
  <main class="container">

    <!-- KPI Summary Grid (5 metrics) -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label"><span>🟢</span> Siap LP</div>
        <div id="kpiSiap" class="kpi-val green">0</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>🚀</span> Break ATH</div>
        <div id="kpiBath" class="kpi-val cyan">0</div>
      </div>

      <div class="kpi-row-bottom">
        <div class="kpi-card">
          <div class="kpi-label"><span>📡</span> Absorb</div>
          <div id="kpiAbsorb" class="kpi-val">0</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label"><span>💰</span> Top Yield</div>
          <div id="kpiTopYield" class="kpi-val green">$0.00</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label"><span>🕒</span> Scan</div>
          <div id="kpiTime" class="kpi-val yellow">--:--</div>
        </div>
      </div>
    </div>

    <!-- Controls Strip: Category Tabs + Quick Search -->
    <div class="controls-strip">
      <div class="cat-tabs">
        <div class="cat-tab active" data-cat="siap" onclick="setCategoryTab('siap')" title="Hotkey: 1">
          <span>🟢 SIAP LP</span>
          <span id="badgeSiap" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="momentum_5m" onclick="setCategoryTab('momentum_5m')" title="Hotkey: 2">
          <span>⚡ 5M MOMENTUM</span>
          <span id="badgeM5" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="absorption" onclick="setCategoryTab('absorption')" title="Hotkey: 3">
          <span>📡 ABSORB</span>
          <span id="badgeAbsorb" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="break_ath" onclick="setCategoryTab('break_ath')" title="Hotkey: 4">
          <span>🚀 ATH</span>
          <span id="badgeBath" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="gaps" onclick="setCategoryTab('gaps')" title="Hotkey: 5">
          <span>⚠️ GAPS</span>
          <span id="badgeGaps" class="badge-count">0</span>
        </div>
      </div>

      <!-- Controls Right: Quick Search + View Switcher -->
      <div class="controls-right">
        <div class="search-wrap">
          <span class="search-icon">🔍</span>
          <input type="text" id="tokenSearch" class="search-input" placeholder="Cari simbol atau CA... ( / )" oninput="onSearchInput()">
          <button id="searchClear" class="search-clear" onclick="clearSearch()">✕</button>
        </div>

        <div class="view-toggle" title="Ubah Tampilan Daftar (Hotkey: V)">
          <button id="btnViewCards" class="btn-view active" onclick="setViewMode('cards')">
            <span>⊞</span> Cards
          </button>
          <button id="btnViewTable" class="btn-view" onclick="setViewMode('table')">
            <span>☰</span> Table
          </button>
        </div>
      </div>
    </div>

    <!-- Token Cards / Table List Feed -->
    <div id="tokenCardsList" class="token-container"></div>

    <!-- Footer Counter -->
    <div class="footer-info" id="footerInfo" style="display:none">
      <span id="footerText"></span>
    </div>

  </main>

  <!-- ===== FILTER MODAL SETTINGS ===== -->
  <div id="filterModal" class="modal-overlay" onclick="closeModalOnBg(event)">
    <div class="modal-sheet">
      <div class="modal-header">
        <div class="modal-title">⚙️ Parameter Filter LP</div>
        <button class="modal-close" onclick="closeModal()" title="Tutup (Esc)">✕</button>
      </div>

      <!-- Quick Tuning Presets -->
      <div class="preset-strip">
        <button class="btn-preset" onclick="applyPreset('konservatif')">🛡️ Konservatif ($5/h)</button>
        <button class="btn-preset" onclick="applyPreset('standar')">⚖️ Standar ($1/h)</button>
        <button class="btn-preset" onclick="applyPreset('agresif')">🚀 Agresif ($0.5/h)</button>
      </div>

      <div class="form-section-title">📊 Kriteria Chop Sideways LP</div>
      <div class="form-grid-2">
        <div class="form-group">
          <div class="form-label">
            <span>Min Fee Siap LP ($/jam)</span>
            <span style="color:var(--green-light)">Posisi $100</span>
          </div>
          <input type="number" step="0.1" id="f_min_fee" class="form-input" value="1.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min Market Cap ($)</span>
            <span>Filter micap</span>
          </div>
          <input type="number" step="50000" id="f_min_mcap" class="form-input" value="500000">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min Likuiditas ($)</span>
            <span>Pool TVL</span>
          </div>
          <input type="number" step="5000" id="f_min_liq" class="form-input" value="20000">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min V/L 24h</span>
            <span>Perputaran fee</span>
          </div>
          <input type="number" step="0.5" id="f_min_vl" class="form-input" value="2.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Max Volatilitas 5m (%)</span>
            <span>Simetris pump/dump</span>
          </div>
          <input type="number" step="1" id="f_max_5m" class="form-input" value="15.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Max Volatilitas 1h (%)</span>
            <span>Simetris pump/dump</span>
          </div>
          <input type="number" step="5" id="f_max_1h" class="form-input" value="80.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Max Efficiency Ratio (ER)</span>
            <span>Ideal ≤ 20</span>
          </div>
          <input type="number" step="1" id="f_max_er" class="form-input" value="20.0">
        </div>
      </div>

      <div class="form-section-title">⚡ Kriteria 5M Momentum</div>
      <div class="form-grid-2">
        <div class="form-group">
          <div class="form-label">
            <span>Min 5m Volume ($)</span>
            <span style="color:#fde047">Default $100k</span>
          </div>
          <input type="number" step="10000" id="f_m5_vol" class="form-input" value="100000">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min Likuiditas 5m ($)</span>
            <span>Default $10k</span>
          </div>
          <input type="number" step="1000" id="f_m5_liq" class="form-input" value="10000">
        </div>
      </div>

      <div class="form-section-title">⚙️ Konfigurasi Pemindaian</div>
      <div class="form-grid-2">
        <div class="form-group">
          <div class="form-label">
            <span>Modal Simulasi Posisi ($)</span>
            <span>Dasar kalkulasi $/h</span>
          </div>
          <input type="number" step="10" id="f_position" class="form-input" value="100.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Interval Scan (Detik)</span>
            <span>300s = 5 menit</span>
          </div>
          <input type="number" step="30" id="f_interval" class="form-input" value="300">
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn-submit" onclick="saveFilters()">💾 Simpan &amp; Terapkan</button>
        <button class="btn-reset" onclick="resetDefaultFilters()">Reset</button>
      </div>
    </div>
  </div>

  <!-- Floating Toast -->
  <div id="toast" class="toast"><span>🔔</span> <span id="toastMsg">Notifikasi</span></div>

  <script>
    let globalState = null;
    let activeChain = 'all';
    let activeCategory = 'siap';
    let searchQuery = '';
    let countdownInterval = null;
    let viewMode = localStorage.getItem("lp_view_mode") || (window.innerWidth >= 1024 ? "table" : "cards");

    /* ---- ArcTools Style Avatar Generator ---- */
    function getAvatarGradient(s) {
      let hash = 0;
      const str = String(s || "?").toUpperCase();
      for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
      const h1 = Math.abs(hash) % 360;
      const h2 = (h1 + 45) % 360;
      return `linear-gradient(135deg, hsl(${h1}, 65%, 38%), hsl(${h2}, 70%, 20%))`;
    }

    function renderAvatar(symbol, logoUrl, size = 38) {
      const sym = String(symbol || "?").toUpperCase();
      const clean = sym.replace(/[^A-Z0-9]/g, "");
      const initials = clean.slice(0, 2) || sym.slice(0, 2) || "??";
      const grad = getAvatarGradient(sym);
      if (logoUrl && (logoUrl.startsWith("http://") || logoUrl.startsWith("https://"))) {
        return `
          <div class="tok-avatar-wrap" style="width:${size}px;height:${size}px">
            <img src="${logoUrl}" class="tok-avatar-img" alt="${sym}" loading="lazy" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';">
            <div class="tok-avatar-mono" style="display:none;background:${grad}">${initials}</div>
          </div>`;
      }
      return `
        <div class="tok-avatar-wrap" style="width:${size}px;height:${size}px">
          <div class="tok-avatar-mono" style="background:${grad}">${initials}</div>
        </div>`;
    }

    /* ---- ArcTools Rank Badges ---- */
    function getRankBadge(idx) {
      if (idx === 0) return `<span class="rank-badge rank-gold" title="Rank #1 by Yield">🥇 #1</span>`;
      if (idx === 1) return `<span class="rank-badge rank-silver" title="Rank #2 by Yield">🥈 #2</span>`;
      if (idx === 2) return `<span class="rank-badge rank-bronze" title="Rank #3 by Yield">🥉 #3</span>`;
      return `<span class="rank-badge rank-dim">#${idx + 1}</span>`;
    }

    /* ---- Age Formatter ---- */
    function formatAge(ageHours) {
      if (ageHours === undefined || ageHours === null || ageHours >= 9000 || ageHours <= 0) return "";
      if (ageHours < 1) {
        const m = Math.max(1, Math.round(ageHours * 60));
        return `<span class="meta-age fresh" title="Pool berusia ${m} menit">${m}m</span>`;
      }
      if (ageHours < 24) {
        return `<span class="meta-age fresh" title="Pool berusia ${ageHours.toFixed(1)} jam">${ageHours.toFixed(0)}h</span>`;
      }
      const d = Math.round(ageHours / 24);
      return `<span class="meta-age" title="Pool berusia ${d} hari">${d}d</span>`;
    }

    /* ---- Format Numbers & Transactions ---- */
    function formatTx(num) {
      if (!num || num <= 0) return "0";
      if (num >= 1e3) return (num / 1e3).toFixed(1) + "k";
      return num.toString();
    }

    /* ---- View Mode Toggle ---- */
    function setViewMode(mode) {
      viewMode = mode;
      localStorage.setItem("lp_view_mode", mode);
      const bCards = document.getElementById("btnViewCards");
      const bTable = document.getElementById("btnViewTable");
      if (bCards) bCards.classList.toggle("active", mode === "cards");
      if (bTable) bTable.classList.toggle("active", mode === "table");
      renderCards();
    }

    /* ---- Helpers ---- */
    function formatUsd(val) {
      if (!val || val <= 0) return "$0";
      if (val >= 1e6) {
        const m = val / 1e6;
        return "$" + (m < 10 ? m.toFixed(2) : m.toFixed(1)) + "M";
      }
      if (val >= 1e3) {
        const k = val / 1e3;
        return "$" + (k >= 100 || k === Math.floor(k) ? Math.floor(k) : k.toFixed(1)) + "k";
      }
      return "$" + Math.round(val);
    }

    function formatPrice(p) {
      if (!p || p <= 0) return "$0.00";
      if (p < 0.000001) return "$" + p.toExponential(2);
      if (p < 0.001)    return "$" + p.toFixed(6);
      if (p < 1)        return "$" + p.toFixed(4);
      return "$" + p.toFixed(2);
    }

    function showToast(msg, icon = "🔔") {
      const t = document.getElementById("toast");
      const m = document.getElementById("toastMsg");
      t.firstElementChild.innerText = icon;
      m.innerText = msg;
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 2400);
    }

    /* ---- Copy CA Clipboard ---- */
    function copyCA(ca, btn) {
      if (!ca) return;
      navigator.clipboard.writeText(ca).then(() => {
        if (btn) {
          const orig = btn.innerHTML;
          btn.innerHTML = `✓ Copied!`;
          btn.classList.add("copied");
          setTimeout(() => {
            btn.innerHTML = orig;
            btn.classList.remove("copied");
          }, 1600);
        }
        showToast(`CA disalin: ${ca.slice(0, 6)}...${ca.slice(-4)}`, "📋");
      }).catch(() => {
        // Fallback
        const ta = document.createElement("textarea");
        ta.value = ca;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast(`CA disalin!`, "📋");
      });
    }

    /* ---- Quick Search Input ---- */
    function onSearchInput() {
      const inp = document.getElementById("tokenSearch");
      const clr = document.getElementById("searchClear");
      searchQuery = (inp.value || "").trim().toLowerCase();
      clr.classList.toggle("visible", searchQuery.length > 0);
      renderCards();
    }

    function clearSearch() {
      const inp = document.getElementById("tokenSearch");
      inp.value = "";
      onSearchInput();
      inp.focus();
    }

    /* ---- Chain & Category Filter Handlers ---- */
    function setChainFilter(chain) {
      activeChain = chain;
      document.querySelectorAll(".chain-tab").forEach(tab =>
        tab.classList.toggle("active", tab.getAttribute("data-chain") === chain)
      );
      renderCards();
    }

    function setCategoryTab(cat) {
      activeCategory = cat;
      document.querySelectorAll(".cat-tab").forEach(tab =>
        tab.classList.toggle("active", tab.getAttribute("data-cat") === cat)
      );
      renderCards();
    }

    /* ---- Modal Settings ---- */
    function openModal() {
      if (globalState && globalState.filters) {
        const f = globalState.filters;
        if (f.min_fee_siap_lp     !== undefined) document.getElementById("f_min_fee").value  = f.min_fee_siap_lp;
        if (f.min_mcap           !== undefined) document.getElementById("f_min_mcap").value = f.min_mcap;
        if (f.min_liq            !== undefined) document.getElementById("f_min_liq").value  = f.min_liq;
        if (f.min_vl             !== undefined) document.getElementById("f_min_vl").value   = f.min_vl;
        if (f.max_5m             !== undefined) document.getElementById("f_max_5m").value   = f.max_5m;
        if (f.max_1h             !== undefined) document.getElementById("f_max_1h").value   = f.max_1h;
        if (f.max_er             !== undefined) document.getElementById("f_max_er").value   = f.max_er;
        if (f.momentum_5m_min_vol !== undefined) document.getElementById("f_m5_vol").value   = f.momentum_5m_min_vol;
        if (f.momentum_5m_min_liq !== undefined) document.getElementById("f_m5_liq").value   = f.momentum_5m_min_liq;
        if (f.position_usd       !== undefined) document.getElementById("f_position").value = f.position_usd;
        if (f.interval_sec       !== undefined) document.getElementById("f_interval").value = f.interval_sec;
      }
      document.getElementById("filterModal").classList.add("show");
    }

    function closeModal() {
      document.getElementById("filterModal").classList.remove("show");
    }

    function closeModalOnBg(e) {
      if (e.target.id === "filterModal") closeModal();
    }

    function applyPreset(p) {
      if (p === 'konservatif') {
        document.getElementById("f_min_fee").value  = 5.0;
        document.getElementById("f_min_mcap").value = 500000;
        document.getElementById("f_min_liq").value  = 30000;
        document.getElementById("f_min_vl").value   = 3.0;
        document.getElementById("f_max_5m").value   = 10.0;
        document.getElementById("f_max_1h").value   = 60.0;
        document.getElementById("f_max_er").value   = 15.0;
        document.getElementById("f_m5_vol").value   = 150000;
        document.getElementById("f_m5_liq").value   = 20000;
        showToast("Preset Konservatif dipilih", "🛡️");
      } else if (p === 'standar') {
        resetDefaultFilters();
        showToast("Preset Standar dipilih", "⚖️");
      } else if (p === 'agresif') {
        document.getElementById("f_min_fee").value  = 0.5;
        document.getElementById("f_min_mcap").value = 500000;
        document.getElementById("f_min_liq").value  = 15000;
        document.getElementById("f_min_vl").value   = 1.5;
        document.getElementById("f_max_5m").value   = 20.0;
        document.getElementById("f_max_1h").value   = 100.0;
        document.getElementById("f_max_er").value   = 25.0;
        document.getElementById("f_m5_vol").value   = 80000;
        document.getElementById("f_m5_liq").value   = 10000;
        showToast("Preset Agresif dipilih", "🚀");
      }
    }

    function resetDefaultFilters() {
      document.getElementById("f_min_fee").value  = 1.0;
      document.getElementById("f_min_mcap").value = 500000;
      document.getElementById("f_min_liq").value  = 20000;
      document.getElementById("f_min_vl").value   = 2.0;
      document.getElementById("f_max_5m").value   = 15.0;
      document.getElementById("f_max_1h").value   = 80.0;
      document.getElementById("f_max_er").value   = 20.0;
      document.getElementById("f_m5_vol").value   = 100000;
      document.getElementById("f_m5_liq").value   = 10000;
      document.getElementById("f_position").value = 100.0;
      document.getElementById("f_interval").value = 300;
    }

    async function saveFilters() {
      const payload = {
        min_fee_siap_lp:     parseFloat(document.getElementById("f_min_fee").value)  || 1.0,
        min_mcap:            parseFloat(document.getElementById("f_min_mcap").value) || 500000,
        min_liq:             parseFloat(document.getElementById("f_min_liq").value)  || 20000,
        min_vl:              parseFloat(document.getElementById("f_min_vl").value)   || 2.0,
        max_5m:              parseFloat(document.getElementById("f_max_5m").value)   || 15.0,
        max_1h:              parseFloat(document.getElementById("f_max_1h").value)   || 80.0,
        max_er:              parseFloat(document.getElementById("f_max_er").value)   || 20.0,
        momentum_5m_min_vol: parseFloat(document.getElementById("f_m5_vol").value)  || 100000.0,
        momentum_5m_min_liq: parseFloat(document.getElementById("f_m5_liq").value)  || 10000.0,
        position_usd:        parseFloat(document.getElementById("f_position").value) || 100.0,
        interval_sec:        parseInt(document.getElementById("f_interval").value)   || 300,
      };
      try {
        const res  = await fetch("/api/filters", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.ok) {
          closeModal();
          showToast("Filter berhasil disimpan!", "✅");
          fetchState();
        }
      } catch (err) {
        alert("Gagal menyimpan filter: " + err);
      }
    }

    /* ---- Scan Trigger ---- */
    async function triggerScan() {
      const btn = document.getElementById("btnScan");
      btn.style.opacity = "0.6";
      showToast("Memulai scan data GMGN...", "⏳");
      try {
        const res  = await fetch("/api/scan", { method: "POST" });
        const data = await res.json();
        if (data.ok) fetchState();
      } catch (err) {
        console.error("Scan error:", err);
      } finally {
        setTimeout(() => { btn.style.opacity = "1"; }, 1500);
      }
    }

    /* ---- Countdown Timer ---- */
    function updateCountdown() {
      if (!globalState) return;
      const pulse       = document.getElementById("statusPulse");
      const statusText  = document.getElementById("statusText");
      const scanBtnText = document.getElementById("scanCountdown");

      if (globalState.scanning) {
        pulse.classList.add("scanning");
        statusText.innerText  = "SCANNING";
        scanBtnText.innerText = "SCANNING";
        return;
      }

      pulse.classList.remove("scanning");
      statusText.innerText = "LIVE";

      const now  = Math.floor(Date.now() / 1000);
      const next = globalState.next_scan_timestamp || 0;
      const diff = Math.max(0, next - now);

      if (diff > 0) {
        const m = Math.floor(diff / 60).toString().padStart(2, "0");
        const s = (diff % 60).toString().padStart(2, "0");
        scanBtnText.innerText = `${m}:${s}`;
      } else {
        scanBtnText.innerText = "SCAN";
      }
    }

    /* ---- Render Token Cards & Table ---- */
    function renderCards() {
      const container = document.getElementById("tokenCardsList");
      if (!container) return;

      // Update active toggle buttons
      const bCards = document.getElementById("btnViewCards");
      const bTable = document.getElementById("btnViewTable");
      if (bCards) bCards.classList.toggle("active", viewMode === "cards");
      if (bTable) bTable.classList.toggle("active", viewMode === "table");

      if (!globalState) {
        container.innerHTML = `<div class="empty-box"><div class="empty-icon">⏳</div><div class="empty-title">Memuat data radar...</div></div>`;
        return;
      }

      // Source per active category
      let rawList = [];
      if      (activeCategory === "siap")        rawList = globalState.siap_lp    || [];
      else if (activeCategory === "momentum_5m") rawList = globalState.momentum_5m || [];
      else if (activeCategory === "absorption")  rawList = globalState.absorption || [];
      else if (activeCategory === "break_ath")   rawList = globalState.break_ath  || [];
      else                                       rawList = globalState.gaps        || [];

      // Count badges in chain switcher
      let solCount = 0, rhCount = 0;
      rawList.forEach(t => {
        if ((t.chain || "SOL").toUpperCase() === "RH") rhCount++; else solCount++;
      });
      document.getElementById("cntChainAll").innerText = rawList.length;
      document.getElementById("cntChainSol").innerText = solCount;
      document.getElementById("cntChainRh").innerText  = rhCount;

      // Apply chain filter
      let filtered = rawList;
      if      (activeChain === "sol") filtered = rawList.filter(t => (t.chain || "SOL").toUpperCase() !== "RH");
      else if (activeChain === "rh")  filtered = rawList.filter(t => (t.chain || "SOL").toUpperCase() === "RH");

      // Apply quick search query if any
      if (searchQuery) {
        filtered = filtered.filter(t =>
          (t.symbol || "").toLowerCase().includes(searchQuery) ||
          (t.name || "").toLowerCase().includes(searchQuery) ||
          (t.address || "").toLowerCase().includes(searchQuery)
        );
      }

      // Pareto guarantee for gaps
      if (activeCategory === "gaps") {
        filtered = [...filtered].sort((a, b) => (b.fee_hour || 0) - (a.fee_hour || 0) || (b.vol || 0) - (a.vol || 0));
      }

      // Empty State
      if (!filtered.length) {
        const msgs = {
          siap:        "Belum ada token memenuhi kriteria Siap LP (Fee ≥ $1/h & MC ≥ $500k).",
          momentum_5m: "Belum ada token memenuhi kriteria ⚡ 5M Momentum (Vol 5m > $100k, Pump Up, Liq ≥ $10k).",
          absorption:  "Belum ada sinyal akumulasi/absorption terdeteksi saat ini.",
          break_ath:   "Belum ada token Break ATH terkonfirmasi (≥ 15m, Fee ≥ $1/h, ATH > $500k).",
          gaps:        "Tidak ada token radar yang berada di luar kriteria.",
        };
        const searchMsg = searchQuery ? `Tidak ditemukan token yang cocok dengan pencarian "<b>${searchQuery}</b>".` : (msgs[activeCategory] || msgs.siap);
        container.innerHTML = `
          <div class="empty-box">
            <div class="empty-icon">🔍</div>
            <div class="empty-title">Tidak Ada Token</div>
            <div class="empty-desc">${searchMsg}<br>Coba ubah filter Chain atau sesuaikan tuning di menu ⚙️.</div>
          </div>`;
        updateFooter(0, globalState.total_scanned || 0);
        return;
      }

      // Render Table View (ArcTools Dense Trading Style)
      if (viewMode === "table") {
        let tRows = "";
        filtered.forEach((t, idx) => {
          const isRh = (t.chain || "SOL").toUpperCase() === "RH";
          const chainBadge = isRh
            ? `<span class="chain-pill rh" style="font-size:9px;padding:1px 4px;margin-left:4px">RH</span>`
            : `<span class="chain-pill sol" style="font-size:9px;padding:1px 4px;margin-left:4px">SOL</span>`;
          const rankBadge = getRankBadge(idx);
          const rankClass = idx === 0 ? "rank-1" : (idx === 1 ? "rank-2" : (idx === 2 ? "rank-3" : ""));

          const feeHour = t.fee_hour ? `$${t.fee_hour.toFixed(2)}/h` : "$0.00/h";
          const feeDay  = t.fee_24h  ? `+$${t.fee_24h.toFixed(1)}/24h` : "";
          const mcapStr = formatUsd(t.mcap || 0);
          const liqStr  = formatUsd(t.liq  || 0);
          const vlStr   = (t.vl || 0).toFixed(1) + "x";
          const priceStr = formatPrice(t.price || 0);
          const addrStr = t.address || "";
          const caShort = addrStr ? (addrStr.slice(0, 4) + "…" + addrStr.slice(-4)) : "";
          const ageHtml = formatAge(t.age_hours);
          const venueTag = isRh ? "UniswapV4" : (t.url && t.url.includes("meteora") ? "Meteora" : "Raydium");
          const avatarHtml = renderAvatar(t.symbol, t.logo, 34);

          // ER badge
          const erVal = t.er !== undefined ? t.er : 999;
          let erClass = "er-high", erText = "High";
          if      (erVal <= 3)  { erClass = "er-prime"; erText = "Prime"; }
          else if (erVal <= 6)  { erClass = "er-good";  erText = "Good"; }
          else if (erVal <= 15) { erClass = "er-mid";   erText = "Mid"; }
          const erBadge = `<span class="er-badge ${erClass}">${erVal.toFixed(1)} · ${erText}</span>`;

          // Volatility
          const p5 = t.p5 || 0, p1 = t.p1 || 0;
          const p5Str = (p5 >= 0 ? "+" : "") + p5.toFixed(1) + "%";
          const p1Str = (p1 >= 0 ? "+" : "") + p1.toFixed(1) + "%";

          // Order flow
          const buys = t.buys || 0, sells = t.sells || 0;
          const buyRatio = t.buy_ratio !== undefined ? Math.round(t.buy_ratio) : 50;

          // Safety grade
          const sScore = Math.round(t.score || 0);
          const sGrade = sScore >= 80 ? "A" : (sScore >= 65 ? "B" : (sScore >= 50 ? "C" : "D"));

          // State Pill
          const mState = t.micro_state || "NEUTRAL";
          let spClass = "state-neutral", spIcon = "🎯";
          if (activeCategory === "momentum_5m") { spClass = "state-momentum"; spIcon = "⚡"; }
          else if (activeCategory === "break_ath")  { spClass = "state-ath";        spIcon = "🚀"; }
          else if (mState === "ABSORPTION")    { spClass = "state-absorption"; spIcon = "📡"; }
          else if (mState === "REACCUMULATION"){ spClass = "state-reaccum";    spIcon = "🔄"; }
          else if (mState === "DISTRIBUTION")  { spClass = "state-distrib";    spIcon = "⚠️"; }
          else if (t.is_chop)                  { spClass = "state-chop";       spIcon = "🟢"; }
          const spLabel = activeCategory === "momentum_5m" ? "5M Momentum ⚡" : (activeCategory === "break_ath" ? "Break ATH ✓" : (t.status_label || (t.is_chop ? "Chop Sideways" : "Monitoring")));

          const dexsUrl = isRh ? `https://fomo.family/token/${addrStr}` : `https://dexscreener.com/solana/${addrStr}`;
          const dexsLabel = isRh ? "FOMO ↗" : "DexS ↗";

          // Sub-details if Momentum, ATH
          let subRowHtml = "";
          if (activeCategory === "momentum_5m") {
            subRowHtml = `<span style="color:#fde047;font-size:10px;margin-left:8px">⚡ V5: ${formatUsd(t.vol_5m || t.vol || 0)}</span>`;
          } else if (activeCategory === "break_ath") {
            subRowHtml = `<span style="color:var(--cyan-light);font-size:10px;margin-left:8px">🚀 +${t.breakout_pct || 0}%</span>`;
          } else if (activeCategory === "gaps" && t.gap_reasons && t.gap_reasons.length) {
            subRowHtml = `<span style="color:var(--red);font-size:10px;margin-left:8px">❌ ${t.gap_reasons[0]}</span>`;
          }

          tRows += `
            <tr class="arc-tr ${rankClass}">
              <td class="arc-td" style="width:36px;text-align:center">${rankBadge}</td>
              <td class="arc-td" style="white-space:nowrap">
                <div style="display:flex;align-items:center;gap:6px">
                  ${avatarHtml}
                  <span style="font-weight:800;font-size:13.5px;color:#fff">${t.symbol || "?"}</span>
                  ${chainBadge}
                  ${subRowHtml}
                </div>
              </td>
              <td class="arc-td mono" style="white-space:nowrap">
                <div style="display:flex;align-items:center;gap:6px">
                  <button class="btn-copy-inline" onclick="copyCA('${addrStr}', this)" title="Copy CA" style="background:rgba(255,255,255,0.05);padding:2px 6px;border-radius:4px;border:1px solid var(--card-border);color:var(--text-dim);cursor:pointer;display:flex;align-items:center;gap:4px;margin:0">
                    <span style="font-size:11px">${caShort}</span> <span style="font-size:10px">⧉</span>
                  </button>
                  <span style="color:var(--text-sub);font-size:10px">• ${venueTag} • ${ageHtml}</span>
                </div>
              </td>
              <td class="arc-td mono" style="font-weight:700;color:#fff">${mcapStr}</td>
              <td class="arc-td mono" style="color:#cbd5e1">${liqStr}</td>
              <td class="arc-td mono" style="color:#a5b4fc">${vlStr}</td>
              <td class="arc-td mono">${erBadge}</td>
              <td class="arc-td mono" style="font-size:11px">
                <span class="${p1 >= 0 ? 'vol-pos' : 'vol-neg'}">${p1Str}</span> / <span class="${p5 >= 0 ? 'vol-pos' : 'vol-neg'}">${p5Str}</span>
              </td>
              <td class="arc-td mono" style="color:var(--green-light)">${buyRatio}%</td>
              <td class="arc-td">
                <span class="safety-pill safety-${sGrade}" title="Score: ${sScore}/100" style="padding:2px 5px;font-size:10px">${sGrade} ${sScore}</span>
              </td>
              <td class="arc-td mono" style="text-align:right">
                <span class="fee-hour" style="font-size:14px">${feeHour}</span>
              </td>
              <td class="arc-td" style="text-align:right">
                <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px">
                  <span class="state-pill ${spClass}" style="padding:3px 6px;font-size:10px;white-space:nowrap" title="${spLabel}">${spIcon}</span>
                  <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-chart" style="padding:3px 8px;font-size:10px;margin:0">GMGN</a>
                </div>
              </td>
            </tr>`;
        });

        container.innerHTML = `
          <div class="table-container">
            <table class="arc-table">
              <thead class="arc-thead">
                <tr>
                  <th style="width:36px;text-align:center">#</th>
                  <th>TOKEN</th>
                  <th>CA / VENUE</th>
                  <th>MCAP</th>
                  <th>LIQ</th>
                  <th>V/L</th>
                  <th>ER</th>
                  <th>1H / 5M</th>
                  <th>BUY %</th>
                  <th>SAFE</th>
                  <th style="text-align:right">FEE/H</th>
                  <th style="text-align:right">AKSI</th>
                </tr>
              </thead>
              <tbody>${tRows}</tbody>
            </table>
          </div>`;
        updateFooter(filtered.length, globalState.total_scanned || 0);
        return;
      }

      // Render Cards View (Modern Responsive Grid)
      let html = `<div class="card-list">`;
      filtered.forEach((t, idx) => {
        const isRh = (t.chain || "SOL").toUpperCase() === "RH";
        const chainBadge = isRh
          ? `<span class="chain-pill rh">RH</span>`
          : `<span class="chain-pill sol">SOL</span>`;
        const cardClass = activeCategory === "momentum_5m" ? "momentum-card" : (activeCategory === "break_ath" ? "ath-card" : (isRh ? "rh-card" : "sol-card"));
        const rankBadge = getRankBadge(idx);
        const rankClass = idx === 0 ? "rank-1" : "";

        const feeHour = t.fee_hour ? `$${t.fee_hour.toFixed(2)}/h` : "$0.00/h";
        const feeDay  = t.fee_24h  ? `+$${t.fee_24h.toFixed(1)}/24h` : "";
        const mcapStr = formatUsd(t.mcap || 0);
        const liqStr  = formatUsd(t.liq  || 0);
        const vlStr   = (t.vl || 0).toFixed(1) + "x";
        const priceStr = formatPrice(t.price || 0);
        const addrStr = t.address || "";
        const caShort = addrStr ? (addrStr.slice(0, 4) + "…" + addrStr.slice(-4)) : "";
        const ageHtml = formatAge(t.age_hours);
        const venueTag = isRh ? "UniswapV4" : (t.url && t.url.includes("meteora") ? "Meteora" : "Raydium");
        const avatarHtml = renderAvatar(t.symbol, t.logo, 38);

        // ER badge
        const erVal = t.er !== undefined ? t.er : 999;
        let erClass = "er-high", erText = "High";
        if      (erVal <= 3)  { erClass = "er-prime"; erText = "Prime"; }
        else if (erVal <= 6)  { erClass = "er-good";  erText = "Good"; }
        else if (erVal <= 15) { erClass = "er-mid";   erText = "Mid"; }
        const erBadge = `<span class="er-badge ${erClass}">ER ${erVal.toFixed(1)} · ${erText}</span>`;

        // Volatility
        const p5 = t.p5 || 0, p1 = t.p1 || 0;
        const p5Str = (p5 >= 0 ? "▲ +" : "▼ ") + p5.toFixed(1) + "%";
        const p1Str = (p1 >= 0 ? "▲ +" : "▼ ") + p1.toFixed(1) + "%";

        // Order flow
        const buys = t.buys || 0, sells = t.sells || 0;
        const buyRatio = t.buy_ratio !== undefined ? Math.round(t.buy_ratio) : 50;

        // Safety grade
        const sScore = Math.round(t.score || 0);
        const sGrade = sScore >= 80 ? "A" : (sScore >= 65 ? "B" : (sScore >= 50 ? "C" : "D"));

        // State Pill
        const mState = t.micro_state || "NEUTRAL";
        let spClass = "state-neutral", spIcon = "🎯";
        if (activeCategory === "momentum_5m") { spClass = "state-momentum"; spIcon = "⚡"; }
        else if (activeCategory === "break_ath")  { spClass = "state-ath";        spIcon = "🚀"; }
        else if (mState === "ABSORPTION")    { spClass = "state-absorption"; spIcon = "📡"; }
        else if (mState === "REACCUMULATION"){ spClass = "state-reaccum";    spIcon = "🔄"; }
        else if (mState === "DISTRIBUTION")  { spClass = "state-distrib";    spIcon = "⚠️"; }
        else if (t.is_chop)                  { spClass = "state-chop";       spIcon = "🟢"; }

        const spLabel = activeCategory === "momentum_5m"
          ? "5M Momentum ⚡"
          : (activeCategory === "break_ath"
            ? "Break ATH ✓"
            : (t.status_label || (t.is_chop ? "Chopping Sideways" : "Monitoring")));

        // 5M Momentum / Break ATH Extra Banner
        let athHtml = "";
        if (activeCategory === "momentum_5m") {
          const v5m = formatUsd(t.vol_5m || t.vol || 0);
          athHtml = `
            <div class="ath-info-row" style="border-left: 2px solid #eab308; background: rgba(234, 179, 8, 0.08);">
              <div class="ath-tag" style="color:#fde047"><span>⚡ Vol 5m:</span> ${v5m}</div>
              <div class="ath-tag" style="color:var(--green-light)"><span>📈 Pump 5m:</span> ${p5Str}</div>
              <div class="ath-tag"><span>💧 Liq:</span> ${liqStr}</div>
            </div>`;
        } else if (activeCategory === "break_ath") {
          const bp  = t.breakout_pct !== undefined ? `+${t.breakout_pct}%` : "—";
          const dur = t.duration_mins !== undefined ? `${t.duration_mins}m` : "—";
          const athOld = t.ath_old ? formatUsd(t.ath_old) : "—";
          athHtml = `
            <div class="ath-info-row">
              <div class="ath-tag"><span>🚀 Breakout:</span> ${bp}</div>
              <div class="ath-tag"><span>⏱️ Durasi:</span> ${dur}</div>
              <div class="ath-tag"><span>📊 Rekor:</span> ${athOld}</div>
            </div>`;
        }

        // Gaps Reason Pills
        let gapsHtml = "";
        if (activeCategory === "gaps" && t.gap_reasons && t.gap_reasons.length) {
          gapsHtml = `<div class="gap-reasons-box">` +
            t.gap_reasons.map(r => {
              let icon = "✕";
              if (r.includes("Liq")) icon = "💧";
              else if (r.includes("V/L")) icon = "📊";
              else if (r.includes("Fee")) icon = "💰";
              else if (r.includes("5m") || r.includes("1h")) icon = "⚡";
              else if (r.includes("ER")) icon = "📐";
              return `<span class="gap-pill">${icon} ${r}</span>`;
            }).join("") +
            `</div>`;
        }

        const dexsUrl = isRh ? `https://fomo.family/token/${addrStr}` : `https://dexscreener.com/solana/${addrStr}`;
        const dexsLabel = isRh ? "FOMO ↗" : "DexS ↗";

        html += `
          <div class="token-card ${cardClass} ${rankClass}">
            <div class="card-row-top">
              <div class="token-info-left">
                ${rankBadge}
                ${avatarHtml}
                <div class="token-name-block">
                  <div class="symbol-row">
                    <span class="token-symbol">${t.symbol || "?"}</span>
                    ${chainBadge}
                    <button class="btn-copy-inline" onclick="copyCA('${addrStr}', this)" title="Salin Contract Address">
                      <span>${caShort}</span> <span>⧉</span>
                    </button>
                  </div>
                  <div class="token-sub-row">
                    <span class="token-price">${priceStr}</span>
                    ${ageHtml ? "<span>·</span>" + ageHtml : ""}
                    <span class="venue-pill">${venueTag}</span>
                    <span>·</span>
                    <span class="token-name" title="${t.name || ''}">${t.name || ""}</span>
                  </div>
                </div>
              </div>
              <div class="token-fee-right">
                <div class="fee-hour">${feeHour}</div>
                <div class="fee-day">${feeDay}</div>
              </div>
            </div>

            <div class="metrics-grid">
              <div class="metric-cell"><div class="m-label">MCAP</div><div class="m-val">${mcapStr}</div></div>
              <div class="metric-cell"><div class="m-label">LIQ TVL</div><div class="m-val">${liqStr}</div></div>
              <div class="metric-cell"><div class="m-label">V/L 24H</div><div class="m-val">${vlStr}</div></div>
              <div class="metric-cell"><div class="m-label">ER SCORE</div><div class="m-val">${erBadge}</div></div>
            </div>

            ${athHtml}

            <!-- Order Flow Bar -->
            <div class="orderflow-wrap">
              <div class="orderflow-header">
                <span class="of-label">Order Flow</span>
                <span class="of-ratio">
                  <span class="of-buy-txt">${buyRatio}% Buy</span>
                  <span style="color:var(--text-dim)">(${formatTx(buys)} / ${formatTx(sells)})</span>
                </span>
              </div>
              <div class="orderflow-bar">
                <div class="of-fill-buy" style="width:${buyRatio}%"></div>
                <div class="of-fill-sell" style="width:${100 - buyRatio}%"></div>
              </div>
            </div>

            <div class="card-row-vol">
              <div class="vol-tag"><span>5m:</span>&nbsp;<span class="${p5 >= 0 ? 'vol-pos' : 'vol-neg'}">${p5Str}</span></div>
              <div class="vol-tag"><span>1h:</span>&nbsp;<span class="${p1 >= 0 ? 'vol-pos' : 'vol-neg'}">${p1Str}</span></div>
              <div class="safety-block">
                <span class="safety-pill safety-${sGrade}" title="Audit Score: ${sScore}/100">${sGrade} ${sScore}</span>
                <span class="safety-sub">t10 ${t.top10_rate || 0}% · dev ${t.dev_team_hold || 0}%</span>
              </div>
            </div>

            <div class="card-row-bottom">
              <div class="state-pill ${spClass}" title="${spLabel}">
                <span>${spIcon}</span><span>${spLabel}</span>
              </div>
              <div class="card-actions">
                <a href="${dexsUrl}" target="_blank" rel="noopener noreferrer" class="btn-chart btn-chart-secondary" title="Buka di DexScreener / FOMO">
                  ${dexsLabel}
                </a>
                <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-chart" title="Buka Chart GMGN">
                  GMGN ↗
                </a>
              </div>
            </div>

            ${gapsHtml}
          </div>`;
      });

      html += `</div>`;
      container.innerHTML = html;
      updateFooter(filtered.length, globalState.total_scanned || 0);
    }

    function updateFooter(shown, total) {
      const el  = document.getElementById("footerInfo");
      const txt = document.getElementById("footerText");
      if (total > 0) {
        txt.innerText  = `Menampilkan ${shown} token · Total dipindai: ${total} token`;
        el.style.display = "block";
      } else {
        el.style.display = "none";
      }
    }

    /* ---- Fetch State API ---- */
    async function fetchState() {
      try {
        const res  = await fetch("/api/state?t=" + Date.now());
        const data = await res.json();
        if (!data || !data.ok) return;

        globalState = data;

        // KPI
        document.getElementById("kpiSiap").innerText     = data.counts.siap || 0;
        document.getElementById("kpiAbsorb").innerText   = data.counts.absorption || 0;
        document.getElementById("kpiTopYield").innerText = data.top_yield ? `$${data.top_yield.toFixed(2)}/h` : "$0.00";
        document.getElementById("kpiTime").innerText     = data.scanned_at || "--:--";
        const bathCount = (data.counts && data.counts.break_ath) || (data.break_ath ? data.break_ath.length : 0);
        document.getElementById("kpiBath").innerText     = bathCount;

        // Category Badges
        document.getElementById("badgeSiap").innerText   = data.counts.siap || 0;
        const bM5 = document.getElementById("badgeM5");
        if (bM5) bM5.innerText = (data.counts && data.counts.momentum_5m) || (data.momentum_5m ? data.momentum_5m.length : 0);
        document.getElementById("badgeAbsorb").innerText = data.counts.absorption || 0;
        document.getElementById("badgeBath").innerText   = bathCount;
        document.getElementById("badgeGaps").innerText   = data.counts.gaps || 0;

        renderCards();
        updateCountdown();

      } catch (err) {
        console.error("Fetch state error:", err);
      }
    }

    /* ---- Keyboard Shortcuts ---- */
    window.addEventListener("keydown", (e) => {
      if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
        if (e.key === "Escape") {
          document.activeElement.blur();
          closeModal();
        }
        return;
      }
      if (e.key === "Escape") closeModal();
      else if (e.key === "s" || e.key === "S") { e.preventDefault(); triggerScan(); }
      else if (e.key === "1") setCategoryTab("siap");
      else if (e.key === "2") setCategoryTab("momentum_5m");
      else if (e.key === "3") setCategoryTab("absorption");
      else if (e.key === "4") setCategoryTab("break_ath");
      else if (e.key === "5") setCategoryTab("gaps");
      else if (e.key === "/") {
        e.preventDefault();
        const inp = document.getElementById("tokenSearch");
        if (inp) inp.focus();
      }
      else if (e.key === "v" || e.key === "V") {
        e.preventDefault();
        setViewMode(viewMode === "cards" ? "table" : "cards");
        showToast(`Tampilan: ${viewMode === "cards" ? "Cards ⊞" : "Table ☰"}`);
      }
    });

    // Polling interval
    fetchState();
    setInterval(fetchState, 4000);
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(updateCountdown, 1000);
  </script>
</body>
</html>
"""



# ================= HTTP SERVER HANDLER =================

class MobileDashboardHandler(BaseHTTPRequestHandler):
    """Handler ringan untuk melayani Dashboard Mobile SPA dan API REST."""

    def log_message(self, format: str, *args: Any) -> None:
        # Menekan verbose access log agar console terminal tetap bersih
        return

    def send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            payload = HTML_DASHBOARD.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/state":
            with state_lock:
                now_ts = int(time.time())
                next_ts = app_state.get("next_scan_timestamp", 0)
                sec_left = max(0, next_ts - now_ts)
                data = {
                    "ok": True,
                    "scanning": app_state["scanning"],
                    "scanned_at": app_state["scanned_at"],
                    "next_scan_timestamp": app_state["next_scan_timestamp"],
                    "seconds_until_next": sec_left,
                    "counts": app_state["counts"],
                    "top_yield": app_state["top_yield"],
                    "filters": app_state["filters"],
                    "siap_lp": app_state["siap_lp"],
                    "momentum_5m": app_state.get("momentum_5m", []),
                    "absorption": app_state["absorption"],
                    "break_ath": app_state.get("break_ath", []),
                    "gaps": app_state["gaps"],
                }
            self.send_json(data)
            return

        if path in ("/api/health", "/health"):
            self.send_json({"status": "ok", "port": PORT, "time": datetime.now().isoformat()})
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/scan":
            with state_lock:
                is_busy = app_state["scanning"]

            if is_busy:
                self.send_json({"ok": False, "status": "already_scanning"})
                return

            threading.Thread(target=perform_scan, daemon=True).start()
            self.send_json({"ok": True, "status": "scanning_started"})
            return

        if path == "/api/filters":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                new_f = json.loads(body)

                with state_lock:
                    for k, v in new_f.items():
                        if k in app_state["filters"]:
                            app_state["filters"][k] = v
                    current_filters = dict(app_state["filters"])

                save_persistent_filters(current_filters)
                # Picu scan ulang dengan filter baru
                threading.Thread(target=perform_scan, daemon=True).start()
                self.send_json({"ok": True, "filters": current_filters})
                return
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=400)
                return

        self.send_response(404)
        self.end_headers()


def run_server() -> None:
    """Entry point untuk menjalankan web server dan background worker."""
    # 1. Inisialisasi filter dari konfigurasi
    initial_filters = load_persistent_filters()
    app_state["filters"] = initial_filters

    print("=" * 60)
    print("⚡ CHOP LP RADAR — MOBILE WEB DASHBOARD (PORT 8771)")
    print("=" * 60)
    print(f"📡 Shared Engine : bot_sol_lp.py (GMGN Open API Multi-Chain)")
    print(f"🔸 Solana        : Aktif (GMGN / Fallback Meteora)")
    print(f"🔹 Robinhood     : Aktif (GMGN Open API)")
    print(f"🎯 Strategi      : Chop Sideways LP Farming (100% Bot Parity)")
    print(f"⚙️ Parameter     : Min Fee ${initial_filters.get('min_fee_siap_lp')}/h │ MC ≥ {bot_sol_lp._usd(initial_filters.get('min_mcap', 500000))}")
    print(f"🌐 Akses Browser : http://localhost:{PORT} atau http://<IP_VPS>:{PORT}")
    print("=" * 60)

    # 2. Mulai background worker
    worker = threading.Thread(target=background_scanner_worker, daemon=True)
    worker.start()

    # 3. Jalankan HTTP server
    server = ThreadingHTTPServer((HOST, PORT), MobileDashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Menutup web dashboard...")
        server.server_close()


if __name__ == "__main__":
    run_server()
