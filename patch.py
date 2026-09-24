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

    new_code = """    # Gaps logic
    min_liq = float(conf.get("min_liq", 50000.0))
    min_vl = float(conf.get("min_vl", 2.0))
    max_5m = float(conf.get("max_5m_goyang", 15.0))
    max_1h = float(conf.get("max_1h_goyang", 25.0))
    max_er = float(conf.get("max_er", 20.0))

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
            reasons.append(f"Liq < {_usd(min_liq)}")
        if vl < min_vl:
            reasons.append(f"V/L < {min_vl:.1f}x")
        if abs(p5) > max_5m:
            reasons.append(f"5m > {max_5m:.0f}%")
        if abs(p1) > max_1h:
            reasons.append(f"1h > {max_1h:.0f}%")
        if er > max_er:
            reasons.append(f"ER > {max_er:.1f}")
        if fee < min_fee_siap_lp:
            reasons.append(f"Fee < ${_usd(min_fee_siap_lp)}/h")

        if reasons:
            p_copy = dict(p)
            p_copy["gap_reasons"] = reasons
            gaps_candidates.append(p_copy)

    gaps = deduplicate_best_tokens(gaps_candidates)
    gaps.sort(key=lambda x: (-x.get("fee_hour", 0.0), -x.get("vol", 0.0)))

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    top_limit = conf.get("top_n_display", 10)
    chain_mode = str(conf.get("chain_mode", "BOTH")).upper()

    top_yield = siap_lp[0]["fee_hour"] if siap_lp else (absorption[0]["fee_hour"] if absorption else 0.0)
    top_yield_str = f"${top_yield:.2f}/h" if top_yield > 0 else "$0.00/h"

    mode_label = "Solana + Robinhood" if chain_mode == "BOTH" else ("Robinhood" if chain_mode == "RH" else "Solana")
    mode_icon = "🪐🦊" if chain_mode == "BOTH" else ("🦊" if chain_mode == "RH" else "🪐")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚡ <b>CHOP LP RADAR — MULTI-CHAIN</b> ⚡",
        f"📅 <code>{now_str}</code> • {mode_icon} <b>{mode_label}</b>",
        f"📊 Dipindai: <b>{len(tokens)} Token</b> • 💰 Top Yield: <b>{top_yield_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🟢 <b>SIAP LP (Chop Sideways)</b>",
        f"<i>Fee >= ${min_fee_siap_lp:.2f}/h • MC >= {_usd(min_mcap)}</i>",
        "",
    ]

    if siap_lp:
        for idx, t in enumerate(siap_lp[:top_limit]):
            sym = html.escape(str(t.get("symbol") or "?"))
            sym_link = f'<a href="{t["url"]}"><b>{sym}</b></a>'
            fee_h = t.get("fee_hour", 0.0)
            fee_24 = t.get("fee_24h", fee_h * 24)
            fee_str = f"💰 <b>${fee_h:.2f}/h</b> (<i>+${fee_24:.1f}/24h</i>)"
            mc_str = f"MC {_usd(t['mcap'])}"
            er_val = t.get("er", 999.0)
            er_rating = "Prime" if er_val <= 3 else ("Good" if er_val <= 6 else ("Mid" if er_val <= 15 else "High"))
            er_str = f"🎯 ER {er_val:.1f}"
            vl_str = f"V/L {t.get('vl', 0.0):.1f}x"
            p5 = t.get("p5", 0.0)
            p1 = t.get("p1", 0.0)
            p5_str = ("+" if p5 >= 0 else "") + f"{p5:.1f}%"
            p1_str = ("+" if p1 >= 0 else "") + f"{p1:.1f}%"
            vol_str = f"⚡ 5m {p5_str} • 1h {p1_str}"

            buys = int(t.get("buys") or 0)
            sells = int(t.get("sells") or 0)
            buy_ratio = round(t.get("buy_ratio", 50.0))
            tx_str = f"🟢 <b>{buy_ratio}% Buy</b>"
            if buys > 0 or sells > 0:
                b_fmt = f"{buys/1000:.1f}k" if buys >= 1000 else str(buys)
                s_fmt = f"{sells/1000:.1f}k" if sells >= 1000 else str(sells)
                tx_str += f" ({b_fmt}/{s_fmt})"

            score = round(t.get("score") or 0.0)
            grade = "A" if score >= 80 else ("B" if score >= 65 else ("C" if score >= 50 else "D"))
            t10 = t.get("top10_rate", 0.0)
            dev = t.get("dev_team_hold", 0.0)
            safety_str = f"🛡️ Audit <b>{grade}{score}</b> (t10 {t10:.0f}% • dev {dev:.0f}%)"

            age_h = t.get("age_hours", 9999.0)
            age_str = f"{max(1, round(age_h * 60))}m" if age_h < 1 else (f"{age_h:.0f}h" if age_h < 24 else (f"{round(age_h/24)}d" if age_h < 9000 else ""))

            chain = str(t.get("chain", "SOL")).upper()
            chain_badge = "🦊 RH" if chain == "RH" else "🪐 SOL"
            venue = "UniswapV4" if chain == "RH" else ("Meteora" if "meteora" in t.get("url", "") else "Raydium")
            meta_tag = f"({chain_badge}) • <i>{venue}</i>" + (f" • ⏱ {age_str}" if age_str else "")

            addr = t.get("address", "")
            ca_code = f"<code>{addr}</code>" if addr else ""

            if idx < 3:
                # Top 1-3 Podium Cards
                medal = "🏆" if idx == 0 else ("🥈" if idx == 1 else "🥉")
                lines.append(f"{medal} {sym_link} {ca_code} {meta_tag}")
                lines.append(f"   {fee_str} | {mc_str} | {vl_str} | {er_str} | 🛡️ {grade}{score}")
                lines.append("")
            else:
                # Rank 4+ Compact Rows
                rank_num = f"#{idx + 1}"
                badge_icon = "🦊" if chain == "RH" else "🪐"
                lines.append(f"🔹 {rank_num} {badge_icon} {sym_link} {ca_code}")
                lines.append(f"   <b>${fee_h:.2f}/h</b> | {mc_str} | ER {er_val:.1f} | 🛡️ {grade}{score}")
                lines.append("")

        if len(siap_lp) > top_limit:
            lines.append(f"<i>...dan {len(siap_lp) - top_limit} pool lainnya</i>")
    else:
        lines.append("<i>(Belum ada pool memenuhi syarat Siap LP)</i>")

    # 2. ⚡ 5M MOMENTUM (5m Vol > $100k • Pump Up • Liq >= $10k)
    m5_list = momentum_5m_candidates or []
    if m5_list:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚡ <b>5M MOMENTUM</b>")
        lines.append("")
        for m in m5_list[:6]:
            sym = html.escape(str(m.get("symbol") or "?"))
            sym_link = f'<a href="{m["url"]}"><b>{sym}</b></a>'
            fee_h = m.get("fee_hour", 0.0)
            mc_str = _usd(m.get("mcap", 0.0))
            vol5_str = _usd(m.get("vol_5m", 0.0))
            p5 = m.get("p5", 0.0)
            p5_str = f"+{p5:.1f}%" if p5 > 0 else f"{p5:.1f}%"
            badge = "🦊" if str(m.get("chain", "SOL")).upper() == "RH" else "🪐"
            m_ca = f"<code>{m.get('address', '')}</code>" if m.get('address') else ""
            b_ratio = round(m.get("buy_ratio", 50.0))
            buys = m.get("buys", 0)
            sells = m.get("sells", 0)
            tx_str = f"🟢 <b>{b_ratio}% Buy</b>"
            if buys > 0 or sells > 0:
                tx_str += f" ({buys}/{sells})"

            lines.append(f"🔹 {badge} {sym_link} {m_ca} ➔ <b>${fee_h:.2f}/h</b> | MC {mc_str}")
            lines.append(f"  5m {p5_str} | V5 {vol5_str} | {tx_str}")
            lines.append("")

    # 3. Break ATH LP Radar — hanya tampil jika ada kandidat terkonfirmasi >= 15 menit
    bath_list = break_ath_candidates or []
    if bath_list:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🚀 <b>BREAK ATH LP</b>")
        lines.append("")
        for b in bath_list[:6]:
            sym = html.escape(str(b.get("symbol") or "?"))
            sym_link = f'<a href="{b["url"]}"><b>{sym}</b></a>'
            fee_str  = f"${b['fee_hour']:.2f}/h"
            mc_str   = _usd(b['mcap'])
            ath_str  = _usd(b['ath_mcap'])
            dur_str  = f"{b['duration_mins']}m"
            badge = "🦊" if str(b.get("chain", "SOL")).upper() == "RH" else "🪐"
            pct_sign = "+" if b["breakout_pct"] >= 0 else ""
            b_ca     = f"<code>{b.get('address', '')}</code>" if b.get('address') else ""
            vl_str   = f"V/L {b.get('vl', 0.0):.1f}x"
            b_ratio  = round(b.get("buy_ratio", 50.0))

            lines.append(f"🔹 {badge} {sym_link} {b_ca} ➔ <b>{fee_str}</b> | MC {mc_str} ({pct_sign}{b['breakout_pct']:.0f}%)")
            lines.append(f"  ⏱ {dur_str} | 📊 {vl_str} | 🟢 {b_ratio}% Buy")
            lines.append("")

    # 4. Kategori Absorption Radar
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📡 <b>ABSORPTION RADAR</b>")
    lines.append("")

    if absorption:
        for t in absorption[:top_limit]:
            sym = html.escape(str(t.get("symbol") or "?"))
            sym_link = f'<a href="{t["url"]}"><b>{sym}</b></a>'
            fee_str = f"${t['fee_hour']:.2f}/h"
            mc_str = f"MC {_usd(t['mcap'])}"
            status = html.escape(str(t.get("status_label", "")).strip())
            badge = "🦊" if str(t.get("chain", "SOL")).upper() == "RH" else "🪐"
            t_ca = f"<code>{t.get('address', '')}</code>" if t.get('address') else ""

            lines.append(f"🔹 {badge} {sym_link} {t_ca}")
            lines.append(f"   💰 <b>{fee_str}</b> | {mc_str} | {status}")
        if len(absorption) > top_limit:
            lines.append(f"<i>...dan {len(absorption) - top_limit} token lainnya</i>")
    else:
        lines.append("<i>(Belum ada sinyal absorption baru)</i>")

    # 5. GAPS RADAR (Top 5)
    if gaps:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ <b>GAPS RADAR (Top 5)</b>")
        lines.append("<i>(Token potensial namun gagal kriteria)</i>")
        lines.append("")
        for g in gaps[:5]:
            sym = html.escape(str(g.get("symbol") or "?"))
            sym_link = f'<a href="{g.get("url", "")}"><b>{sym}</b></a>'
            fee_str = f"${g.get('fee_hour', 0.0):.2f}/h"
            mc_str = _usd(g.get('mcap', 0.0))
            badge = "🦊" if str(g.get("chain", "SOL")).upper() == "RH" else "🪐"
            g_ca = f"<code>{g.get('address', '')}</code>" if g.get('address') else ""
            gap_reason = g.get("gap_reasons", ["-"])[0]

            lines.append(f"🔹 {badge} {sym_link} {g_ca}")
            lines.append(f"   💰 <b>{fee_str}</b> | MC {mc_str} | ❌ {gap_reason}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    report_body = "\\n".join(lines)
    # 1 space / baris kosong sebelum dan sesudah isi chat agar tampilan Telegram tidak bertumpuk terlalu rapat
    return f"\\u200b\\n{report_body}\\n\\u200b"\n"""

    final_content = content[:start_idx] + new_code + content[end_idx:]

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(final_content)
    
    print("Patched successfully.")

if __name__ == "__main__":
    patch()
