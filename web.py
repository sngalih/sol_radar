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
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
    "akashi_zone": [],
    "slow_cook_lp": [],
    "dip_chop": [],
    "smart_lp": [],
    "flip_lp": [],
    "cto_lp": [],
    "momentum_5m": [],
    "absorption": [],
    "break_ath": [],
    "gaps": [],
    "signal_history": [],
    "counts": {"siap": 0, "cto": 0, "momentum_5m": 0, "absorption": 0, "break_ath": 0, "gaps": 0, "history": 0, "total": 0},
    "top_yield": 0.0,
    "top_vl": 0.0,
    "filters": {},
}


def load_persistent_filters() -> dict[str, Any]:
    """Memuat filter dari sol-hp-filters.json atau gunakan default dari bot_sol_lp."""
    conf = dict(bot_sol_lp.get_config())
    # Ensure mobile-specific defaults match bot
    defaults = {
        "position_usd": 100.0,
        "min_fee_siap_lp": 0.50,
        "min_fee_break_ath": 0.50,
        "momentum_5m_min_vol": 100000.0,
        "momentum_5m_min_liq": 10000.0,
        "momentum_5m_min_fee": 0.50,
        "min_liq": 20000.0,
        "min_mcap": 1000000.0,
        "max_mcap": 500000000.0,
        "min_age_hours": 12.0,
        "min_vl": 0.5,
        "max_5m": 15.0,
        "max_1h": 20.0,
        "max_drop_5m": -4.0,
        "max_drop_1h": -8.0,
        "min_buy_ratio": 46.0,
        "max_ath_drawdown": -85.0,
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
                if saved.get("min_fee_siap_lp") in (1.0, 3.0):
                    saved["min_fee_siap_lp"] = 0.50
                if saved.get("min_fee_break_ath") in (1.0, 3.0):
                    saved["min_fee_break_ath"] = 0.50
                if saved.get("min_vl") in (0.6, 2.0):
                    saved["min_vl"] = 0.5
                if saved.get("max_1h") == 80.0:
                    saved["max_1h"] = 20.0
                if saved.get("min_mcap") == 500000.0:
                    saved["min_mcap"] = 1000000.0
                if "min_age_hours" not in saved:
                    saved["min_age_hours"] = 12.0
                if "min_buy_ratio" not in saved:
                    saved["min_buy_ratio"] = 46.0
                if "max_ath_drawdown" not in saved:
                    saved["max_ath_drawdown"] = -85.0
                if "max_drop_1h" not in saved:
                    saved["max_drop_1h"] = -8.0
                if "max_drop_5m" not in saved:
                    saved["max_drop_5m"] = -4.0

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


def perform_scan(force: bool = False) -> None:
    """Eksekusi pemindaian token SOL + RH menggunakan shared engine bot_sol_lp.
    Memanfaatkan cache terpusat (TTL 50s) sehingga tidak ada duplikasi request ke GMGN.
    """
    global app_state

    with state_lock:
        if app_state["scanning"]:
            return
        app_state["scanning"] = True

    t0 = time.time()
    try:
        latest_filters = load_persistent_filters()
        with state_lock:
            app_state["filters"].update(latest_filters)
        conf = dict(app_state["filters"])

        # Panggil shared scan engine dari bot_sol_lp (memakai cache jika fresh < 50s)
        res = bot_sol_lp.execute_full_scan(conf, force=force)

        siap_lp = res.get("siap_lp", [])
        akashi_zone = res.get("akashi_zone", [])
        slow_cook_lp = res.get("slow_cook_lp", [])
        dip_chop = res.get("dip_chop", [])
        smart_lp = res.get("smart_lp", [])
        flip_lp = res.get("flip_lp", [])
        cto_lp = res.get("cto_lp", [])
        momentum_5m = res.get("momentum_5m", [])
        absorption = res.get("absorption", [])
        break_ath = res.get("break_ath", [])
        gaps = res.get("gaps", [])
        signal_history = res.get("signal_history", [])
        if not signal_history:
            signal_history = bot_sol_lp.get_aggregated_signal_history()

        with state_lock:
            app_state["scanning"] = False
            app_state["scanned_at"] = res.get("scanned_at")
            app_state["scanned_timestamp"] = res.get("scanned_timestamp", 0)
            app_state["next_scan_timestamp"] = res.get("next_scan_timestamp", 0)
            app_state["total_scanned"] = res.get("total_scanned", 0)
            app_state["siap_lp"] = siap_lp
            app_state["akashi_zone"] = akashi_zone
            app_state["slow_cook_lp"] = slow_cook_lp
            app_state["dip_chop"] = dip_chop
            app_state["smart_lp"] = smart_lp
            app_state["flip_lp"] = flip_lp
            app_state["cto_lp"] = cto_lp
            app_state["momentum_5m"] = momentum_5m
            app_state["absorption"] = absorption
            app_state["break_ath"] = break_ath
            app_state["gaps"] = gaps[:40]  # Limit agar tidak membebani browser HP
            app_state["signal_history"] = signal_history
            c_dict = dict(res.get("counts", {}))
            c_dict["history"] = len(signal_history)
            app_state["counts"] = c_dict
            app_state["top_yield"] = res.get("top_yield", 0.0)
            app_state["top_vl"] = res.get("top_vl", 0.0)

        print(
            f"[{get_wib_str()}] [Web Sync] Selesai dalam {time.time() - t0:.2f}s | "
            f"Total: {res.get('total_scanned', 0)} | Siap LP: {len(siap_lp)} | CTO: {len(cto_lp)} | Absorption: {len(absorption)} | History 24h: {len(signal_history)}"
        )

    except Exception as e:
        print(f"[Web Scan Error]: {e}", file=sys.stderr)
        with state_lock:
            app_state["scanning"] = False
            interval = int(app_state["filters"].get("interval_sec", 300))
            app_state["next_scan_timestamp"] = int((time.time() // interval + 1) * interval)


def background_scanner_worker() -> None:
    """Background daemon yang mengeksekusi pemindaian berkala tersinkronisasi kelipatan 5 menit."""
    interval = int(app_state["filters"].get("interval_sec", 300))
    print(f"🚀 [Web Scanner] Background worker aktif (Tersinkronisasi kelipatan {interval // 60} menit)")
    # Langsung jalankan pemindaian awal saat startup
    perform_scan()

    last_conf_check = 0
    while True:
        try:
            time.sleep(1)
            now_ts = int(time.time())

            # Cek berkala file filter (setiap 3 detik) untuk mendeteksi perubahan dari Telegram Bot
            if now_ts - last_conf_check >= 3:
                last_conf_check = now_ts
                latest_f = load_persistent_filters()
                disk_mode = str(latest_f.get("chain_mode", "")).upper().strip()
                current_mode = str(app_state["filters"].get("chain_mode", "")).upper().strip()
                if disk_mode and disk_mode != current_mode:
                    print(f"[{time.strftime('%H:%M:%S')}] 🔄 [Web Sync] Mendeteksi perubahan chain_mode dari Telegram/disk: {current_mode} ➔ {disk_mode}")
                    with state_lock:
                        app_state["filters"]["chain_mode"] = disk_mode
                    if not app_state.get("scanning", False):
                        threading.Thread(target=perform_scan, daemon=True).start()

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
    /* ===== DATA-PUBLIC OBSIDIAN PALETTE & DESIGN TOKENS ===== */
    :root {
      --bg: #1e1e1e;
      --card-bg: #252526;
      --card-inner: #1e1e1e;
      --card-border: #333333;
      --card-hover: #161c2a;
      --text-main: #e6e9f0;
      --text-sub: #8a93a8;
      --text-dim: #5d667a;
      --text-muted: #5d667a;

      --green: #2fd97b;
      --green-light: #2fd97b;
      --green-bg: rgba(47, 217, 123, 0.1);
      --green-glow: rgba(47, 217, 123, 0.2);

      --sol: #ffc24b;
      --sol-light: #ffc24b;
      --sol-bg: rgba(255, 194, 75, 0.1);
      --sol-glow: rgba(255, 194, 75, 0.2);

      --rh: #7ee0c9;
      --rh-light: #7ee0c9;
      --rh-bg: rgba(126, 224, 201, 0.1);
      --rh-glow: rgba(126, 224, 201, 0.2);

      --blue: #d8b4fe;
      --blue-bg: rgba(216, 180, 254, 0.1);
      --red: #ff6b86;
      --red-bg: rgba(255, 107, 134, 0.1);
      --yellow: #ffc24b;
      --yellow-bg: rgba(255, 194, 75, 0.1);
      --cyan: #7ee0c9;
      --cyan-light: #7ee0c9;
      --cyan-bg: rgba(126, 224, 201, 0.1);
      --cyan-glow: rgba(126, 224, 201, 0.2);

      --purple: #d8b4fe;
      --amber: #ffc24b;
      --teal: #7ee0c9;
      --rose: #ff6b86;

      --safe-top: env(safe-area-inset-top, 0px);
      --safe-bottom: env(safe-area-inset-bottom, 0px);
      --container-max: 1840px;
      --radius-card: 10px;
      --radius-pill: 999px;
      --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
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
      font-size: 13px;
      line-height: 1.45;
      padding-top: var(--safe-top);
      padding-bottom: calc(var(--safe-bottom) + 32px);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    /* ===== STICKY HEADER (SLEEK & COMPACT) ===== */
    .app-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(11, 14, 20, 0.95);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--card-border);
      padding: 8px 16px;
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
      gap: 7px;
    }

    .brand-title {
      font-weight: 800;
      font-size: 15px;
      letter-spacing: -0.3px;
      color: var(--text-main);
    }

    .brand-badge {
      font-size: 9.5px;
      font-weight: 700;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      background: var(--green-bg);
      color: var(--green);
      border: 1px solid rgba(47, 217, 123, 0.3);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      font-family: var(--font-mono);
    }

    .status-pulse {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      font-weight: 600;
      color: var(--green);
      background: var(--green-bg);
      padding: 2px 8px;
      border-radius: var(--radius-pill);
      border: 1px solid rgba(47, 217, 123, 0.25);
      font-family: var(--font-mono);
    }

    .pulse-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--green);
      animation: pulse 1.8s infinite;
      flex-shrink: 0;
    }

    .status-pulse.scanning {
      color: var(--yellow);
      background: var(--yellow-bg);
      border-color: rgba(255, 194, 75, 0.3);
    }
    .status-pulse.scanning .pulse-dot { background: var(--yellow); }

    @keyframes pulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.35; transform: scale(1.2); }
      100% { opacity: 1; transform: scale(1); }
    }

    .header-actions {
      display: flex;
      gap: 6px;
      align-items: center;
    }

    .btn-icon {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      border-radius: 7px;
      padding: 4px 9px;
      font-size: 11px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .btn-icon:hover { background: var(--card-hover); border-color: rgba(255,255,255,0.18); }
    .btn-icon:active { transform: scale(0.97); }

    .btn-scan {
      background: var(--green-bg);
      color: var(--green);
      border: 1px solid rgba(47, 217, 123, 0.35);
      font-family: var(--font-mono);
      font-weight: 700;
    }
    .btn-scan:hover { background: rgba(47, 217, 123, 0.2); border-color: var(--green); }

    /* ===== CHAIN SEGMENTED SWITCHER (SLEEK PILLS) ===== */
    .chain-segmented {
      display: flex;
      background: var(--bg);
      padding: 2px;
      border-radius: var(--radius-pill);
      border: 1px solid var(--card-border);
      gap: 2px;
    }

    .chain-tab {
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-sub);
      border-radius: var(--radius-pill);
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      user-select: none;
      white-space: nowrap;
      font-family: var(--font-mono);
    }
    .chain-tab:hover { color: var(--text-main); }

    .chain-count-badge {
      font-size: 9.5px;
      font-weight: 700;
      padding: 0 5px;
      border-radius: var(--radius-pill);
      background: rgba(255, 255, 255, 0.08);
      color: inherit;
      font-family: var(--font-mono);
      min-width: 16px;
      text-align: center;
      line-height: 1.3;
    }

    .chain-tab.active {
      background: var(--card-bg);
      color: var(--text-main);
      border: 1px solid rgba(255, 255, 255, 0.12);
    }
    .chain-tab.active[data-chain="sol"], .chain-tab.active[data-chain="SOL"] {
      color: var(--sol-light);
      border-color: rgba(255, 194, 75, 0.4);
      background: rgba(255, 194, 75, 0.1);
    }
    .chain-tab.active[data-chain="rh"],  .chain-tab.active[data-chain="RH"]  {
      color: var(--rh-light);
      border-color: rgba(126, 224, 201, 0.4);
      background: rgba(126, 224, 201, 0.1);
    }
    .chain-tab.active[data-chain="both"],.chain-tab.active[data-chain="BOTH"],.chain-tab.active[data-chain="all"] {
      color: var(--green);
      border-color: rgba(47, 217, 123, 0.4);
      background: rgba(47, 217, 123, 0.1);
    }

    /* ===== MAIN CONTAINER ===== */
    .container {
      padding: 12px 16px;
      max-width: var(--container-max);
      margin: 0 auto;
    }

    /* ===== INLINE STAT CHIPS ROW (PENGGANTI KOTAK BESAR) ===== */
    .stat-strip {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      margin-bottom: 12px;
    }

    .stat-chip {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 4px 9px;
      font-size: 12px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
      transition: border-color 0.15s;
    }
    .stat-chip:hover { border-color: rgba(255, 255, 255, 0.18); }

    .chip-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .dot-green  { background: var(--green); }
    .dot-purple { background: var(--purple); }
    .dot-amber  { background: var(--amber); }
    .dot-teal   { background: var(--teal); }
    .dot-dim    { background: var(--text-dim); }

    .stat-chip b {
      font-family: var(--font-mono);
      font-weight: 700;
      color: var(--text-main);
      font-size: 12.5px;
    }
    .chip-lbl {
      font-size: 11px;
      color: var(--text-sub);
      font-weight: 500;
    }
    .chip-val-teal {
      color: var(--teal) !important;
      font-weight: 700;
    }
    .chip-val-time {
      font-family: var(--font-mono);
      font-size: 11.5px;
      color: var(--amber);
      font-weight: 600;
    }
    .stat-chip-time {
      margin-left: auto;
    }

    /* Fallback kpi classes for backward compatibility */
    .kpi-grid { display: none; }
    .kpi-card { display: none; }

    /* ===== CATEGORY TABS & CONTROLS STRIP ===== */
    .controls-strip {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 12px;
    }

    .cat-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      width: 100%;
    }

    .cat-tab {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-pill);
      padding: 4px 10px;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-sub);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      white-space: nowrap;
      user-select: none;
      transition: all 0.15s ease;
      line-height: 1.2;
    }
    .cat-tab:hover {
      color: var(--text-main);
      border-color: rgba(255, 255, 255, 0.2);
    }

    .cat-tab .badge-count {
      font-size: 10px;
      padding: 0 5px;
      border-radius: var(--radius-pill);
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-sub);
      font-weight: 700;
      font-family: var(--font-mono);
      line-height: 1.3;
    }

    .cat-tab.active {
      background: rgba(47, 217, 123, 0.1);
      border-color: var(--green);
      color: var(--green);
    }
    .cat-tab.active .badge-count {
      background: rgba(47, 217, 123, 0.25);
      color: var(--green);
    }

    .cat-tab.active[data-cat="cto"] {
      background: rgba(216, 180, 254, 0.1);
      border-color: var(--purple);
      color: var(--purple);
    }
    .cat-tab.active[data-cat="cto"] .badge-count {
      background: rgba(216, 180, 254, 0.25);
      color: var(--purple);
    }

    .cat-tab.active[data-cat="momentum_5m"] {
      background: rgba(255, 194, 75, 0.1);
      border-color: var(--amber);
      color: var(--amber);
    }
    .cat-tab.active[data-cat="momentum_5m"] .badge-count {
      background: rgba(255, 194, 75, 0.25);
      color: var(--amber);
    }

    .cat-tab.active[data-cat="absorption"] {
      background: rgba(126, 224, 201, 0.1);
      border-color: var(--teal);
      color: var(--teal);
    }
    .cat-tab.active[data-cat="absorption"] .badge-count {
      background: rgba(126, 224, 201, 0.25);
      color: var(--teal);
    }

    .cat-tab.active[data-cat="break_ath"] {
      background: rgba(126, 224, 201, 0.1);
      border-color: var(--teal);
      color: var(--teal);
    }
    .cat-tab.active[data-cat="break_ath"] .badge-count {
      background: rgba(126, 224, 201, 0.25);
      color: var(--teal);
    }

    .cat-tab.active[data-cat="gaps"] {
      background: rgba(255, 107, 134, 0.1);
      border-color: var(--rose);
      color: var(--rose);
    }
    .cat-tab.active[data-cat="gaps"] .badge-count {
      background: rgba(255, 107, 134, 0.25);
      color: var(--rose);
    }

    .cat-tab.active[data-cat="history"] {
      background: rgba(255, 194, 75, 0.1);
      border-color: var(--amber);
      color: var(--amber);
    }
    .cat-tab.active[data-cat="history"] .badge-count {
      background: rgba(255, 194, 75, 0.25);
      color: var(--amber);
    }

    .cat-tab.active[data-cat="akashi"] {
      background: rgba(239, 68, 68, 0.15);
      border-color: #ef4444;
      color: #ef4444;
    }
    .cat-tab.active[data-cat="akashi"] .badge-count {
      background: rgba(239, 68, 68, 0.3);
      color: #ef4444;
    }

    /* Sub-Controls Bar (Tier 2) */
    .sub-controls-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-top: 8px;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      width: 100%;
    }

    .sub-controls-left {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 11.5px;
    }
    .sub-ctrl-label {
      color: var(--text-dim);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .sub-ctrl-name {
      color: var(--text-main);
      font-weight: 700;
      font-size: 12px;
    }
    .sub-ctrl-pill {
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 1px 7px;
      border-radius: var(--radius-pill);
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-sub);
    }

    /* Controls Right: Search + View Switcher */
    .sub-controls-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .controls-right {
      display: flex;
      align-items: center;
      gap: 6px;
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
      left: 10px;
      font-size: 11px;
      color: var(--text-dim);
      pointer-events: none;
    }
    .search-input {
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 5px 28px 5px 28px;
      font-size: 12px;
      color: var(--text-main);
      outline: none;
      transition: border-color 0.15s;
    }
    .search-input:focus {
      border-color: var(--purple);
    }
    .search-clear {
      position: absolute;
      right: 8px;
      background: transparent;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 12px;
      padding: 2px 4px;
      display: none;
    }
    .search-clear.visible { display: block; }

    /* View Switcher: Cards vs Table */
    .view-toggle {
      display: inline-flex;
      background: var(--bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 2px;
      gap: 2px;
      flex-shrink: 0;
    }
    .btn-view {
      background: transparent;
      border: none;
      color: var(--text-dim);
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-family: var(--font-mono);
      transition: all 0.15s;
      user-select: none;
    }
    .btn-view:hover { color: var(--text-main); }
    .btn-view.active {
      background: var(--card-bg);
      color: var(--text-main);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }

    /* ===== TOKEN AVATAR ===== */
    .tok-avatar-wrap {
      width: 26px;
      height: 26px;
      border-radius: 6px;
      overflow: hidden;
      flex-shrink: 0;
      background: #0e1118;
      border: 1px solid rgba(255, 255, 255, 0.08);
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
      font-weight: 700;
      font-size: 11px;
      color: rgba(255, 255, 255, 0.95);
    }

    /* ===== RANK BADGES (MINIMALIST) ===== */
    .rank-badge {
      font-size: 11px;
      font-weight: 700;
      font-family: var(--font-mono);
      color: var(--text-sub);
      display: inline-flex;
      align-items: center;
      line-height: 1;
    }
    .rank-gold   { color: var(--amber); font-weight: 800; }
    .rank-silver { color: #cbd5e1; font-weight: 800; }
    .rank-bronze { color: #fb923c; font-weight: 800; }
    .rank-dim    { color: var(--text-dim); }

    /* ===== MICRO METADATA ===== */
    .meta-age {
      font-size: 10px;
      font-weight: 600;
      font-family: var(--font-mono);
      color: var(--text-dim);
    }
    .meta-age.fresh { color: var(--green); }
    .venue-pill {
      font-size: 9.5px;
      font-weight: 600;
      padding: 0 4px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.05);
      color: var(--text-sub);
      border: 1px solid rgba(255, 255, 255, 0.07);
      font-family: var(--font-mono);
    }

    .btn-copy-inline {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      border-radius: 5px;
      padding: 2px 6px;
      font-size: 10.5px;
      font-family: var(--font-mono);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      transition: all 0.15s;
    }
    .btn-copy-inline:hover {
      color: var(--text-main);
      border-color: rgba(255, 255, 255, 0.2);
    }
    .btn-copy-inline.copied {
      background: var(--green-bg);
      color: var(--green);
      border-color: rgba(47, 217, 123, 0.4);
    }

    /* ===== SAFETY AUDIT BADGE (CAPSULE PILL) ===== */
    .safety-block {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .safety-pill {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      font-weight: 700;
      font-size: 10.5px;
      line-height: 1.2;
      font-family: var(--font-mono);
    }
    .safety-A { background: var(--green-bg); color: var(--green); border: 1px solid rgba(47, 217, 123, 0.35); }
    .safety-B { background: var(--teal-bg);  color: var(--teal);  border: 1px solid rgba(126, 224, 201, 0.35); }
    .safety-C { background: var(--amber-bg); color: var(--amber); border: 1px solid rgba(255, 194, 75, 0.35); }
    .safety-D { background: var(--red-bg);   color: var(--red);   border: 1px solid rgba(255, 107, 134, 0.35); }

    /* ===== DATA-PUBLIC FEED TABLE ===== */
    .table-container {
      width: 100%;
      overflow-x: auto;
      background: var(--bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      -webkit-overflow-scrolling: touch;
    }
    .arc-table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 13px;
      line-height: 1.45;
      min-width: 960px;
    }
    .arc-thead th {
      background: var(--card-bg);
      color: var(--text-sub);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 7px 10px;
      border-bottom: 1px solid var(--card-border);
      white-space: nowrap;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .arc-tr {
      border-bottom: 1px solid var(--card-border);
      transition: background 0.12s ease;
    }
    .arc-tr:hover {
      background: rgba(255, 255, 255, 0.02);
    }
    .arc-tr.rank-1 { background: rgba(255, 194, 75, 0.02); }
    .arc-tr.rank-2 { background: rgba(226, 232, 240, 0.01); }
    .arc-tr.rank-3 { background: rgba(249, 115, 22, 0.01); }

    .arc-td {
      padding: 6px 10px;
      vertical-align: middle;
      font-size: 13px;
    }
    .arc-td.mono {
      font-family: var(--font-mono);
      font-size: 12.5px;
    }

    /* Action button in Table */
    .btn-chart {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      text-decoration: none;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
      box-shadow: none;
    }
    .btn-chart:hover {
      border-color: var(--purple);
      color: var(--purple);
      box-shadow: none;
      transform: none;
    }
    .btn-chart:active { transform: scale(0.97); }

    .btn-chart-secondary {
      background: transparent;
      color: var(--text-sub);
      border: 1px solid var(--card-border);
    }
    .btn-chart-secondary:hover { color: var(--text-main); border-color: rgba(255, 255, 255, 0.2); }

    /* Chain pill */
    .chain-pill {
      font-size: 10px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: var(--radius-pill);
      display: inline-flex;
      align-items: center;
      gap: 2px;
      flex-shrink: 0;
      font-family: var(--font-mono);
      line-height: 1.2;
    }
    .chain-pill.sol { background: var(--sol-bg); color: var(--sol-light); border: 1px solid rgba(255, 194, 75, 0.3); }
    .chain-pill.rh  { background: var(--rh-bg);  color: var(--rh-light);  border: 1px solid rgba(126, 224, 201, 0.3); }

    /* State pill */
    .state-pill {
      font-size: 11px;
      font-weight: 700;
      padding: 1px 7px;
      border-radius: var(--radius-pill);
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
      line-height: 1.3;
      border: 1px solid var(--card-border);
      background: var(--card-bg);
      color: var(--text-sub);
    }
    .state-chop       { background: var(--green-bg); color: var(--green); border-color: rgba(47, 217, 123, 0.3); }
    .state-cto        { background: var(--purple-bg); color: var(--purple); border-color: rgba(216, 180, 254, 0.3); }
    .state-momentum   { background: var(--amber-bg); color: var(--amber); border-color: rgba(255, 194, 75, 0.3); }
    .state-ath        { background: var(--teal-bg); color: var(--teal); border-color: rgba(126, 224, 201, 0.3); }
    .state-absorption { background: var(--teal-bg); color: var(--teal); border-color: rgba(126, 224, 201, 0.3); }
    .state-distrib    { background: var(--red-bg); color: var(--red); border-color: rgba(255, 107, 134, 0.3); }
    .state-reaccum    { background: var(--amber-bg); color: var(--amber); border-color: rgba(255, 194, 75, 0.3); }
    .state-neutral    { background: var(--card-bg); color: var(--text-sub); border-color: var(--card-border); }

    /* ER Badge */
    .er-badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      font-weight: 700;
      font-size: 10.5px;
      font-family: var(--font-mono);
      line-height: 1.2;
    }
    .er-prime { background: var(--green-bg); color: var(--green); border: 1px solid rgba(47, 217, 123, 0.3); }
    .er-good  { background: var(--teal-bg); color: var(--teal); border: 1px solid rgba(126, 224, 201, 0.3); }
    .er-mid   { background: var(--amber-bg); color: var(--amber); border: 1px solid rgba(255, 194, 75, 0.3); }
    .er-high  { background: var(--red-bg); color: var(--red); border: 1px solid rgba(255, 107, 134, 0.3); }

    /* Volatility text */
    .vol-pos { color: var(--green); font-weight: 700; }
    .vol-neg { color: var(--red); font-weight: 700; }

    /* Narrative Badges & Social Link */
    .narr-pill {
      display: inline-flex;
      align-items: center;
      font-size: 9.5px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: var(--radius-pill);
      white-space: nowrap;
      line-height: 1.2;
    }
    .narr-cto   { background: var(--purple-bg); color: var(--purple); border: 1px solid rgba(216, 180, 254, 0.3); }
    .narr-ai    { background: var(--teal-bg);   color: var(--teal);   border: 1px solid rgba(126, 224, 201, 0.3); }
    .narr-smart { background: var(--amber-bg);  color: var(--amber);  border: 1px solid rgba(255, 194, 75, 0.3); }
    .narr-blue  { background: rgba(59, 130, 246, 0.1); color: #93c5fd; border: 1px solid rgba(59, 130, 246, 0.3); }

    .social-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--text-dim);
      text-decoration: none;
      font-size: 11px;
      margin-left: 2px;
      transition: color 0.15s;
    }
    .social-link:hover { color: var(--purple); }

    /* Signal History Time Pills & Compact Badge */
    .time-cell-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
      max-width: 440px;
    }
    .time-pill {
      font-size: 11px;
      font-family: var(--font-mono);
      font-weight: 600;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      background: var(--amber-bg);
      color: var(--amber);
      border: 1px solid rgba(255, 194, 75, 0.25);
      white-space: nowrap;
      line-height: 1.2;
    }
    .time-pill.recent {
      background: rgba(255, 194, 75, 0.2);
      color: #fff;
      border-color: var(--amber);
      font-weight: 700;
    }
    .time-pill-more {
      font-size: 10px;
      font-family: var(--font-mono);
      font-weight: 600;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      background: var(--card-bg);
      color: var(--text-sub);
      border: 1px solid var(--card-border);
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.15s;
    }
    .time-pill-more:hover { color: var(--text-main); border-color: rgba(255, 255, 255, 0.2); }

    .badge-hist-compact {
      display: inline-flex;
      align-items: center;
      background: var(--amber-bg);
      color: var(--amber);
      border: 1px solid rgba(255, 194, 75, 0.25);
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      font-size: 10.5px;
      font-weight: 600;
      margin-left: 5px;
      white-space: nowrap;
      font-family: var(--font-mono);
    }

    /* ===== COMPACT TOKEN CARD (FOR CARDS VIEW MODE) ===== */
    .card-list {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .token-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 12px;
      position: relative;
      transition: border-color 0.15s ease;
      overflow: hidden;
    }
    .token-card:hover { border-color: rgba(255, 255, 255, 0.15); }
    .token-card.sol-card { border-left: 3px solid var(--amber); }
    .token-card.rh-card  { border-left: 3px solid var(--teal); }
    .token-card.ath-card { border-left: 3px solid var(--teal); }
    .token-card.momentum-card { border-left: 3px solid var(--amber); }
    .token-card.cto-card { border-left: 3px solid var(--purple); }

    .card-row-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 8px;
      gap: 8px;
    }
    .token-info-left {
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }
    .token-name-block { min-width: 0; }
    .symbol-row {
      display: flex;
      align-items: center;
      gap: 5px;
      flex-wrap: wrap;
    }
    .token-symbol {
      font-size: 14px;
      font-weight: 800;
      color: var(--text-main);
      line-height: 1.2;
    }
    .token-sub-row {
      display: flex;
      align-items: center;
      gap: 5px;
      margin-top: 2px;
      font-size: 11px;
      color: var(--text-sub);
      flex-wrap: wrap;
    }
    .token-price {
      font-family: var(--font-mono);
      font-weight: 600;
      color: var(--text-sub);
    }
    .token-name {
      max-width: 140px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--text-dim);
    }

    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: var(--bg);
      border-radius: 6px;
      padding: 6px 4px;
      margin-bottom: 7px;
      border: 1px solid var(--card-border);
      gap: 2px;
    }
    .metric-cell { text-align: center; }
    .m-label {
      font-size: 9px;
      font-weight: 600;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 2px;
    }
    .m-val {
      font-size: 11.5px;
      font-weight: 700;
      color: var(--text-main);
      font-family: var(--font-mono);
    }

    .card-row-bottom {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      border-top: 1px solid var(--card-border);
      padding-top: 8px;
    }
    .card-actions {
      display: flex;
      align-items: center;
      gap: 5px;
      flex-shrink: 0;
    }

    /* Gap reasons pills */
    .gap-reasons-box {
      margin-top: 6px;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding-top: 6px;
      border-top: 1px dashed var(--card-border);
    }
    .gap-pill {
      font-size: 10px;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: var(--radius-pill);
      background: var(--red-bg);
      color: var(--red);
      border: 1px solid rgba(255, 107, 134, 0.25);
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-family: var(--font-mono);
    }

    /* ===== EMPTY STATE ===== */
    .empty-box {
      background: var(--card-bg);
      border: 1px dashed var(--card-border);
      border-radius: 10px;
      padding: 36px 16px;
      text-align: center;
      color: var(--text-sub);
      grid-column: 1 / -1;
    }
    .empty-icon  { font-size: 32px; margin-bottom: 10px; }
    .empty-title { font-size: 15px; font-weight: 700; color: var(--text-main); margin-bottom: 4px; }
    .empty-desc  { font-size: 12.5px; line-height: 1.5; max-width: 440px; margin: 0 auto; color: var(--text-sub); }

    /* ===== FOOTER INFO ===== */
    .footer-info {
      margin-top: 16px;
      text-align: center;
      font-size: 11px;
      color: var(--text-dim);
      font-family: var(--font-mono);
    }

    /* ===== MODAL SETTINGS (OBSIDIAN THEMED) ===== */
    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      z-index: 200;
      align-items: flex-end;
      justify-content: center;
    }
    .modal-overlay.show { display: flex; }

    .modal-sheet {
      background: var(--card-bg);
      border-top-left-radius: 16px;
      border-top-right-radius: 16px;
      border: 1px solid var(--card-border);
      border-bottom: none;
      width: 100%;
      max-width: 540px;
      max-height: 88vh;
      overflow-y: auto;
      padding: 18px 16px calc(var(--safe-bottom) + 16px);
      animation: slideUp 0.2s ease-out;
    }

    @keyframes slideUp {
      from { transform: translateY(100%); }
      to   { transform: translateY(0); }
    }
    @keyframes fadeScaleIn {
      from { opacity: 0; transform: scale(0.97); }
      to   { opacity: 1; transform: scale(1); }
    }

    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--card-border);
    }
    .modal-title {
      font-size: 15px;
      font-weight: 700;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .modal-close {
      background: var(--bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      width: 26px;
      height: 26px;
      border-radius: 50%;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    }
    .modal-close:hover { color: var(--text-main); border-color: rgba(255, 255, 255, 0.2); }

    .preset-strip {
      display: flex;
      gap: 5px;
      margin-bottom: 12px;
    }
    .btn-preset {
      flex: 1;
      background: var(--bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      padding: 5px 4px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      text-align: center;
    }
    .btn-preset:hover { color: var(--text-main); border-color: rgba(255, 255, 255, 0.2); }

    .form-section-title {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-sub);
      margin: 14px 0 8px;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--card-border);
    }
    .form-section-title:first-of-type { margin-top: 0; }

    .form-grid-2 {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .form-group { margin-bottom: 3px; }
    .form-label {
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-sub);
      margin-bottom: 3px;
    }
    .form-input {
      width: 100%;
      background: var(--bg);
      border: 1px solid var(--card-border);
      border-radius: 7px;
      padding: 7px 9px;
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-main);
      outline: none;
      font-family: var(--font-mono);
      transition: border-color 0.15s;
    }
    .form-input:focus { border-color: var(--purple); }

    .modal-actions { display: flex; gap: 8px; margin-top: 16px; }
    .btn-submit {
      flex: 1;
      background: var(--purple);
      color: #0b0e14;
      border: none;
      padding: 10px 0;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: opacity 0.15s;
    }
    .btn-submit:hover { opacity: 0.9; }

    .btn-reset {
      background: var(--bg);
      color: var(--text-sub);
      border: 1px solid var(--card-border);
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-reset:hover { color: var(--text-main); border-color: rgba(255, 255, 255, 0.2); }

    /* ===== TOAST NOTIFICATION ===== */
    .toast {
      position: fixed;
      top: 16px;
      left: 50%;
      transform: translateX(-50%) translateY(-20px);
      background: var(--card-bg);
      border: 1px solid var(--purple);
      color: var(--text-main);
      padding: 6px 16px;
      border-radius: var(--radius-pill);
      font-size: 12px;
      font-weight: 600;
      z-index: 300;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
      opacity: 0;
      pointer-events: none;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-family: var(--font-mono);
    }
    .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }

    /* ===== RESPONSIVE MEDIA QUERIES ===== */

    /* Mobile Phone (HP screens) */
    @media (max-width: 640px) {
      .container { padding: 8px 10px; }
      .brand-title { font-size: 14px; }
      .stat-chip { padding: 3px 7px; font-size: 11px; }
      .stat-chip b { font-size: 11.5px; }
      .chip-lbl { font-size: 10px; }
      .cat-tab { padding: 3px 8px; font-size: 10.5px; }
      .search-input { font-size: 11.5px; padding: 4px 24px 4px 24px; }
      .arc-table { font-size: 12px; }
      .arc-thead th { padding: 6px 8px; font-size: 10px; }
      .arc-td { padding: 5px 8px; font-size: 12px; }
    }

    /* Tablet & Desktop */
    @media (min-width: 768px) {
      .container { padding: 14px 20px; }
      .brand-title { font-size: 16px; }

      .header-inner {
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
      }
      .chain-segmented {
        max-width: 300px;
        flex: 1;
        margin: 0 14px;
      }

      .controls-strip {
        flex-direction: column;
        align-items: stretch;
      }
      .cat-tabs { width: 100%; margin-bottom: 0; }
      .search-wrap { width: 280px; }

      .card-list {
        grid-template-columns: repeat(2, 1fr);
        gap: 10px;
      }

      .modal-overlay { align-items: center; }
      .modal-sheet {
        border-radius: 12px;
        border: 1px solid var(--card-border);
        width: 500px;
        max-width: 90vw;
        max-height: 82vh;
        animation: fadeScaleIn 0.18s ease-out;
      }
      .form-grid-2 { grid-template-columns: repeat(2, 1fr); }
    }

    /* Wide Desktop (Laptop 1920x1200) */
    @media (min-width: 1100px) {
      .card-list {
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
      }
      .search-wrap { width: 340px; }
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

      <!-- Segmented Chain Switcher (Tersinkronisasi 2-Arah dengan Telegram) -->
      <div class="chain-segmented" id="chainSegmented" title="Mode Rantai (Pusat Sinkronisasi Telegram)">
        <div class="chain-tab" id="tabChainRh" data-chain="RH" onclick="switchBackendChain('RH')">
          <span>RH</span><span class="chain-count-badge" id="cntChainRh">0</span>
        </div>
        <div class="chain-tab" id="tabChainSol" data-chain="SOL" onclick="switchBackendChain('SOL')">
          <span>SOL</span><span class="chain-count-badge" id="cntChainSol">0</span>
        </div>
        <div class="chain-tab active" id="tabChainBoth" data-chain="BOTH" onclick="switchBackendChain('BOTH')">
          <span>DUAL</span><span class="chain-count-badge" id="cntChainAll">0</span>
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

    <!-- Controls Strip: Two-Tier Layout (Category Tabs + Sub-Controls) -->
    <div class="controls-strip">
      <!-- Tier 1: Category Tabs (Full-Width Wrap, No Clipping) -->
      <div class="cat-tabs" id="catTabsBar">
        <div class="cat-tab active" data-cat="all" onclick="setCategoryTab('all')" title="Hotkey: A">
          <span>ALL SIGNALS</span>
          <span id="badgeAll" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="akashi" onclick="setCategoryTab('akashi')" title="Hotkey: K">
          <span>🔴 AKASHI</span>
          <span id="badgeAkashi" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="siap" onclick="setCategoryTab('siap')" title="Hotkey: 1">
          <span>🟢 SIAP LP</span>
          <span id="badgeSiap" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="smart" onclick="setCategoryTab('smart')" title="Hotkey: M">
          <span>🧠 SMART</span>
          <span id="badgeSmart" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="cto" onclick="setCategoryTab('cto')" title="Hotkey: 2">
          <span>👑 CTO</span>
          <span id="badgeCto" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="slow_cook" onclick="setCategoryTab('slow_cook')" title="Hotkey: O">
          <span>🍲 SLOW COOK</span>
          <span id="badgeSlow" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="dip" onclick="setCategoryTab('dip')" title="Hotkey: D">
          <span>📉 30% DIP</span>
          <span id="badgeDip" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="flip" onclick="setCategoryTab('flip')" title="Hotkey: F">
          <span>📉 FLIP</span>
          <span id="badgeFlip" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="momentum_5m" onclick="setCategoryTab('momentum_5m')" title="Hotkey: 3">
          <span>⚡ 5M</span>
          <span id="badgeM5" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="absorption" onclick="setCategoryTab('absorption')" title="Hotkey: 4">
          <span>📡 ABSORB</span>
          <span id="badgeAbsorb" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="break_ath" onclick="setCategoryTab('break_ath')" title="Hotkey: 5">
          <span>🚀 ATH</span>
          <span id="badgeBath" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="gaps" onclick="setCategoryTab('gaps')" title="Hotkey: 6">
          <span>⚖️ GAPS</span>
          <span id="badgeGaps" class="badge-count">0</span>
        </div>
        <div class="cat-tab" data-cat="history" onclick="setCategoryTab('history')" title="Hotkey: 7">
          <span>📜 HISTORY</span>
          <span id="badgeHistory" class="badge-count">0</span>
        </div>
      </div>

      <!-- Tier 2: Sub-Controls Bar (Status Kategori Kiri + Quick Search & Mode Switcher Kanan) -->
      <div class="sub-controls-bar">
        <div class="sub-controls-left">
          <span class="sub-ctrl-label">Active:</span>
          <span id="activeCatLabel" class="sub-ctrl-name">ALL SIGNALS</span>
          <span id="activeCatPill" class="sub-ctrl-pill">0 pool</span>
        </div>

        <div class="sub-controls-right">
          <div class="search-wrap">
            <span class="search-icon">🔍</span>
            <input type="text" id="tokenSearch" class="search-input" placeholder="Cari simbol atau CA... ( / )" oninput="onSearchInput()">
            <button id="searchClear" class="search-clear" onclick="clearSearch()">✕</button>
          </div>

          <div class="view-toggle" title="Ubah Tampilan Daftar (Hotkey: V)">
            <button id="btnViewCards" class="btn-view" onclick="setViewMode('cards')">
              <span>⊞</span> Cards
            </button>
            <button id="btnViewTable" class="btn-view active" onclick="setViewMode('table')">
              <span>☰</span> Table
            </button>
          </div>
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
        <button class="btn-preset" onclick="applyPreset('konservatif')">🛡️ Konservatif (V/L 0.8x)</button>
        <button class="btn-preset active" onclick="applyPreset('standar')">⚖️ Standar (V/L 0.5x)</button>
        <button class="btn-preset" onclick="applyPreset('agresif')">🚀 Agresif (V/L 0.3x)</button>
      </div>

      <div class="form-section-title">📊 Kriteria Chop Sideways LP (Anti-Drill Down)</div>
      <input type="hidden" id="f_min_fee" value="0.5">
      <div class="form-grid-2">

        <div class="form-group">
          <div class="form-label">
            <span>Min Market Cap ($)</span>
            <span>Hard filter min $1M</span>
          </div>
          <input type="number" step="100000" id="f_min_mcap" class="form-input" value="1000000">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min Usia Token (Jam)</span>
            <span style="color:var(--sol-light)">Hard filter anti-sniper</span>
          </div>
          <input type="number" step="1" id="f_min_age_hours" class="form-input" value="12">
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
            <span>Perputaran fee (MC $1M+)</span>
          </div>
          <input type="number" step="0.1" id="f_min_vl" class="form-input" value="0.5">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Min Buy Ratio (%)</span>
            <span style="color:var(--green-light)">Anti-Panic Sell (min 46%)</span>
          </div>
          <input type="number" step="1" id="f_min_buy_ratio" class="form-input" value="46.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Max ATH Drawdown (%)</span>
            <span style="color:#fde047">Anti-Zombie (Drop ≤ 85%)</span>
          </div>
          <input type="number" step="1" id="f_max_ath_drawdown" class="form-input" value="-85.0">
        </div>

        <div class="form-group">
          <div class="form-label">
            <span>Max Drop 1 Jam (%)</span>
            <span style="color:var(--sol-light)">Downside Guard (Drop ≤ 8%)</span>
          </div>
          <input type="number" step="1" id="f_max_drop_1h" class="form-input" value="-8.0">
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
            <span>Simetris pump/dump (max 20%)</span>
          </div>
          <input type="number" step="1" id="f_max_1h" class="form-input" value="20.0">
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
        <div class="form-group" style="grid-column: 1 / -1;">
          <div class="form-label">
            <span>Mode Rantai (Chain Mode)</span>
            <span style="color:var(--cyan)">Tersinkronisasi Telegram</span>
          </div>
          <select id="f_chain_mode" class="form-input" style="background:#0b1120;color:#fff;border:1px solid var(--card-border);height:42px;border-radius:8px;padding:0 10px;">
            <option value="RH">🔹 Robinhood Only (RH)</option>
            <option value="SOL">🔸 Solana Only (SOL)</option>
            <option value="BOTH">🔸🔹 Dual / Both (SOL + RH)</option>
          </select>
        </div>

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
    let currentChainMode = 'BOTH';
    let activeChain = 'both';
    let activeCategory = 'siap';
    let searchQuery = '';
    let countdownInterval = null;
    let viewMode = localStorage.getItem("lp_view_mode") || (window.innerWidth >= 1024 ? "table" : "cards");
    let lastRenderStateKey = "";

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
      lastRenderStateKey = "";
      renderCards();
    }

    /* ---- Helpers ---- */
    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function renderTimePills(timestamps, summaryTimes, maxPills = 3) {
      let times = Array.isArray(timestamps) && timestamps.length ? [...timestamps] : [];
      if (!times.length && summaryTimes) {
        times = summaryTimes.replace(/\s*WIB/g, "").split(",").map(s => s.trim()).filter(Boolean);
      }
      if (!times.length) return `<span class="time-pill">—</span>`;

      if (times.length <= maxPills + 1) {
        return `<div class="time-cell-wrap">` +
          times.map(t => `<span class="time-pill recent">🕒 ${escapeHtml(t)}</span>`).join("") +
          `</div>`;
      }

      const recent = times.slice(-maxPills);
      const earlierCount = times.length - maxPills;
      const allText = times.join(", ") + " WIB";

      return `
        <div class="time-cell-wrap">
          ${recent.map(t => `<span class="time-pill recent">🕒 ${escapeHtml(t)}</span>`).join("")}
          <span class="time-pill-more" title="Seluruh riwayat: ${escapeHtml(allText)}" onclick="toggleTimePills(this, '${escapeHtml(allText)}')">
            +${earlierCount} jam lainnya
          </span>
        </div>`;
    }

    window.toggleTimePills = function(el, allText) {
      const parent = el.parentElement;
      if (!parent) return;
      const times = allText.replace(/\s*WIB/g, "").split(",").map(s => s.trim()).filter(Boolean);
      parent.innerHTML = times.map(t => `<span class="time-pill recent" style="margin:1px">🕒 ${escapeHtml(t)}</span>`).join("") +
        `<span class="time-pill-more" onclick="renderCards()" style="background:rgba(244,63,94,0.18);color:#fb7185">Tutup ✕</span>`;
    };
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
      lastRenderStateKey = "";
      renderCards();
    }

    function clearSearch() {
      const inp = document.getElementById("tokenSearch");
      inp.value = "";
      onSearchInput();
      inp.focus();
    }

    /* ---- Chain & Category Filter Handlers ---- */
    function updateChainModeUI(mode) {
      currentChainMode = (mode || "BOTH").toUpperCase();
      activeChain = currentChainMode.toLowerCase();
      lastRenderStateKey = "";
      const tabRh   = document.getElementById("tabChainRh");
      const tabSol  = document.getElementById("tabChainSol");
      const tabBoth = document.getElementById("tabChainBoth");
      if (tabRh)   tabRh.classList.toggle("active", currentChainMode === "RH");
      if (tabSol)  tabSol.classList.toggle("active", currentChainMode === "SOL");
      if (tabBoth) tabBoth.classList.toggle("active", currentChainMode === "BOTH");

      const sel = document.getElementById("f_chain_mode");
      if (sel) sel.value = currentChainMode;
    }

    async function switchBackendChain(mode) {
      mode = (mode || "BOTH").toUpperCase();
      updateChainModeUI(mode);
      const label = mode === "RH" ? "🔹 Robinhood Only" : (mode === "SOL" ? "🔸 Solana Only" : "🔸🔹 Dual (SOL + RH)");
      showToast(`Mengganti rantai ke: ${label}...`, "🔄");

      try {
        const res = await fetch("/api/chain", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chain_mode: mode }),
        });
        const data = await res.json();
        if (data.ok) {
          showToast(`Mode aktif: ${label}`, "✅");
          fetchState();
        } else {
          showToast(`Gagal: ${data.error || "Gagal mengubah chain"}`, "❌");
        }
      } catch (err) {
        console.error("Gagal ganti mode:", err);
        showToast("Gagal menghubungi server", "❌");
      }
    }

    // Alias untuk kompatibilitas fungsi lama
    function setChainFilter(chain) {
      const modeMap = { all: "BOTH", both: "BOTH", sol: "SOL", rh: "RH" };
      switchBackendChain(modeMap[String(chain).toLowerCase()] || "BOTH");
    }

    function setCategoryTab(cat) {
      activeCategory = cat;
      document.querySelectorAll(".cat-tab").forEach(tab =>
        tab.classList.toggle("active", tab.getAttribute("data-cat") === cat)
      );
      const catLabels = {
        all: "ALL SIGNALS", akashi: "🔴 AKASHI ZONE", siap: "🟢 SIAP LP",
        smart: "🧠 SMART LP", cto: "👑 CTO", slow_cook: "🍲 SLOW COOK",
        dip: "📉 30% DIP", flip: "📉 FLIP LP", momentum_5m: "⚡ 5M",
        absorption: "📡 ABSORB", break_ath: "🚀 ATH", gaps: "⚖️ GAPS",
        history: "📜 HISTORY"
      };
      const labelEl = document.getElementById("activeCatLabel");
      if (labelEl) labelEl.innerText = catLabels[cat] || cat.toUpperCase();
      lastRenderStateKey = "";
      renderCards();
    }

    /* ---- Modal Settings ---- */
    function openModal() {
      if (globalState && globalState.filters) {
        const f = globalState.filters;
        if (f.chain_mode         !== undefined) document.getElementById("f_chain_mode").value = String(f.chain_mode).toUpperCase();
        if (f.min_fee_siap_lp     !== undefined) document.getElementById("f_min_fee").value  = f.min_fee_siap_lp;
        if (f.min_mcap           !== undefined) document.getElementById("f_min_mcap").value = f.min_mcap;
        if (f.min_age_hours      !== undefined) document.getElementById("f_min_age_hours").value = f.min_age_hours;
        if (f.min_liq            !== undefined) document.getElementById("f_min_liq").value  = f.min_liq;
        if (f.min_vl             !== undefined) document.getElementById("f_min_vl").value   = f.min_vl;
        if (f.min_buy_ratio      !== undefined) document.getElementById("f_min_buy_ratio").value = f.min_buy_ratio;
        if (f.max_ath_drawdown   !== undefined) document.getElementById("f_max_ath_drawdown").value = f.max_ath_drawdown;
        if (f.max_drop_1h        !== undefined) document.getElementById("f_max_drop_1h").value = f.max_drop_1h;
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
        document.getElementById("f_min_fee").value  = 1.0;
        document.getElementById("f_min_mcap").value = 1000000;
        document.getElementById("f_min_age_hours").value = 48;
        document.getElementById("f_min_liq").value  = 30000;
        document.getElementById("f_min_vl").value   = 0.8;
        document.getElementById("f_min_buy_ratio").value = 50.0;
        document.getElementById("f_max_ath_drawdown").value = -75.0;
        document.getElementById("f_max_drop_1h").value = -6.0;
        document.getElementById("f_max_5m").value   = 10.0;
        document.getElementById("f_max_1h").value   = 15.0;
        document.getElementById("f_max_er").value   = 15.0;
        document.getElementById("f_m5_vol").value   = 150000;
        document.getElementById("f_m5_liq").value   = 20000;
        showToast("Preset Konservatif dipilih", "🛡️");
      } else if (p === 'standar') {
        resetDefaultFilters();
        showToast("Preset Standar dipilih", "⚖️");
      } else if (p === 'agresif') {
        document.getElementById("f_min_fee").value  = 0.3;
        document.getElementById("f_min_mcap").value = 1000000;
        document.getElementById("f_min_age_hours").value = 12;
        document.getElementById("f_min_liq").value  = 15000;
        document.getElementById("f_min_vl").value   = 0.3;
        document.getElementById("f_min_buy_ratio").value = 42.0;
        document.getElementById("f_max_ath_drawdown").value = -90.0;
        document.getElementById("f_max_drop_1h").value = -12.0;
        document.getElementById("f_max_5m").value   = 20.0;
        document.getElementById("f_max_1h").value   = 25.0;
        document.getElementById("f_max_er").value   = 25.0;
        document.getElementById("f_m5_vol").value   = 80000;
        document.getElementById("f_m5_liq").value   = 10000;
        showToast("Preset Agresif dipilih", "🚀");
      }
    }

    function resetDefaultFilters() {
      document.getElementById("f_chain_mode").value = "BOTH";
      document.getElementById("f_min_fee").value  = 0.5;
      document.getElementById("f_min_mcap").value = 1000000;
      document.getElementById("f_min_age_hours").value = 24;
      document.getElementById("f_min_liq").value  = 20000;
      document.getElementById("f_min_vl").value   = 0.5;
      document.getElementById("f_min_buy_ratio").value = 46.0;
      document.getElementById("f_max_ath_drawdown").value = -85.0;
      document.getElementById("f_max_drop_1h").value = -8.0;
      document.getElementById("f_max_5m").value   = 15.0;
      document.getElementById("f_max_1h").value   = 20.0;
      document.getElementById("f_max_er").value   = 20.0;
      document.getElementById("f_m5_vol").value   = 100000;
      document.getElementById("f_m5_liq").value   = 10000;
      document.getElementById("f_position").value = 100.0;
      document.getElementById("f_interval").value = 300;
    }

    async function saveFilters() {
      const targetChain = (document.getElementById("f_chain_mode").value || "BOTH").toUpperCase();
      const payload = {
        chain_mode:          targetChain,
        min_fee_siap_lp:     parseFloat(document.getElementById("f_min_fee").value)  || 0.5,
        min_mcap:            parseFloat(document.getElementById("f_min_mcap").value) || 1000000,
        min_age_hours:       parseFloat(document.getElementById("f_min_age_hours").value) || 12,
        min_liq:             parseFloat(document.getElementById("f_min_liq").value)  || 20000,
        min_vl:              parseFloat(document.getElementById("f_min_vl").value)   || 0.5,
        min_buy_ratio:       parseFloat(document.getElementById("f_min_buy_ratio").value) || 46.0,
        max_ath_drawdown:    parseFloat(document.getElementById("f_max_ath_drawdown").value) || -85.0,
        max_drop_1h:         parseFloat(document.getElementById("f_max_drop_1h").value) || -8.0,
        max_drop_5m:         -4.0,
        max_5m:              parseFloat(document.getElementById("f_max_5m").value)   || 15.0,
        max_1h:              parseFloat(document.getElementById("f_max_1h").value)   || 20.0,
        max_er:              parseFloat(document.getElementById("f_max_er").value)   || 20.0,
        momentum_5m_min_vol: parseFloat(document.getElementById("f_m5_vol").value)  || 100000.0,
        momentum_5m_min_liq: parseFloat(document.getElementById("f_m5_liq").value)  || 10000.0,
        position_usd:        parseFloat(document.getElementById("f_position").value) || 100.0,
        interval_sec:        parseInt(document.getElementById("f_interval").value)   || 300,
      };
      updateChainModeUI(targetChain);
      try {
        const res  = await fetch("/api/filters", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.ok) {
          closeModal();
          showToast("Filter & Chain berhasil disimpan!", "✅");
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
      if (activeCategory === "all") {
          const merged = [];
          const seen = new Set();
          
          const mergeList = (list, tag, tagColor) => {
              (list || []).forEach(t => {
                  const addr = (t.address || "").toLowerCase();
                  if (!seen.has(addr)) {
                      seen.add(addr);
                      const cloned = {...t, strat_tags: [{name: tag, color: tagColor}]};
                      merged.push(cloned);
                  } else {
                      const existing = merged.find(x => (x.address||"").toLowerCase() === addr);
                      if (existing && !existing.strat_tags.find(x => x.name === tag)) {
                          existing.strat_tags.push({name: tag, color: tagColor});
                      }
                  }
              });
          };

          mergeList(globalState.akashi_zone, "AKASHI", "#dc2626");
          mergeList(globalState.siap_lp, "Siap LP", "#2fd97b");
          mergeList(globalState.smart_lp, "SMART", "#f43f5e");
          mergeList(globalState.cto_lp, "CTO", "#a855f7");
          mergeList(globalState.slow_cook_lp, "Slow Cook", "#2fd97b");
          mergeList(globalState.dip_chop, "30% Dip", "#3b82f6");
          mergeList(globalState.flip_lp, "FLIP", "#f97316");
          mergeList(globalState.momentum_5m, "5M", "#f59e0b");
          mergeList(globalState.absorption, "Absorb", "#5d667a");
          mergeList(globalState.break_ath, "ATH", "#14b8a6");
          
          rawList = merged;
          // Update the badge count dynamically
          const bAll = document.getElementById("badgeAll");
          if (bAll) bAll.innerText = rawList.length;
      }
      else if (activeCategory === "akashi")      rawList = globalState.akashi_zone || [];
      else if (activeCategory === "dip")         rawList = globalState.dip_chop || [];
      else if (activeCategory === "slow_cook")   rawList = globalState.slow_cook_lp || [];
      else if (activeCategory === "siap")        rawList = globalState.siap_lp    || [];
      else if (activeCategory === "smart")       rawList = globalState.smart_lp   || [];
      else if (activeCategory === "flip")        rawList = globalState.flip_lp    || [];
      else if (activeCategory === "cto")         rawList = globalState.cto_lp     || [];
      else if (activeCategory === "momentum_5m") rawList = globalState.momentum_5m || [];
      else if (activeCategory === "absorption")  rawList = globalState.absorption || [];
      else if (activeCategory === "break_ath")   rawList = globalState.break_ath  || [];
      else if (activeCategory === "history")     rawList = globalState.signal_history || [];
      else                                       rawList = globalState.gaps        || [];

      const pillEl = document.getElementById("activeCatPill");
      if (pillEl) pillEl.innerText = `${rawList.length} pool`;

      // History lookup for recurring token badges
      const histLookup = {};
      (globalState.signal_history || []).forEach(h => {
        const a = (h.address || "").toLowerCase();
        if (a && (!histLookup[a] || h.count > histLookup[a].count)) {
          histLookup[a] = h;
        }
      });

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
        filtered = [...filtered].sort((a, b) => (b.vl || 0) - (a.vl || 0) || (b.vol || 0) - (a.vol || 0));
      }

      // Empty State
      if (!filtered.length) {
        const msgs = {
          slow_cook:   "Belum ada token memenuhi kriteria 🍲 Slow Cook (Skor ≥ 90).",
          dip:         "Belum ada token memenuhi kriteria 📉 30% Dip Chop (Drop 20-45%, ER ≤ 10, Range 1H ≤ 4%).",
          all:         "Belum ada token di semua kategori sinyal.",
            siap:        "Belum ada token memenuhi kriteria Siap LP (V/L ≥ 0.5x, Buy% ≥ 46%, Drop 1h ≥ -8%, ATH Drop ≤ 85%).",
          cto:         "Belum ada token memenuhi kriteria 👑 CTO Revival LP (CTO verified, V/L ≥ 0.5x, Buy% ≥ 46%, ATH Drop ≤ 85%).",
          momentum_5m: "Belum ada token memenuhi kriteria ⚡ 5M Momentum (Vol 5m > $100k, V/L ≥ 0.5x, MC ≥ $1M, Usia ≥ 24h).",
          absorption:  "Belum ada sinyal akumulasi/absorption terdeteksi saat ini (MC ≥ $1M, Usia ≥ 24h, V/L ≥ 0.5x, Buy% ≥ 46%).",
          break_ath:   "Belum ada token Break ATH terkonfirmasi (MC ≥ $1M, Usia ≥ 24h, V/L ≥ 0.5x).",
          history:     "Belum ada riwayat sinyal aktif yang tercatat dalam 24 jam terakhir (5M Momentum, Siap LP, CTO, Break ATH, Absorption).",
          gaps:        "Tidak ada token radar yang berada di luar kriteria (Hard Filter: MC ≥ $1M & Usia ≥ 24h).",
        };
        const searchMsg = searchQuery ? `Tidak ditemukan token yang cocok dengan pencarian "<b>${searchQuery}</b>".` : (msgs[activeCategory] || msgs.siap);
        container.innerHTML = `
          <div class="empty-box">
            <div class="empty-icon" style="display:none"></div>
            <div class="empty-title">Tidak Ada Token</div>
            <div class="empty-desc">${searchMsg}<br>Coba ubah filter Chain atau sesuaikan tuning di menu ⚙️.</div>
          </div>`;
        updateFooter(0, globalState.total_scanned || 0);
        return;
      }

      // ===== SPECIALIZED VIEW FOR 24H SIGNAL HISTORY =====
      if (activeCategory === "history") {
        if (viewMode === "table") {
          let tRows = "";
          filtered.forEach((t, idx) => {
            const isRh = (t.chain || "SOL").toUpperCase() === "RH";
            const chainBadge = isRh
              ? `<span class="chain-pill rh" style="font-size:9px;padding:1px 4px;margin-left:4px">RH</span>`
              : `<span class="chain-pill sol" style="font-size:9px;padding:1px 4px;margin-left:4px">SOL</span>`;
            const rankBadge = getRankBadge(idx);
            const mcapStr = formatUsd(t.mcap || 0);
            const vlStr = (t.vl || 0).toFixed(1) + "x";
            const avatarHtml = "";

            let stratCls = "state-momentum", stratIcon = "⚡";
            if (t.strategy_key === "siap_lp") { stratCls = "state-chop"; stratIcon = "🟢"; }
            else if (t.strategy_key === "cto_lp") { stratCls = "state-cto"; stratIcon = "👑"; }
            else if (t.strategy_key === "break_ath") { stratCls = "state-ath"; stratIcon = "🚀"; }
            else if (t.strategy_key === "absorption") { stratCls = "state-absorption"; stratIcon = "📡"; }

            const timePillsHtml = renderTimePills(t.timestamps_wib, t.summary_times, 3);

            tRows += `
              <tr class="arc-tr">
                <td class="arc-td" style="width:36px;text-align:center;white-space:nowrap">${rankBadge}</td>
                <td class="arc-td" style="white-space:nowrap;min-width:180px">
                  <div style="display:flex;align-items:center;gap:6px">
                    ${avatarHtml}
                    <div>
                      <div style="display:flex;align-items:center">
                        <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" style="font-weight:800;font-size:13.5px;color:#fff;text-decoration:none">${t.symbol || "?"}</a>
                        ${chainBadge} ${t.strat_tags ? t.strat_tags.map(tg => `<span style="font-size:9px; color:${tg.color}; border:1px solid ${tg.color}40; padding:1px 4px; border-radius:3px; margin-left:4px;">${tg.name}</span>`).join("") : ""}
                  </div>
                  <div style="color:var(--text-muted);font-size:11px">${t.name || ""}</div>
                    </div>
                  </div>
                </td>
                <td class="arc-td" style="text-align:center;white-space:nowrap;width:130px">
                  <span class="state-pill ${stratCls}">${stratIcon} ${t.strategy || t.strategy_key}</span>
                </td>
                <td class="arc-td mono" style="text-align:center;font-weight:700;color:#38bdf8;font-size:13px;white-space:nowrap;width:75px">${vlStr}</td>
                <td class="arc-td" style="text-align:center;white-space:nowrap;width:85px">
                  <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-chart" style="padding:3px 10px;font-size:11px;font-weight:700">GMGN ↗</a>
                </td>
                <td class="arc-td mono" style="font-weight:700;color:#fff;white-space:nowrap;width:95px">${mcapStr}</td>
                <td class="arc-td mono" style="text-align:center;white-space:nowrap;width:95px">
                  <span style="background:rgba(59,130,246,0.18);color:#60a5fa;border:1px solid rgba(59,130,246,0.35);padding:2px 7px;border-radius:6px;font-weight:700;font-size:11px">${t.count}x Muncul</span>
                </td>
                <td class="arc-td mono" style="min-width:240px;max-width:440px">
                  ${timePillsHtml}
                </td>
              </tr>`;
          });

          container.innerHTML = `
            <div class="table-container">
              <table class="arc-table">
                <thead class="arc-thead">
                  <tr>
                    <th style="width:36px;text-align:center">#</th>
                    <th style="min-width:180px">TOKEN</th>
                    <th style="text-align:center;width:130px">STRATEGI</th>
                    <th style="text-align:center;width:75px">V/L</th>
                    <th style="text-align:center;width:85px">AKSI</th>
                    <th style="width:95px">MCAP</th>
                    <th style="text-align:center;width:95px">FREKUENSI</th>
                    <th style="min-width:240px;max-width:440px">JAM SINYAL (WIB)</th>
                  </tr>
                </thead>
                <tbody>${tRows}</tbody>
              </table>
            </div>`;
        } else {
          let cardsHtml = '<div class="card-list">';
          filtered.forEach((t, idx) => {
            const isRh = (t.chain || "SOL").toUpperCase() === "RH";
            const chainBadge = isRh
              ? `<span class="chain-pill rh" style="font-size:9px;padding:1px 4px;margin-left:4px">RH</span>`
              : `<span class="chain-pill sol" style="font-size:9px;padding:1px 4px;margin-left:4px">SOL</span>`;
            const rankBadge = getRankBadge(idx);
            const mcapStr = formatUsd(t.mcap || 0);
            const vlStr = (t.vl || 0).toFixed(1) + "x";
            const avatarHtml = "";
            const dexsUrl = isRh ? `https://fomo.family/token/${t.address}` : `https://dexscreener.com/solana/${t.address}`;

            let stratCls = "state-momentum", stratIcon = "⚡";
            if (t.strategy_key === "siap_lp") { stratCls = "state-chop"; stratIcon = "🟢"; }
            else if (t.strategy_key === "cto_lp") { stratCls = "state-cto"; stratIcon = "👑"; }
            else if (t.strategy_key === "break_ath") { stratCls = "state-ath"; stratIcon = "🚀"; }
            else if (t.strategy_key === "absorption") { stratCls = "state-absorption"; stratIcon = "📡"; }

            const timePillsHtml = renderTimePills(t.timestamps_wib, t.summary_times, 3);

            cardsHtml += `
              <div class="token-card" style="border-left: 3px solid #3b82f6;">
                <div class="card-row-top">
                  <div class="token-info-left">
                    ${rankBadge}
                    ${avatarHtml}
                    <div class="token-name-block">
                      <div class="symbol-row" style="display:flex;align-items:center;gap:4px">
                        <span class="token-symbol"><a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" style="color:#fff;text-decoration:none">${t.symbol || "?"}</a></span>
                        ${chainBadge} ${t.strat_tags ? t.strat_tags.map(tg => `<span style="font-size:9px; color:${tg.color}; border:1px solid ${tg.color}40; padding:1px 4px; border-radius:3px; margin-left:4px;">${tg.name}</span>`).join("") : ""}
                  </div>
                  <div class="token-sub-row">
                        <span class="token-name">${t.name || ""}</span>
                      </div>
                    </div>
                  </div>
                  <div style="text-align:right">
                    <span class="state-pill ${stratCls}">${stratIcon} ${t.strategy || t.strategy_key}</span>
                    <div style="margin-top:4px"><span style="background:rgba(59,130,246,0.18);color:#60a5fa;border:1px solid rgba(59,130,246,0.35);padding:2px 7px;border-radius:6px;font-size:11px;font-weight:700">${t.count}x Muncul</span></div>
                  </div>
                </div>

                <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:8px 10px;margin:8px 0">
                  <div style="font-size:11px;color:var(--text-muted);margin-bottom:5px;display:flex;justify-content:space-between;align-items:center">
                    <span>🕒 Riwayat Sinyal 24 Jam Terakhir:</span>
                    <span style="color:#fbbf24;font-family:var(--font-mono);font-weight:700">${t.count}x Terdeteksi</span>
                  </div>
                  ${timePillsHtml}
                </div>

                <div class="metrics-grid" style="grid-template-columns: 1fr 1fr; margin-bottom: 8px;">
                  <div class="metric-cell"><div class="m-label">MCAP</div><div class="m-val">${mcapStr}</div></div>
                  <div class="metric-cell"><div class="m-label">V/L TURNOVER</div><div class="m-val" style="color:#38bdf8">${vlStr}</div></div>
                </div>

                <div style="display:flex;gap:6px;margin-top:8px">
                  <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-chart" style="flex:1;text-align:center;padding:7px;font-size:11px;font-weight:700">GMGN ↗</a>
                  <a href="${dexsUrl}" target="_blank" rel="noopener noreferrer" class="btn-copy-inline" style="flex:1;text-align:center;padding:7px;font-size:11px;justify-content:center">${isRh ? "FOMO ↗" : "DexS ↗"}</a>
                  <button class="btn-copy-inline" onclick="copyCA('${t.address}', this)" style="padding:7px 10px;font-size:11px">Salin CA</button>
                </div>
              </div>
            `;
          });
          cardsHtml += '</div>';
          container.innerHTML = cardsHtml;
        }
        updateFooter(filtered.length, globalState.total_scanned || 0);
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
          const avatarHtml = "";

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
          if (activeCategory === "cto")            { spClass = "state-cto";        spIcon = "👑"; }
          else if (activeCategory === "momentum_5m") { spClass = "state-momentum"; spIcon = "⚡"; }
          else if (activeCategory === "break_ath")  { spClass = "state-ath";        spIcon = "🚀"; }
          else if (mState === "ABSORPTION")    { spClass = "state-absorption"; spIcon = "📡"; }
          else if (mState === "REACCUMULATION"){ spClass = "state-reaccum";    spIcon = "🔄"; }
          else if (mState === "DISTRIBUTION")  { spClass = "state-distrib";    spIcon = "⚠️"; }
          else if (t.is_chop)                  { spClass = "state-chop";       spIcon = "🟢"; }
          const spLabel = activeCategory === "cto" ? "CTO Revival 👑" : (activeCategory === "momentum_5m" ? "5M Momentum ⚡" : (activeCategory === "break_ath" ? "Break ATH ✓" : (t.status_label || (t.is_chop ? "Chop Sideways" : "Monitoring"))));

          const dexsUrl = isRh ? `https://fomo.family/token/${addrStr}` : `https://dexscreener.com/solana/${addrStr}`;
          const dexsLabel = isRh ? "FOMO ↗" : "DexS ↗";

          // Sub-details if CTO, Momentum, ATH
          let subRowHtml = "";
          if (activeCategory === "cto") {
            subRowHtml = `<span style="color:#d8b4fe;font-size:10px;margin-left:6px;white-space:nowrap">👑 Dev ${t.dev_team_hold || 0}%</span>`;
          } else if (activeCategory === "momentum_5m") {
            subRowHtml = `<span style="color:#fde047;font-size:10px;margin-left:8px;white-space:nowrap">⚡ V5: ${formatUsd(t.vol_5m || t.vol || 0)}</span>`;
          } else if (activeCategory === "break_ath") {
            subRowHtml = `<span style="color:var(--cyan-light);font-size:10px;margin-left:8px;white-space:nowrap">🚀 +${t.breakout_pct || 0}%</span>`;
          } else if (activeCategory === "gaps" && t.gap_reasons && t.gap_reasons.length) {
            const gapShort = t.gap_reasons[0].split('(')[0].trim();
            subRowHtml = `<span style="color:var(--red);font-size:10px;margin-left:8px;white-space:nowrap">❌ ${gapShort}</span>`;
          }

          // Narrative badges & social link
          const narrHtml = (t.narratives || []).map(n => {
            let cls = 'narr-cto';
            if (n.includes('AI')) cls = 'narr-ai';
            else if (n.includes('Smart')) cls = 'narr-smart';
            else if (n.includes('Bluechip')) cls = 'narr-blue';
            return `<span class="narr-pill ${cls}">${n}</span>`;
          }).join('');
          const twitterHtml = t.twitter_url ? `<a href="${t.twitter_url}" target="_blank" rel="noopener noreferrer" class="social-link" title="Twitter / X">𝕏</a>` : '';
          const histInfo = histLookup[(t.address || "").toLowerCase()];
          const histShortTime = histInfo ? (histInfo.summary_times_short || histInfo.last_seen_wib || "") : "";
          const histBadge = (histInfo && histInfo.count > 1)
            ? `<span class="badge-hist-compact" title="Sinyal muncul ${histInfo.count}x: ${escapeHtml(histInfo.summary_times)}">🕒 ${histInfo.count}x${histShortTime ? ' (' + escapeHtml(histShortTime) + ')' : ''}</span>`
            : "";

          tRows += `
            <tr class="arc-tr ${rankClass}">
              <td class="arc-td" style="width:36px;text-align:center;white-space:nowrap">${rankBadge}</td>
              <td class="arc-td" style="white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis">
                <div style="display:flex;align-items:center;gap:5px">
                  ${avatarHtml}
                  <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" style="font-weight:700;font-size:13px;color:var(--text-main);text-decoration:none">${t.symbol || "?"}</a>
                  ${chainBadge} ${t.strat_tags ? t.strat_tags.map(tg => `<span style="font-size:9px; color:${tg.color}; border:1px solid ${tg.color}40; padding:1px 4px; border-radius:3px; margin-left:4px;">${tg.name}</span>`).join("") : ""}
                    ${narrHtml}
                  ${twitterHtml}
                  ${subRowHtml}
                  ${histBadge}
                </div>
              </td>
              <td class="arc-td mono" style="text-align:center;font-weight:700;color:var(--teal);font-size:12.5px;white-space:nowrap">${vlStr}</td>
              <td class="arc-td" style="text-align:center;white-space:nowrap">
                <div style="display:flex;align-items:center;justify-content:center;gap:5px">
                  <span class="state-pill ${spClass}" style="padding:1px 5px;font-size:9.5px;white-space:nowrap" title="${spLabel}">${spIcon}</span>
                  <a href="${t.url || '#'}" target="_blank" rel="noopener noreferrer" class="btn-chart">GMGN ↗</a>
                </div>
              </td>
              <td class="arc-td mono" style="font-weight:700;color:var(--text-main);white-space:nowrap">${mcapStr}</td>
              <td class="arc-td mono" style="color:var(--text-sub);white-space:nowrap">${liqStr}</td>
              <td class="arc-td mono" style="white-space:nowrap">${erBadge}</td>
              <td class="arc-td mono" style="font-size:11.5px;white-space:nowrap">
                <span class="${p1 >= 0 ? 'vol-pos' : 'vol-neg'}">${p1Str}</span> <span style="color:var(--text-dim);font-size:10px">/</span> <span class="${p5 >= 0 ? 'vol-pos' : 'vol-neg'}">${p5Str}</span>
              </td>
              <td class="arc-td mono" style="color:var(--green);font-weight:600;white-space:nowrap">${buyRatio}%</td>
              <td class="arc-td" style="white-space:nowrap">
                <span class="safety-pill safety-${sGrade}" title="Score: ${sScore}/100">${sGrade} ${sScore}</span>
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
                  <th style="text-align:center">V/L</th>
                  <th style="text-align:center">AKSI</th>
                  <th>MCAP</th>
                  <th>LIQ</th>
                  <th>ER</th>
                  <th>1H / 5M</th>
                  <th>BUY %</th>
                  <th>SAFE</th>
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
        const cardClass = activeCategory === "cto" ? "cto-card" : (activeCategory === "momentum_5m" ? "momentum-card" : (activeCategory === "break_ath" ? "ath-card" : (isRh ? "rh-card" : "sol-card")));
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
        const avatarHtml = "";

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
        if (activeCategory === "cto")            { spClass = "state-cto";        spIcon = "👑"; }
        else if (activeCategory === "momentum_5m") { spClass = "state-momentum"; spIcon = "⚡"; }
        else if (activeCategory === "break_ath")  { spClass = "state-ath";        spIcon = "🚀"; }
        else if (mState === "ABSORPTION")    { spClass = "state-absorption"; spIcon = "📡"; }
        else if (mState === "REACCUMULATION"){ spClass = "state-reaccum";    spIcon = "🔄"; }
        else if (mState === "DISTRIBUTION")  { spClass = "state-distrib";    spIcon = "⚠️"; }
        else if (t.is_chop)                  { spClass = "state-chop";       spIcon = "🟢"; }

        const spLabel = activeCategory === "cto"
          ? "CTO Revival 👑"
          : (activeCategory === "momentum_5m"
            ? "5M Momentum ⚡"
            : (activeCategory === "break_ath"
              ? "Break ATH ✓"
              : (t.status_label || (t.is_chop ? "Chopping Sideways" : "Monitoring"))));

        // CTO / 5M Momentum / Break ATH Extra Banner
        let athHtml = "";
        if (activeCategory === "cto") {
          athHtml = `
            <div class="ath-info-row" style="border-left: 2px solid #a855f7; background: rgba(168, 85, 247, 0.08);">
              <div class="ath-tag" style="color:#d8b4fe"><span>👑 CTO:</span> Verified</div>
              <div class="ath-tag" style="color:var(--green-light)"><span>🛡️ Dev Hold:</span> ${t.dev_team_hold || 0}%</div>
              <div class="ath-tag"><span>👥 Holders:</span> ${formatTx(t.holder_count || 0)}</div>
            </div>`;
        } else if (activeCategory === "momentum_5m") {
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

        // Narrative badges & social link
        const narrHtml = (t.narratives || []).map(n => {
          let cls = 'narr-cto';
          if (n.includes('AI')) cls = 'narr-ai';
          else if (n.includes('Smart')) cls = 'narr-smart';
          else if (n.includes('Bluechip')) cls = 'narr-blue';
          return `<span class="narr-pill ${cls}">${n}</span>`;
        }).join('');
        const twitterHtml = t.twitter_url ? `<a href="${t.twitter_url}" target="_blank" rel="noopener noreferrer" class="social-link" title="Twitter / X">𝕏</a>` : '';

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

        const histInfoCard = histLookup[(t.address || "").toLowerCase()];
        const cardHistHtml = (histInfoCard && histInfoCard.count > 1)
          ? `<div style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.25);border-radius:6px;padding:5px 8px;margin:6px 0;font-size:11px;color:#fbbf24;font-family:var(--font-mono);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px" title="Seluruh riwayat: ${escapeHtml(histInfoCard.summary_times)}">
              <span>🕒 <b>Sinyal 24h:</b> ${escapeHtml(histInfoCard.summary_times_short || histInfoCard.summary_times)}</span>
              <span style="background:rgba(251,191,36,0.18);padding:1px 6px;border-radius:4px;font-weight:700">${histInfoCard.count}x</span>
            </div>`
          : "";

        html += `
          <div class="token-card ${cardClass} ${rankClass}">
            <div class="card-row-top">
              <div class="token-info-left">
                ${rankBadge}
                ${avatarHtml}
                <div class="token-name-block">
                  <div class="symbol-row" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap">
                    <span class="token-symbol">${t.symbol || "?"}</span>
                    ${chainBadge} ${t.strat_tags ? t.strat_tags.map(tg => `<span style="font-size:9px; color:${tg.color}; border:1px solid ${tg.color}40; padding:1px 4px; border-radius:3px; margin-left:4px;">${tg.name}</span>`).join("") : ""}
                    ${narrHtml}
                    ${twitterHtml}
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
                <div class="fee-hour" style="color:#38bdf8">${vlStr}</div>
                <div class="fee-day" style="color:var(--text-muted);font-size:10px">V/L TURNOVER</div>
              </div>
            </div>

            <div class="metrics-grid">
              <div class="metric-cell"><div class="m-label">MCAP</div><div class="m-val">${mcapStr}</div></div>
              <div class="metric-cell"><div class="m-label">LIQ TVL</div><div class="m-val">${liqStr}</div></div>
              <div class="metric-cell"><div class="m-label">24H VOL</div><div class="m-val">${formatUsd(t.vol || 0)}</div></div>
              <div class="metric-cell"><div class="m-label">ER SCORE</div><div class="m-val">${erBadge}</div></div>
            </div>

            ${athHtml}
            ${cardHistHtml}

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

        // Sync Chain Mode dari Backend / Telegram
        const srvChain = (data.chain_mode || (data.filters && data.filters.chain_mode) || "BOTH").toUpperCase();
        if (srvChain !== currentChainMode) {
          updateChainModeUI(srvChain);
        }

        // KPI / Stat Chips
        const topVl = (data.top_vl !== undefined && data.top_vl !== null) ? Number(data.top_vl).toFixed(1) + "x" : (data.top_yield ? `${Number(data.top_yield).toFixed(1)}x` : "0.0x");
        const bathCount = (data.counts && data.counts.break_ath) || (data.break_ath ? data.break_ath.length : 0);

        // Category Badges
        const bAkashi = document.getElementById("badgeAkashi");
        if (bAkashi) bAkashi.innerText = (data.counts && data.counts.akashi_zone) || (data.akashi_zone ? data.akashi_zone.length : 0);
        document.getElementById("badgeDip").innerText = data.counts.dip_chop || 0;
        document.getElementById("badgeSlow").innerText = data.counts.slow_cook || 0;
        document.getElementById("badgeSiap").innerText   = data.counts.siap || 0;
        const bSmart = document.getElementById("badgeSmart");
        if (bSmart) bSmart.innerText = data.counts.smart_lp || 0;
        const bFlip = document.getElementById("badgeFlip");
        if (bFlip) bFlip.innerText = data.counts.flip_lp || 0;
        const bCto = document.getElementById("badgeCto");
        if (bCto) bCto.innerText = (data.counts && data.counts.cto) || (data.cto_lp ? data.cto_lp.length : 0);
        const bM5 = document.getElementById("badgeM5");
        if (bM5) bM5.innerText = (data.counts && data.counts.momentum_5m) || (data.momentum_5m ? data.momentum_5m.length : 0);
        document.getElementById("badgeAbsorb").innerText = data.counts.absorption || 0;
        document.getElementById("badgeBath").innerText   = bathCount;
        document.getElementById("badgeGaps").innerText   = data.counts.gaps || 0;
        const bHist = document.getElementById("badgeHistory");
        if (bHist) bHist.innerText = (data.counts && data.counts.history) || (data.signal_history ? data.signal_history.length : 0);

        // Render Caching / Smart Diffing: Hindari render ulang DOM jika data tidak berubah
        const currentFingerprint = `${data.scanned_timestamp || 0}_${data.scanning ? 1 : 0}_${(data.counts && data.counts.total) || 0}_${(data.counts && data.counts.history) || 0}_${activeCategory}_${activeChain}_${viewMode}_${searchQuery}`;
        if (currentFingerprint !== lastRenderStateKey) {
          lastRenderStateKey = currentFingerprint;
          renderCards();
        }
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
      else if (e.key === "a" || e.key === "A") setCategoryTab("all");
      else if (e.key === "k" || e.key === "K") setCategoryTab("akashi");
      else if (e.key === "1") setCategoryTab("siap");
      else if (e.key === "m" || e.key === "M") setCategoryTab("smart");
      else if (e.key === "2") setCategoryTab("cto");
      else if (e.key === "o" || e.key === "O") setCategoryTab("slow_cook");
      else if (e.key === "d" || e.key === "D") setCategoryTab("dip");
      else if (e.key === "f" || e.key === "F") setCategoryTab("flip");
      else if (e.key === "3") setCategoryTab("momentum_5m");
      else if (e.key === "4") setCategoryTab("absorption");
      else if (e.key === "5") setCategoryTab("break_ath");
      else if (e.key === "6") setCategoryTab("gaps");
      else if (e.key === "7") setCategoryTab("history");
      else if (e.key === "s" || e.key === "S") { e.preventDefault(); triggerScan(); }
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
                    "chain_mode": app_state["filters"].get("chain_mode", "BOTH"),
                    "counts": app_state["counts"],
                    "top_yield": app_state["top_yield"],
                    "top_vl": app_state.get("top_vl", 0.0),
                    "filters": app_state["filters"],
                    "siap_lp": app_state["siap_lp"],
                    "akashi_zone": app_state.get("akashi_zone", []),
                    "slow_cook_lp": app_state.get("slow_cook_lp", []),
                    "dip_chop": app_state.get("dip_chop", []),
                    "smart_lp": app_state.get("smart_lp", []),
                    "flip_lp": app_state.get("flip_lp", []),
                    "cto_lp": app_state.get("cto_lp", []),
                    "momentum_5m": app_state.get("momentum_5m", []),
                    "absorption": app_state["absorption"],
                    "break_ath": app_state.get("break_ath", []),
                    "gaps": app_state["gaps"],
                    "signal_history": app_state.get("signal_history", []),
                }
            self.send_json(data)
            return

        if path == "/api/signal-history":
            with state_lock:
                sig_data = {
                    "ok": True,
                    "updated_at": app_state.get("scanned_at"),
                    "total": len(app_state.get("signal_history", [])),
                    "history": app_state.get("signal_history", []),
                }
            self.send_json(sig_data)
            return

        if path in ("/api/health", "/health"):
            self.send_json({"status": "ok", "port": PORT, "time": now_wib().isoformat()})
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

            threading.Thread(target=perform_scan, args=(True,), daemon=True).start()
            self.send_json({"ok": True, "status": "scanning_started"})
            return

        if path == "/api/chain":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                req = json.loads(body)
                target_mode = str(req.get("chain_mode", "")).upper().strip()
                if target_mode not in ("SOL", "RH", "BOTH"):
                    self.send_json({"ok": False, "error": f"Mode tidak valid: {target_mode}. Gunakan SOL, RH, atau BOTH"}, status=400)
                    return

                with state_lock:
                    app_state["filters"]["chain_mode"] = target_mode
                    current_filters = dict(app_state["filters"])

                save_persistent_filters(current_filters)
                if hasattr(bot_sol_lp, "save_persistent_chain_mode"):
                    try:
                        bot_sol_lp.save_persistent_chain_mode(target_mode)
                    except Exception:
                        pass

                with state_lock:
                    is_busy = app_state["scanning"]
                if not is_busy:
                    threading.Thread(target=perform_scan, args=(True,), daemon=True).start()

                self.send_json({"ok": True, "chain_mode": target_mode})
                return
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=400)
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
                if "chain_mode" in new_f and hasattr(bot_sol_lp, "save_persistent_chain_mode"):
                    try:
                        bot_sol_lp.save_persistent_chain_mode(str(new_f["chain_mode"]).upper().strip())
                    except Exception:
                        pass

                # Picu scan ulang dengan filter baru
                threading.Thread(target=perform_scan, args=(True,), daemon=True).start()
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
    print(f"⚙️ Parameter     : Min Fee ${initial_filters.get('min_fee_siap_lp', 0.5)}/h │ MC ≥ {bot_sol_lp._usd(initial_filters.get('min_mcap', 1000000.0))} │ V/L ≥ {initial_filters.get('min_vl', 0.5)}x │ Buy% ≥ {initial_filters.get('min_buy_ratio', 46.0)}%")
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
