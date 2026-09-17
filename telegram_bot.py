#!/usr/bin/env python3
"""Telegram Bot Alert Engine for Chop Radar LP Terminal.
Multi-chain support (Robinhood, Solana, Arc) with clean HTML formatting.
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from pathlib import Path


def load_tg_config() -> tuple[str, str, bool]:
    """Cari konfigurasi telegram di sol-hp-filters, hp-filters, v5-filters, atau v4-filters."""
    for fn in ["sol-hp-filters.json", "hp-filters.json", "v5-filters.json", "v4-filters.json"]:
        fp = Path(__file__).resolve().parent / fn
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                return (
                    str(data.get("telegram_token") or "").strip(),
                    str(data.get("telegram_chat_id") or "").strip(),
                    bool(data.get("telegram_enabled", False)),
                )
            except (OSError, json.JSONDecodeError):
                pass
    return "", "", False


def send_telegram(token: str, chat_id: str, message: str) -> tuple[bool, str]:
    if not token or not chat_id:
        return False, "Token atau Chat ID belum diisi"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
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
            resp = json.loads(res.read().decode())
            return (True, "") if resp.get("ok") else (False, str(resp.get("description", "Unknown")))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:250]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, str(e)


def _usd(n: float) -> str:
    if not n:
        return "$0"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"${n / 1e3:.1f}k"
    return f"${n:,.0f}"


def _lp_price(lp: dict, key: str) -> str:
    v = lp.get(key)
    return f"${v:.8g}" if v else "--"


def fmt_absorption_alert(t: dict) -> str:
    m = t.get("micro", {})
    lp = m.get("lp_range", {})
    chain = str(t.get("chain") or "RH").upper()
    sym = str(t.get("symbol") or "?")
    score = m.get("score", 0)
    vl = t.get("vl", 0)
    er = t.get("er", 0)
    mcap = t.get("mcap", 0)
    fee_h = t.get("fee_hour", 0)
    fee_24 = t.get("fee_24h", 0)
    be = t.get("breakeven_hours", 0)

    fee_h_s = f"${fee_h:.2f}/h" if fee_h else "--"
    fee_24_s = f"${fee_24:.2f}/24h" if fee_24 else "--"
    be_s = f"{be:.0f}h" if be and be < 9999 else "--"

    links = []
    if t.get("meteora"):
        links.append(f'<a href="{t.get("meteora", "")}">Meteora DLMM</a>')
    if t.get("gmgn"):
        links.append(f'<a href="{t.get("gmgn", "")}">GMGN</a>')
    if t.get("dexscreener"):
        links.append(f'<a href="{t.get("dexscreener", "")}">DexScreener</a>')
    if t.get("fomo"):
        links.append(f'<a href="{t.get("fomo", "")}">FOMO</a>')

    lines = [
        f"🟢 <b>[ENTRY LP ABSORPTION]</b> <b>${sym}</b> <code>[{chain}]</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>Score:</b> {score:.0f}/100 | <b>MC:</b> {_usd(mcap)}",
        f"⚡ <b>V/L:</b> {vl:.1f}x | <b>ER:</b> {er:.1f} (Chop)",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 <b>Range LP:</b> {_lp_price(lp, 'lower')} ➔ {_lp_price(lp, 'upper')}",
        f"🛡️ <i>{lp.get('note', '--')}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>Est Fee @$100:</b> {fee_h_s} | {fee_24_s} (BE: {be_s})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔗 <b>Link:</b> {' | '.join(links)}",
    ]
    return "\n".join(lines)


def fmt_fee_decay_alert(t: dict) -> str:
    chain = str(t.get("chain") or "RH").upper()
    sym = str(t.get("symbol") or "?")
    p1 = t.get("p1", 0)
    sign = "+" if p1 >= 0 else ""
    fee_h = t.get("fee_hour", 0)
    vl = t.get("vl", 0)
    trend = t.get("fee_trend", "--")
    open_link = t.get("meteora") or t.get("gmgn", "")
    btn_name = "Meteora DLMM" if t.get("meteora") else "GMGN"

    lines = [
        f"🟡 <b>[PERINGATAN FEE DECAY]</b> <b>${sym}</b> <code>[{chain}]</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📉 <b>Fee Trend:</b> {trend} | <b>Fee:</b> ${fee_h:.2f}/h",
        f"⚡ <b>V/L:</b> {vl:.1f}x | <b>1h Chg:</b> {sign}{p1:.1f}%",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Volume transaksi pool mulai melemah drastis.</i>",
        "💡 <b>Saran:</b> Pantau ketat & pertimbangkan exit LP dalam 1-2 jam.",
        "━━━━━━━━━━━━━━━━━━━━",
        f'<a href="{open_link}">🔗 Buka {btn_name}</a>',
    ]
    return "\n".join(lines)


def fmt_emergency_exit_alert(t: dict) -> str:
    m = t.get("micro", {})
    chain = str(t.get("chain") or "RH").upper()
    sym = str(t.get("symbol") or "?")
    p1, p5 = t.get("p1", 0), t.get("p5", 0)
    s1, s5 = ("+" if p1 >= 0 else ""), ("+" if p5 >= 0 else "")
    open_link = t.get("meteora") or t.get("gmgn", "")
    btn_name = "Meteora DLMM" if t.get("meteora") else "GMGN"

    lines = [
        f"🔴 <b>[EXIT LP SEKARANG!]</b> <b>${sym}</b> <code>[{chain}]</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💥 <b>Status:</b> {m.get('state_title', 'DISTRIBUTION')} (Breakout)",
        f"📊 <b>5m:</b> {s5}{p5:.1f}% | <b>1h:</b> {s1}{p1:.1f}% | <b>Buy:</b> {m.get('buy_ratio', 50):.0f}%",
        "━━━━━━━━━━━━━━━━━━━━",
        "🚨 <b>PERINGATAN KERAS:</b>",
        "Harga koin keluar dari zona sideways dan bergerak directional tajam!",
        "Segera tarik likuiditas untuk menghindari <b>Impermanent Loss parah</b>.",
        "━━━━━━━━━━━━━━━━━━━━",
        f'<a href="{open_link}">🔗 Buka {btn_name} Sekarang</a>',
    ]
    return "\n".join(lines)


def send_alert(alert_type: str, token_data: dict) -> tuple[bool, str]:
    tk, chat_id, enabled = load_tg_config()
    if not enabled:
        return False, "Telegram alert dinonaktifkan"
    if not tk or not chat_id:
        return False, "Token/Chat ID belum dikonfigurasi"
    if alert_type == "absorption":
        msg = fmt_absorption_alert(token_data)
    elif alert_type == "fee_decay":
        msg = fmt_fee_decay_alert(token_data)
    elif alert_type == "emergency":
        msg = fmt_emergency_exit_alert(token_data)
    else:
        return False, f"Unknown type: {alert_type}"
    return send_telegram(tk, chat_id, msg)


def send_test_alert(token: str, chat_id: str) -> tuple[bool, str]:
    msg = (
        "🚀 <b>TEST ALERT — Chop Radar Terminal</b>\n\n"
        "✅ <b>Koneksi Bot Telegram Berhasil Terhubung!</b>\n\n"
        "Notifikasi otomatis yang akan Anda terima:\n"
        "• 🟢 <b>Entry Absorption:</b> Sweet spot LP (Volume tinggi, harga chop, ER rendah)\n"
        "• 🟡 <b>Fee Decay:</b> Peringatan awal volume & fee per jam melemah\n"
        "• 🔴 <b>Emergency Exit:</b> Sinyal cabut LP saat harga breakout parah\n\n"
        "<i>Terminal aktif berjalan 24/7 di VPS Anda.</i>"
    )
    return send_telegram(token, chat_id, msg)


if __name__ == "__main__":
    print("=== Telegram Bot Test ===")
    tk, chat_id, _ = load_tg_config()
    if not tk:
        print("Config filters belum ada telegram_token/telegram_chat_id")
    else:
        ok, err = send_test_alert(tk, chat_id)
        print("OK!" if ok else f"Gagal: {err}")