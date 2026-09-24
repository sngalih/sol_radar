import re
import sys

def patch():
    file_path = r"d:\Galih\LP\bot_sol_lp.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    start_str = '    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")'
    end_str = '    return f"\\u200b\\n{report_body}\\n\\u200b"\n'

    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx == -1 or end_idx == -1:
        print("Could not find start or end bounds.")
        sys.exit(1)

    end_idx += len(end_str)

    new_code = """    chain_mode = str(conf.get("chain_mode", "BOTH")).upper()
    top_limit = conf.get("top_n_display", 10)

    mode_label = "Solana + Robinhood" if chain_mode == "BOTH" else ("Robinhood" if chain_mode == "RH" else "Solana")
    mode_icon = "🪐🔹" if chain_mode == "BOTH" else ("🔹" if chain_mode == "RH" else "🪐")

    lines = [
        f"{mode_icon} {mode_label}",
        "",
        "SIAP LP (Chop Sideways)"
    ]

    if siap_lp:
        for idx, t in enumerate(siap_lp[:top_limit]):
            sym = html.escape(str(t.get("symbol") or "?"))
            sym_link = f'<a href="{t["url"]}">{sym}</a>'
            fee_h = t.get("fee_hour", 0.0)
            mc_str = _usd(t['mcap'])
            er_val = t.get("er", 999.0)
            vl_str = f"V/L {t.get('vl', 0.0):.1f}x"
            score = round(t.get("score") or 0.0)
            grade = "A" if score >= 80 else ("B" if score >= 65 else ("C" if score >= 50 else "D"))

            chain = str(t.get("chain", "SOL")).upper()
            badge = "🔹" if chain == "RH" else "🪐"
            addr = t.get("address", "")

            lines.append(f"• {badge} [{sym_link}] ➔ ${fee_h:.2f}/h │ MC {mc_str}")
            lines.append(f"🎯 ER {er_val:.1f} │ 📊 {vl_str} │ 🛡️ {grade}{score}")
            lines.append(f"📋 <code>{addr}</code>")
            lines.append(f"🔗 <a href=\"{t['url']}\">GMGN</a>")
            lines.append("")

        if len(siap_lp) > top_limit:
            lines.append(f"<i>...dan {len(siap_lp) - top_limit} pool lainnya</i>")
    else:
        lines.append("(Belum ada pool memenuhi syarat Siap LP)")

    # 2. 5M MOMENTUM
    lines.append("")
    lines.append("5M MOMENTUM")
    m5_list = momentum_5m_candidates or []
    if m5_list:
        for m in m5_list[:6]:
            sym = html.escape(str(m.get("symbol") or "?"))
            sym_link = f'<a href="{m["url"]}">{sym}</a>'
            fee_h = m.get("fee_hour", 0.0)
            mc_str = _usd(m.get("mcap", 0.0))
            vol5_str = _usd(m.get("vol_5m", 0.0))
            p5 = m.get("p5", 0.0)
            p5_str = f"+{p5:.1f}%" if p5 > 0 else f"{p5:.1f}%"
            badge = "🔹" if str(m.get("chain", "SOL")).upper() == "RH" else "🪐"
            addr = m.get("address", "")
            b_ratio = round(m.get("buy_ratio", 50.0))
            buys = m.get("buys", 0)
            sells = m.get("sells", 0)
            tx_str = f"🟢 {b_ratio}% Buy"
            if buys > 0 or sells > 0:
                tx_str += f" ({buys}/{sells})"

            lines.append(f"• {badge} [{sym_link}] ➔ ${fee_h:.2f}/h │ MC {mc_str}")
            lines.append(f"⚡ 5m {p5_str} │ 🌊 Vol5m {vol5_str} │ {tx_str}")
            lines.append(f"📋 <code>{addr}</code>")
            lines.append(f"🔗 <a href=\"{m['url']}\">GMGN</a>")
            lines.append("")
    else:
        lines.append("(Belum ada token memenuhi syarat Momentum)")

    # 3. BREAK ATH LP
    bath_list = break_ath_candidates or []
    if bath_list:
        lines.append("")
        lines.append("BREAK ATH LP")
        for b in bath_list[:6]:
            sym = html.escape(str(b.get("symbol") or "?"))
            sym_link = f'<a href="{b["url"]}">{sym}</a>'
            fee_str  = f"${b['fee_hour']:.2f}/h"
            mc_str   = _usd(b['mcap'])
            dur_str  = f"{b['duration_mins']}m"
            badge = "🔹" if str(b.get("chain", "SOL")).upper() == "RH" else "🪐"
            pct_sign = "+" if b["breakout_pct"] >= 0 else ""
            addr = b.get('address', '')
            vl_str   = f"V/L {b.get('vl', 0.0):.1f}x"
            b_ratio  = round(b.get("buy_ratio", 50.0))

            lines.append(f"• {badge} [{sym_link}] ➔ {fee_str} │ MC {mc_str} ({pct_sign}{b['breakout_pct']:.0f}%)")
            lines.append(f"⏱ {dur_str} │ 📊 {vl_str} │ 🟢 {b_ratio}% Buy")
            lines.append(f"📋 <code>{addr}</code>")
            lines.append(f"🔗 <a href=\"{b['url']}\">GMGN</a>")
            lines.append("")

    # 4. ABSORPTION RADAR
    lines.append("")
    lines.append("ABSORPTION RADAR")
    if absorption:
        for t in absorption[:top_limit]:
            sym = html.escape(str(t.get("symbol") or "?"))
            sym_link = f'<a href="{t["url"]}">{sym}</a>'
            fee_str = f"${t['fee_hour']:.2f}/h"
            mc_str = f"MC {_usd(t['mcap'])}"
            status = html.escape(str(t.get("status_label", "")).strip())
            badge = "🔹" if str(t.get("chain", "SOL")).upper() == "RH" else "🪐"
            addr = t.get('address', '')

            lines.append(f"• {badge} [{sym_link}] ➔ {fee_str} │ {mc_str} │ {status}")
            lines.append(f"📋 <code>{addr}</code>")
            lines.append(f"🔗 <a href=\"{t['url']}\">GMGN</a>")
            lines.append("")
        if len(absorption) > top_limit:
            lines.append(f"<i>...dan {len(absorption) - top_limit} token lainnya</i>")
    else:
        lines.append("(Belum ada sinyal absorption baru)")

    # 5. GAPS RADAR
    if gaps:
        lines.append("")
        lines.append("GAPS RADAR")
        for g in gaps[:5]:
            sym = html.escape(str(g.get("symbol") or "?"))
            url = g.get("url") or f"https://gmgn.ai/sol/token/{g.get('address','')}"
            sym_link = f'<a href="{url}">{sym}</a>'
            fee_str = f"${g.get('fee_hour', 0.0):.2f}/h"
            mc_str = _usd(g.get('mcap', 0.0))
            badge = "🔹" if str(g.get("chain", "SOL")).upper() == "RH" else "🪐"
            addr = g.get('address', '')
            gap_reason = g.get("gap_reasons", ["-"])[0]

            lines.append(f"• {badge} [{sym_link}] ➔ {fee_str} │ MC {mc_str}")
            lines.append(f"❌ {gap_reason}")
            lines.append(f"📋 <code>{addr}</code>")
            lines.append(f"🔗 <a href=\"{url}\">GMGN</a>")
            lines.append("")

    report_body = "\\n".join(lines).strip()
    return f"{report_body}"\n"""

    final_content = content[:start_idx] + new_code + content[end_idx:]

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(final_content)
    
    print("Patched successfully.")

if __name__ == "__main__":
    patch()
