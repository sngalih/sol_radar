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
    "absorption": [],
    "break_ath": [],
    "gaps": [],
    "counts": {"siap": 0, "absorption": 0, "break_ath": 0, "gaps": 0, "total": 0},
    "top_yield": 0.0,
    "filters": {},
}


def load_persistent_filters() -> dict[str, Any]:
    """Memuat filter dari sol-hp-filters.json atau gunakan default dari bot_sol_lp."""
    conf = dict(bot_sol_lp.get_config())
    # Ensure mobile-specific defaults match bot
    defaults = {
        "position_usd": 100.0,
        "min_fee_siap_lp": 3.0,
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
    min_fee_siap_lp = float(conf.get("min_fee_siap_lp", 3.0))
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

        top_yield = siap_lp[0]["fee_hour"] if siap_lp else (absorption[0]["fee_hour"] if absorption else 0.0)

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
            app_state["absorption"] = absorption
            app_state["break_ath"] = break_ath
            app_state["gaps"] = gaps[:40]  # Limit agar tidak membebani browser HP
            app_state["counts"] = {
                "siap": len(siap_lp),
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
  <meta name="theme-color" content="#090d16">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>⚡ Chop LP Radar</title>
  <style>
    /* ===== DESIGN TOKENS ===== */
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f293d;
      --card-hover: #162035;
      --text-main: #f8fafc;
      --text-sub: #94a3b8;
      --green: #10b981;
      --green-light: #34d399;
      --green-bg: rgba(16, 185, 129, 0.12);
      --sol: #ff9800;
      --sol-bg: rgba(255, 152, 0, 0.12);
      --rh: #3b82f6;
      --rh-bg: rgba(59, 130, 246, 0.12);
      --blue: #3b82f6;
      --blue-bg: rgba(59, 130, 246, 0.12);
      --red: #ef4444;
      --red-bg: rgba(239, 68, 68, 0.12);
      --yellow: #f59e0b;
      --yellow-bg: rgba(245, 158, 11, 0.12);
      --cyan: #38bdf8;
      --cyan-bg: rgba(56, 189, 248, 0.12);
      --safe-top: env(safe-area-inset-top, 0px);
      --safe-bottom: env(safe-area-inset-bottom, 0px);
      --container-max: 1100px;
      --radius-card: 14px;
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
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.45;
      padding-top: var(--safe-top);
      padding-bottom: calc(var(--safe-bottom) + 32px);
      -webkit-font-smoothing: antialiased;
    }

    /* ===== HEADER ===== */
    .app-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(9, 13, 22, 0.95);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      border-bottom: 1px solid var(--card-border);
      padding: 11px 16px 9px;
    }

    .header-inner {
      max-width: var(--container-max);
      margin: 0 auto;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 9px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .brand-title {
      font-weight: 800;
      font-size: 17px;
      letter-spacing: -0.3px;
      background: linear-gradient(135deg, #ffffff 30%, #94a3b8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }

    .brand-badge {
      font-size: 10px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 6px;
      background: var(--green-bg);
      color: var(--green);
      border: 1px solid rgba(16, 185, 129, 0.3);
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }

    .status-pulse {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 600;
      color: var(--green);
      background: var(--green-bg);
      padding: 4px 10px;
      border-radius: 20px;
      border: 1px solid rgba(16, 185, 129, 0.25);
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
      padding: 6px 10px;
      font-size: 13px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
      transition: background 0.15s, box-shadow 0.15s, transform 0.1s;
    }
    .btn-icon:hover { background: var(--card-hover); border-color: rgba(255,255,255,0.12); }
    .btn-icon:active { transform: scale(0.95); }

    .btn-scan {
      background: linear-gradient(135deg, #10b981, #059669);
      color: #ffffff;
      border: none;
      box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
    }
    .btn-scan:hover { box-shadow: 0 4px 14px rgba(16, 185, 129, 0.45); background: linear-gradient(135deg, #10b981, #059669); }

    /* ===== CHAIN SWITCHER ===== */
    .chain-segmented {
      display: flex;
      background: rgba(13, 19, 34, 0.8);
      padding: 3px;
      border-radius: 12px;
      border: 1px solid var(--card-border);
      gap: 3px;
    }

    .chain-tab {
      flex: 1;
      text-align: center;
      padding: 7px 4px;
      font-size: 12px;
      font-weight: 700;
      color: var(--text-sub);
      border-radius: 9px;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      user-select: none;
      white-space: nowrap;
    }
    .chain-tab:hover { color: var(--text-main); }

    .chain-tab.active {
      background: var(--card-bg);
      color: var(--text-main);
      box-shadow: 0 2px 6px rgba(0,0,0,0.4);
      border: 1px solid var(--card-border);
    }
    .chain-tab.active[data-chain="sol"] { color: var(--sol); border-color: rgba(255,152,0,0.35); }
    .chain-tab.active[data-chain="rh"]  { color: var(--rh);  border-color: rgba(59,130,246,0.35); }

    /* ===== MAIN CONTAINER ===== */
    .container {
      padding: 14px 16px;
      max-width: var(--container-max);
      margin: 0 auto;
    }

    /* ===== KPI GRID — 5 items ===== */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 8px;
      margin-bottom: 14px;
    }

    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 9px 8px;
      text-align: center;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .kpi-card:hover {
      border-color: rgba(255,255,255,0.12);
      box-shadow: 0 2px 12px rgba(0,0,0,0.25);
    }

    .kpi-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-sub);
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-bottom: 3px;
    }

    .kpi-val {
      font-size: 15px;
      font-weight: 800;
      color: var(--text-main);
    }
    .kpi-val.green { color: var(--green); }
    .kpi-val.yellow { color: var(--yellow); }
    .kpi-val.cyan { color: var(--cyan); }

    /* ===== CATEGORY TABS ===== */
    .cat-tabs {
      display: flex;
      gap: 6px;
      margin-bottom: 14px;
      overflow-x: auto;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
    }
    .cat-tabs::-webkit-scrollbar { display: none; }

    .cat-tab {
      flex: 1;
      min-width: 78px;
      padding: 8px 8px;
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
      transition: all 0.2s ease;
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
    }

    .cat-tab.active { background: #172033; border-color: var(--green); color: var(--green); }
    .cat-tab.active[data-cat="absorption"] { border-color: var(--blue); color: var(--blue); }
    .cat-tab.active[data-cat="break_ath"]  { border-color: var(--cyan); color: var(--cyan); }
    .cat-tab.active[data-cat="gaps"]       { border-color: var(--yellow); color: var(--yellow); }

    .cat-tab.active .badge-count                          { background: rgba(16,185,129,0.2); color: var(--green); }
    .cat-tab.active[data-cat="absorption"] .badge-count  { background: rgba(59,130,246,0.2); color: var(--blue); }
    .cat-tab.active[data-cat="break_ath"]  .badge-count  { background: rgba(56,189,248,0.2); color: var(--cyan); }
    .cat-tab.active[data-cat="gaps"]       .badge-count  { background: rgba(245,158,11,0.2); color: var(--yellow); }

    /* ===== CARD LIST (responsive grid) ===== */
    .card-list {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }

    /* ===== TOKEN CARD ===== */
    .token-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-card);
      padding: 14px 15px;
      position: relative;
      transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.12s ease;
    }
    .token-card:hover {
      border-color: rgba(255,255,255,0.13);
      box-shadow: 0 6px 24px rgba(0,0,0,0.3);
    }
    .token-card:active { transform: scale(0.985); }
    .token-card.sol-card { border-left: 3px solid var(--sol); }
    .token-card.rh-card  { border-left: 3px solid var(--rh); }

    /* Card: top row */
    .card-row-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 10px;
      gap: 8px;
    }

    .token-info-left {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .chain-pill {
      font-size: 11px;
      font-weight: 800;
      padding: 2px 7px;
      border-radius: 7px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      flex-shrink: 0;
    }
    .chain-pill.sol { background: var(--sol-bg); color: var(--sol); border: 1px solid rgba(255,152,0,0.3); }
    .chain-pill.rh  { background: var(--rh-bg);  color: var(--rh);  border: 1px solid rgba(59,130,246,0.3); }

    .token-name-block { min-width: 0; }
    .token-symbol {
      font-size: 17px;
      font-weight: 800;
      letter-spacing: -0.2px;
      color: #ffffff;
      display: block;
    }
    .token-name {
      font-size: 11px;
      color: var(--text-sub);
      max-width: 180px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      display: block;
    }

    .token-fee-right { text-align: right; flex-shrink: 0; }
    .fee-hour { font-size: 18px; font-weight: 800; color: var(--green-light); letter-spacing: -0.3px; }
    .fee-day  { font-size: 10px; color: var(--text-sub); font-weight: 600; }

    /* Card: metrics grid */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: rgba(13,19,34,0.7);
      border-radius: 10px;
      padding: 8px 6px;
      margin-bottom: 10px;
      border: 1px solid rgba(255,255,255,0.04);
      gap: 4px;
    }

    .metric-cell { text-align: center; }
    .m-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-sub);
      text-transform: uppercase;
      margin-bottom: 3px;
      letter-spacing: 0.2px;
    }
    .m-val { font-size: 12px; font-weight: 700; color: var(--text-main); }

    .er-badge {
      display: inline-block;
      padding: 1px 5px;
      border-radius: 5px;
      font-weight: 800;
      font-size: 11px;
    }
    .er-prime { background: rgba(16,185,129,0.25); color: #34d399; }
    .er-good  { background: rgba(59,130,246,0.25); color: #60a5fa; }
    .er-mid   { background: rgba(245,158,11,0.25); color: #fbbf24; }
    .er-high  { background: rgba(239,68,68,0.25);  color: #f87171; }

    /* Card: vol row */
    .card-row-vol {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 11px;
      font-weight: 600;
      padding: 0 2px;
      margin-bottom: 10px;
      color: var(--text-sub);
    }
    .vol-tag { display: inline-flex; align-items: center; gap: 3px; }
    .vol-pos { color: var(--green); }
    .vol-neg { color: var(--red); }

    /* Card: Break ATH extra info */
    .ath-info-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 10px;
      padding: 7px 10px;
      background: rgba(56,189,248,0.07);
      border-radius: 9px;
      border: 1px solid rgba(56,189,248,0.18);
    }
    .ath-tag {
      font-size: 11px;
      font-weight: 700;
      color: var(--cyan);
      display: inline-flex;
      align-items: center;
      gap: 3px;
    }
    .ath-tag .ath-label { color: var(--text-sub); font-weight: 500; }

    /* Card: bottom row */
    .card-row-bottom {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border-top: 1px dashed rgba(31,41,61,0.8);
      padding-top: 10px;
    }

    .state-pill {
      font-size: 11px;
      font-weight: 700;
      padding: 4px 9px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 62%;
    }
    .state-absorption { background: var(--blue-bg);  color: var(--blue);   border: 1px solid rgba(59,130,246,0.3); }
    .state-chop       { background: var(--green-bg); color: var(--green);  border: 1px solid rgba(16,185,129,0.3); }
    .state-reaccum    { background: var(--yellow-bg);color: var(--yellow); border: 1px solid rgba(245,158,11,0.3); }
    .state-neutral    { background: rgba(255,255,255,0.06); color: var(--text-sub); border: 1px solid rgba(255,255,255,0.1); }
    .state-distrib    { background: var(--red-bg);   color: var(--red);    border: 1px solid rgba(239,68,68,0.3); }
    .state-ath        { background: var(--cyan-bg);  color: var(--cyan);   border: 1px solid rgba(56,189,248,0.3); }

    .btn-gmgn {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      text-decoration: none;
      font-size: 11px;
      font-weight: 700;
      padding: 6px 13px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: none;
      box-shadow: 0 2px 6px rgba(37,99,235,0.3);
      flex-shrink: 0;
      cursor: pointer;
      transition: box-shadow 0.15s, transform 0.1s;
    }
    .btn-gmgn:hover { box-shadow: 0 4px 14px rgba(37,99,235,0.5); }
    .btn-gmgn:active { transform: scale(0.95); }

    /* Gap reasons */
    .gap-reasons-box { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; }
    .gap-pill {
      font-size: 10px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 6px;
      background: rgba(239,68,68,0.12);
      color: #f87171;
      border: 1px solid rgba(239,68,68,0.25);
    }

    /* ===== EMPTY STATE ===== */
    .empty-box {
      background: var(--card-bg);
      border: 1px dashed var(--card-border);
      border-radius: 16px;
      padding: 40px 24px;
      text-align: center;
      color: var(--text-sub);
      grid-column: 1 / -1;
    }
    .empty-icon  { font-size: 36px; margin-bottom: 10px; }
    .empty-title { font-size: 16px; font-weight: 700; color: var(--text-main); margin-bottom: 6px; }
    .empty-desc  { font-size: 13px; line-height: 1.6; }

    /* ===== FOOTER INFO ===== */
    .footer-info {
      margin-top: 18px;
      text-align: center;
      font-size: 11px;
      color: var(--text-sub);
      opacity: 0.7;
    }

    /* ===== MODAL ===== */
    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.72);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      z-index: 200;
      align-items: flex-end;
      justify-content: center;
    }
    .modal-overlay.show { display: flex; }

    /* Mobile: drawer from bottom */
    .modal-sheet {
      background: #111827;
      border-top-left-radius: 20px;
      border-top-right-radius: 20px;
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
      margin-bottom: 16px;
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
      background: #1f293d;
      border: none;
      color: var(--text-sub);
      width: 30px;
      height: 30px;
      border-radius: 50%;
      font-size: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    }
    .modal-close:hover { background: #2d3f5c; color: var(--text-main); }

    .form-section-title {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-sub);
      margin: 18px 0 10px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--card-border);
    }
    .form-section-title:first-of-type { margin-top: 0; }

    .form-group { margin-bottom: 13px; }

    .form-label {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-sub);
      margin-bottom: 5px;
    }

    .form-input {
      width: 100%;
      background: rgba(13,19,34,0.9);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
      font-weight: 600;
      color: #ffffff;
      outline: none;
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
      transition: box-shadow 0.15s;
    }
    .btn-submit:hover { box-shadow: 0 4px 14px rgba(16,185,129,0.4); }

    .btn-reset {
      background: #1f293d;
      color: var(--text-sub);
      border: none;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-reset:hover { background: #2d3f5c; color: var(--text-main); }

    /* ===== TOAST ===== */
    .toast {
      position: fixed;
      top: 20px;
      left: 50%;
      transform: translateX(-50%) translateY(-16px);
      background: rgba(17,24,39,0.97);
      border: 1px solid var(--green);
      color: #ffffff;
      padding: 8px 20px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 700;
      z-index: 300;
      box-shadow: 0 6px 24px rgba(0,0,0,0.5);
      opacity: 0;
      pointer-events: none;
      transition: all 0.25s ease;
      white-space: nowrap;
    }
    .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }

    /* ===== RESPONSIVE BREAKPOINTS ===== */

    /* Mobile small: KPI 3+2 wrap */
    @media (max-width: 479px) {
      .kpi-grid {
        grid-template-columns: repeat(3, 1fr);
      }
      .kpi-grid .kpi-card:nth-child(4),
      .kpi-grid .kpi-card:nth-child(5) {
        /* 2 remaining cards span within 3-col, auto place */
      }
    }

    /* Tablet: 2-column cards, bigger fonts, center modal */
    @media (min-width: 769px) {
      .brand-title { font-size: 19px; }
      .container { padding: 18px 24px; }

      .kpi-label { font-size: 11px; }
      .kpi-val   { font-size: 16px; }
      .kpi-card  { padding: 11px 10px; }

      .cat-tab { font-size: 12px; padding: 9px 12px; min-width: 90px; }

      .card-list { grid-template-columns: repeat(2, 1fr); gap: 12px; }

      .m-label { font-size: 10px; }
      .m-val   { font-size: 13px; }
      .token-name { max-width: 200px; }

      /* Modal becomes center dialog on tablet/desktop */
      .modal-overlay { align-items: center; }
      .modal-sheet {
        border-radius: 18px;
        border: 1px solid var(--card-border);
        width: 520px;
        max-width: 90vw;
        max-height: 82vh;
        animation: fadeScaleIn 0.2s ease-out;
      }
    }

    /* Desktop: 3-column cards */
    @media (min-width: 1100px) {
      .card-list { grid-template-columns: repeat(3, 1fr); }
      .brand-title { font-size: 20px; }
      .cat-tab { font-size: 13px; }
    }
  </style>
</head>
<body>

  <!-- ===== HEADER ===== -->
  <header class="app-header">
    <div class="header-inner">
      <div class="header-top">
        <div class="brand">
          <span class="brand-title">⚡ CHOP RADAR</span>
          <span class="brand-badge">LIVE</span>
        </div>
        <div id="statusPulse" class="status-pulse">
          <span class="pulse-dot"></span>
          <span id="statusText">LIVE</span>
        </div>
        <div class="header-actions">
          <button id="btnScan" class="btn-icon btn-scan" onclick="triggerScan()">
            <span>⚡</span>
            <span id="scanCountdown">SCAN</span>
          </button>
          <button class="btn-icon" onclick="openModal()" title="Pengaturan Filter">
            <span>⚙️</span>
          </button>
        </div>
      </div>

      <!-- Chain Switcher -->
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
    </div>
  </header>

  <!-- ===== MAIN ===== -->
  <main class="container">

    <!-- KPI Grid (5 items) -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Siap LP</div>
        <div id="kpiSiap" class="kpi-val green">0</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Top $/h</div>
        <div id="kpiTopYield" class="kpi-val green">$0.00</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Absorb</div>
        <div id="kpiAbsorb" class="kpi-val">0</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">🚀 ATH</div>
        <div id="kpiBath" class="kpi-val cyan">0</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Update</div>
        <div id="kpiTime" class="kpi-val yellow">--:--</div>
      </div>
    </div>

    <!-- Category Tabs -->
    <div class="cat-tabs">
      <div class="cat-tab active" data-cat="siap" onclick="setCategoryTab('siap')">
        <span>🟢 SIAP LP</span>
        <span id="badgeSiap" class="badge-count">0</span>
      </div>
      <div class="cat-tab" data-cat="absorption" onclick="setCategoryTab('absorption')">
        <span>📡 ABSORB</span>
        <span id="badgeAbsorb" class="badge-count">0</span>
      </div>
      <div class="cat-tab" data-cat="break_ath" onclick="setCategoryTab('break_ath')">
        <span>🚀 ATH</span>
        <span id="badgeBath" class="badge-count">0</span>
      </div>
      <div class="cat-tab" data-cat="gaps" onclick="setCategoryTab('gaps')">
        <span>⚠️ GAPS</span>
        <span id="badgeGaps" class="badge-count">0</span>
      </div>
    </div>

    <!-- Token Cards Feed -->
    <div id="tokenCardsList" class="card-list"></div>

    <!-- Footer Info -->
    <div class="footer-info" id="footerInfo" style="display:none">
      <span id="footerText"></span>
    </div>

  </main>

  <!-- ===== FILTER MODAL ===== -->
  <div id="filterModal" class="modal-overlay" onclick="closeModalOnBg(event)">
    <div class="modal-sheet">
      <div class="modal-header">
        <div class="modal-title">⚙️ Parameter Filter LP</div>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>

      <div class="form-section-title">📊 Siap LP / Chop Filter</div>

      <div class="form-group">
        <div class="form-label">
          <span>Min Fee Siap LP ($/jam)</span>
          <span style="color:var(--green)">Modal $100</span>
        </div>
        <input type="number" step="0.5" id="f_min_fee" class="form-input" value="3.0">
      </div>

      <div class="form-group">
        <div class="form-label">
          <span>Min Market Cap ($)</span>
          <span>Filter micap liar</span>
        </div>
        <input type="number" step="50000" id="f_min_mcap" class="form-input" value="500000">
      </div>

      <div class="form-group">
        <div class="form-label">
          <span>Min Likuiditas / TVL ($)</span>
          <span>Pool aman</span>
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
          <span>Sweet spot ≤ 20</span>
        </div>
        <input type="number" step="1" id="f_max_er" class="form-input" value="20.0">
      </div>

      <div class="form-section-title">⚙️ Pengaturan Umum</div>

      <div class="form-group">
        <div class="form-label">
          <span>Modal Simulasi Posisi ($)</span>
          <span>Untuk hitung $/jam</span>
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

      <div class="modal-actions">
        <button class="btn-submit" onclick="saveFilters()">💾 Simpan &amp; Terapkan</button>
        <button class="btn-reset" onclick="resetDefaultFilters()">Reset</button>
      </div>
    </div>
  </div>

  <!-- Toast -->
  <div id="toast" class="toast">Notifikasi</div>

  <script>
    let globalState = null;
    let activeChain = 'all';
    let activeCategory = 'siap';
    let countdownInterval = null;

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

    function showToast(msg) {
      const t = document.getElementById("toast");
      t.innerText = msg;
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 2600);
    }

    /* ---- Chain & Category Filters ---- */
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

    /* ---- Modal ---- */
    function openModal() {
      if (globalState && globalState.filters) {
        const f = globalState.filters;
        if (f.min_fee_siap_lp !== undefined) document.getElementById("f_min_fee").value = f.min_fee_siap_lp;
        if (f.min_mcap       !== undefined) document.getElementById("f_min_mcap").value = f.min_mcap;
        if (f.min_liq        !== undefined) document.getElementById("f_min_liq").value  = f.min_liq;
        if (f.min_vl         !== undefined) document.getElementById("f_min_vl").value   = f.min_vl;
        if (f.max_5m         !== undefined) document.getElementById("f_max_5m").value   = f.max_5m;
        if (f.max_1h         !== undefined) document.getElementById("f_max_1h").value   = f.max_1h;
        if (f.max_er         !== undefined) document.getElementById("f_max_er").value   = f.max_er;
        if (f.position_usd   !== undefined) document.getElementById("f_position").value = f.position_usd;
        if (f.interval_sec   !== undefined) document.getElementById("f_interval").value = f.interval_sec;
      }
      document.getElementById("filterModal").classList.add("show");
    }

    function closeModal() {
      document.getElementById("filterModal").classList.remove("show");
    }

    function closeModalOnBg(e) {
      if (e.target.id === "filterModal") closeModal();
    }

    /* ---- Scan ---- */
    async function triggerScan() {
      const btn = document.getElementById("btnScan");
      btn.style.opacity = "0.6";
      showToast("⏳ Memulai pemindaian data GMGN...");
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

    /* ---- Filters ---- */
    async function saveFilters() {
      const payload = {
        min_fee_siap_lp: parseFloat(document.getElementById("f_min_fee").value)  || 3.0,
        min_mcap:        parseFloat(document.getElementById("f_min_mcap").value) || 500000,
        min_liq:         parseFloat(document.getElementById("f_min_liq").value)  || 20000,
        min_vl:          parseFloat(document.getElementById("f_min_vl").value)   || 2.0,
        max_5m:          parseFloat(document.getElementById("f_max_5m").value)   || 15.0,
        max_1h:          parseFloat(document.getElementById("f_max_1h").value)   || 80.0,
        max_er:          parseFloat(document.getElementById("f_max_er").value)   || 20.0,
        position_usd:    parseFloat(document.getElementById("f_position").value) || 100.0,
        interval_sec:    parseInt(document.getElementById("f_interval").value)   || 300,
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
          showToast("✅ Filter berhasil disimpan!");
          fetchState();
        }
      } catch (err) {
        alert("Gagal menyimpan filter: " + err);
      }
    }

    function resetDefaultFilters() {
      document.getElementById("f_min_fee").value  = 3.0;
      document.getElementById("f_min_mcap").value = 500000;
      document.getElementById("f_min_liq").value  = 20000;
      document.getElementById("f_min_vl").value   = 2.0;
      document.getElementById("f_max_5m").value   = 15.0;
      document.getElementById("f_max_1h").value   = 80.0;
      document.getElementById("f_max_er").value   = 20.0;
      document.getElementById("f_position").value = 100.0;
      document.getElementById("f_interval").value = 300;
    }

    /* ---- Countdown ---- */
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

    /* ---- Render Cards ---- */
    function renderCards() {
      const container = document.getElementById("tokenCardsList");
      if (!globalState) {
        container.innerHTML = `<div class="empty-box"><div class="empty-icon">⏳</div><div class="empty-title">Memuat data...</div></div>`;
        return;
      }

      // Select data source
      let rawList = [];
      if      (activeCategory === "siap")      rawList = globalState.siap_lp    || [];
      else if (activeCategory === "absorption") rawList = globalState.absorption || [];
      else if (activeCategory === "break_ath")  rawList = globalState.break_ath  || [];
      else                                      rawList = globalState.gaps        || [];

      // Chain count badges (header switcher)
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

      if (activeCategory === "gaps") {
        filtered = [...filtered].sort((a, b) => (b.fee_hour || 0) - (a.fee_hour || 0) || (b.vol || 0) - (a.vol || 0));
      }

      // Empty state
      if (!filtered.length) {
        const msgs = {
          siap:      "Belum ada token memenuhi kriteria Siap LP (Fee ≥ $3/h & MC ≥ $500k).",
          absorption:"Belum ada sinyal akumulasi/absorption terdeteksi saat ini.",
          break_ath: "Belum ada token Break ATH terkonfirmasi (≥ 15m, Fee ≥ $3/h, ATH > $500k).",
          gaps:      "Tidak ada token radar yang berada di luar kriteria.",
        };
        container.innerHTML = `
          <div class="empty-box">
            <div class="empty-icon">🔍</div>
            <div class="empty-title">Tidak Ada Token</div>
            <div class="empty-desc">${msgs[activeCategory] || msgs.siap}<br>Coba ubah tab Chain atau sesuaikan filter di menu ⚙️.</div>
          </div>`;
        updateFooter(0, globalState.total_scanned || 0);
        return;
      }

      // Build card HTML
      let html = "";
      filtered.forEach(t => {
        const isRh     = (t.chain || "SOL").toUpperCase() === "RH";
        const chainBadge      = isRh
          ? `<span class="chain-pill rh">🔹 RH</span>`
          : `<span class="chain-pill sol">🔸 SOL</span>`;
        const cardBorderClass = isRh ? "rh-card" : "sol-card";

        const feeHour = t.fee_hour ? `$${t.fee_hour.toFixed(2)}/h` : "$0.00/h";
        const feeDay  = t.fee_24h  ? `+$${t.fee_24h.toFixed(1)}/24h` : "";

        const mcapStr = formatUsd(t.mcap || 0);
        const liqStr  = formatUsd(t.liq  || 0);
        const vlStr   = (t.vl || 0).toFixed(1) + "x";

        // ER badge
        const erVal = t.er !== undefined ? t.er : 999;
        let erClass = "er-high";
        if      (erVal <= 3)  erClass = "er-prime";
        else if (erVal <= 6)  erClass = "er-good";
        else if (erVal <= 15) erClass = "er-mid";
        const erBadge = `<span class="er-badge ${erClass}">ER ${erVal.toFixed(1)}</span>`;

        // Volatility
        const p5 = t.p5 || 0, p1 = t.p1 || 0;
        const p5Str = (p5 >= 0 ? "+" : "") + p5.toFixed(1) + "%";
        const p1Str = (p1 >= 0 ? "+" : "") + p1.toFixed(1) + "%";

        // State pill
        const mState = t.micro_state || "NEUTRAL";
        let spClass = "state-neutral", spIcon = "🎯";
        if (activeCategory === "break_ath") { spClass = "state-ath"; spIcon = "🚀"; }
        else if (mState === "ABSORPTION")   { spClass = "state-absorption"; spIcon = "📡"; }
        else if (mState === "REACCUMULATION"){ spClass = "state-reaccum"; spIcon = "🔄"; }
        else if (mState === "DISTRIBUTION") { spClass = "state-distrib"; spIcon = "⚠️"; }
        else if (t.is_chop)                 { spClass = "state-chop"; spIcon = "🟢"; }

        const spLabel = activeCategory === "break_ath"
          ? "Break ATH ✓"
          : (t.status_label || (t.is_chop ? "Chopping Sideways" : "Monitoring"));

        // Break ATH extra info row
        let athHtml = "";
        if (activeCategory === "break_ath") {
          const bp  = t.breakout_pct !== undefined ? `+${t.breakout_pct}%` : "—";
          const dur = t.duration_mins !== undefined ? `${t.duration_mins}m` : "—";
          const athOld = t.ath_old ? formatUsd(t.ath_old) : "—";
          athHtml = `
            <div class="ath-info-row">
              <div class="ath-tag">🔼 <span class="ath-label">Breakout:</span> ${bp}</div>
              <div class="ath-tag">⏱ <span class="ath-label">Durasi:</span> ${dur}</div>
              <div class="ath-tag">📊 <span class="ath-label">ATH Lama:</span> ${athOld}</div>
            </div>`;
        }

        // Gaps reasons
        let gapsHtml = "";
        if (activeCategory === "gaps" && t.gap_reasons && t.gap_reasons.length) {
          gapsHtml = `<div class="gap-reasons-box">` +
            t.gap_reasons.map(r => `<span class="gap-pill">✕ ${r}</span>`).join("") +
            `</div>`;
        }

        html += `
          <div class="token-card ${cardBorderClass}">
            <div class="card-row-top">
              <div class="token-info-left">
                ${chainBadge}
                <div class="token-name-block">
                  <span class="token-symbol">${t.symbol || "?"}</span>
                  <span class="token-name">${t.name || ""}</span>
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

            <div class="card-row-vol">
              <div class="vol-tag"><span>5m:</span>&nbsp;<span class="${p5 >= 0 ? 'vol-pos' : 'vol-neg'}">${p5Str}</span></div>
              <div class="vol-tag"><span>1h:</span>&nbsp;<span class="${p1 >= 0 ? 'vol-pos' : 'vol-neg'}">${p1Str}</span></div>
              <div class="vol-tag"><span>Buy:</span>&nbsp;<span>${t.buy_ratio || 50}%</span></div>
              <div class="vol-tag"><span>Score:</span>&nbsp;<span style="color:var(--text-main);font-weight:700">${t.score || 0}</span></div>
            </div>

            <div class="card-row-bottom">
              <div class="state-pill ${spClass}" title="${spLabel}">
                <span>${spIcon}</span><span>${spLabel}</span>
              </div>
              <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-gmgn">
                GMGN ↗
              </a>
            </div>

            ${gapsHtml}
          </div>`;
      });

      container.innerHTML = html;
      updateFooter(filtered.length, globalState.total_scanned || 0);
    }

    function updateFooter(shown, total) {
      const el  = document.getElementById("footerInfo");
      const txt = document.getElementById("footerText");
      if (total > 0) {
        txt.innerText  = `Menampilkan ${shown} token · Total dimonitor: ${total}`;
        el.style.display = "block";
      } else {
        el.style.display = "none";
      }
    }

    /* ---- Fetch State ---- */
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

        // Category badges
        document.getElementById("badgeSiap").innerText   = data.counts.siap || 0;
        document.getElementById("badgeAbsorb").innerText = data.counts.absorption || 0;
        document.getElementById("badgeBath").innerText   = bathCount;
        document.getElementById("badgeGaps").innerText   = data.counts.gaps || 0;

        renderCards();
        updateCountdown();

      } catch (err) {
        console.error("Fetch state error:", err);
      }
    }

    // Poll every 4s + countdown every 1s
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
