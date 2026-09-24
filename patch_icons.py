import sys

file_path = r"d:\Galih\LP\bot_sol_lp.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Remove 📋 and space
content = content.replace('lines.append(f"📋 <code>{addr}</code>")', 'lines.append(f"<code>{addr}</code>")')

# Remove 🔗 and space
content = content.replace('lines.append(f\'🔗 <a href="{t["url"]}">GMGN</a>\')', 'lines.append(f\'<a href="{t["url"]}">GMGN</a>\')')
content = content.replace('lines.append(f\'🔗 <a href="{m["url"]}">GMGN</a>\')', 'lines.append(f\'<a href="{m["url"]}">GMGN</a>\')')
content = content.replace('lines.append(f\'🔗 <a href="{b["url"]}">GMGN</a>\')', 'lines.append(f\'<a href="{b["url"]}">GMGN</a>\')')
content = content.replace('lines.append(f\'🔗 <a href="{url}">GMGN</a>\')', 'lines.append(f\'<a href="{url}">GMGN</a>\')')

# Remove brackets around sym_link
content = content.replace('lines.append(f"• {badge} [{sym_link}] ➔', 'lines.append(f"• {badge} {sym_link} ➔')

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Icons and brackets removed successfully.")
