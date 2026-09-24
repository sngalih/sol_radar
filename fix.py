import sys

file_path = r"d:\Galih\LP\bot_sol_lp.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix f-strings inside bot_sol_lp.py
# Look for lines.append(f"🔗 <a href="{t['url']}">GMGN</a>")
content = content.replace('f"🔗 <a href=\"{t[\'url\']}\">GMGN</a>"', "f'🔗 <a href=\"{t[\"url\"]}\">GMGN</a>'")
content = content.replace('f"🔗 <a href=\"{m[\'url\']}\">GMGN</a>"', "f'🔗 <a href=\"{m[\"url\"]}\">GMGN</a>'")
content = content.replace('f"🔗 <a href=\"{b[\'url\']}\">GMGN</a>"', "f'🔗 <a href=\"{b[\"url\"]}\">GMGN</a>'")
content = content.replace('f"🔗 <a href=\"{url}\">GMGN</a>"', "f'🔗 <a href=\"{url}\">GMGN</a>'")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
