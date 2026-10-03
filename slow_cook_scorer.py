import re
from typing import Dict, Any, Tuple

# Daftar hitam kata kunci yang sering dipakai untuk koin artis, politik, atau meme murahan.
# Kata-kata ini diubah menjadi lowercase untuk pencocokan.
BLACKLIST_KEYWORDS = [
    # Politik & Publik Figur
    "trump", "elon", "harris", "biden", "obama", "tate", "saylor", "vitalik", 
    "cz", "sbf", "maga", "boden", "tremp", "kamala",
    # Hewan Meme (Anjing, Kucing, dll)
    "doge", "pepe", "cat", "inu", "shib", "wif", "bonk", "floki", "pnut", 
    "popcat", "michi", "slerf", "bome", "mochi", "dog", "frog", "ape", "monkey",
    # Hype / Ponzi terms
    "safe", "moon", "100x", "1000x", "ponzi", "rug", "fomo", "cum"
]

def evaluate_slow_cook(token: Dict[str, Any]) -> Tuple[bool, str, int]:
    """
    Mengevaluasi token untuk strategi 'Slow Cook' menggunakan sistem poin penalti (Demerit).
    Nilai awal: 100.
    Batas lolos: >= 90.
    
    Returns:
        (is_passed, reason, final_score)
    """
    score = 100
    penalties = []
    
    # 1. CTO Check (Instant Reject)
    # Beberapa versi API menggunakan 'cto_flag', 'is_cto', atau string
    if token.get('cto_flag', 0) == 1 or token.get('is_cto', False):
        score -= 100
        penalties.append("Terdeteksi CTO Token")
        
    # 2. Keyword Matching (Instant Reject)
    symbol = str(token.get('symbol', '')).lower()
    name = str(token.get('name', '')).lower()
    
    # Mencari kecocokan kata kunci dengan regex whole word boundaries
    found_keywords = []
    for kw in BLACKLIST_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, symbol) or re.search(pattern, name):
            found_keywords.append(kw)
            
    if found_keywords:
        score -= 100
        penalties.append(f"Blacklist Keyword: {', '.join(found_keywords)}")
        
    # 3. Kelengkapan Sosial Media (Dev Serius)
    has_twitter = bool(token.get('twitter_link')) or bool(token.get('twitter'))
    has_website = bool(token.get('website_link')) or bool(token.get('website'))
    
    if not has_twitter and not has_website:
        score -= 30
        penalties.append("Tidak ada Website & Twitter")
    elif not has_twitter:
        score -= 15
        penalties.append("Tidak ada Twitter")
    elif not has_website:
        score -= 15
        penalties.append("Tidak ada Website")
        
    # 4. Old Large Cap setelah Pump (Rawan Dump)
    age_hours = float(token.get('age_hours', 0) or 0)
    p24h = float(token.get('p24h', 0) or 0)
    
    # Jika umur lebih dari 3 hari (72h) dan baru saja terbang lebih dari 300%
    if age_hours > 72.0 and p24h > 3.0:
        score -= 50
        penalties.append(f"Old Cap Pumped (Umur {age_hours:.1f}h, Pump +{p24h*100:.0f}%)")
        
    # 5. Downtrend / Falling Knife
    p1h = float(token.get('p1h', 0) or 0)
    if p24h < -0.10 or p1h < -0.05:
        score -= 15
        penalties.append("Sedang Downtrend / Falling Knife")
        
    # Kalkulasi Akhir
    is_passed = score >= 90
    
    if is_passed:
        reason = "Aman & Memenuhi Syarat"
    else:
        reason = " | ".join(penalties)
        
    return is_passed, reason, score

# Testing sederhana jika file dijalankan langsung
if __name__ == "__main__":
    test_token = {
        "symbol": "DOGE",
        "name": "Dogecoin",
        "cto_flag": 0,
        "twitter_link": "https://twitter.com/doge",
        "website_link": "",
        "age_hours": 100,
        "p24h": 0.05
    }
    passed, reason, score = evaluate_slow_cook(test_token)
    print(f"Passed: {passed}, Score: {score}, Reason: {reason}")
