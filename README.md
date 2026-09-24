# 🪓 Chop Radar — Telegram LP Reporter Bot (Dual-Chain GMGN Edition)

Bot Telegram otomatis yang memindai meme coin dan pool LP di **Solana (SOL)** dan **Robinhood (RH)** dari **GMGN Open API** setiap **5 menit** dan mengirimkan laporan terstruktur untuk peluang **Chop Sideways LP Farming**. Logika perhitungan 100% identik dengan **Dashboard V5**!

---

## ⚡ Keunggulan Bot Ini

1. **Dual-Chain Multi-Radar**: Memindai token **Solana (🟠)** dan **Robinhood (🟢)** secara terpadu atau spesifik per rantai.
2. **Sumber Data GMGN Teruji**: Mengambil data ranking likuiditas dan volume langsung dari GMGN Open API resmi dengan session cookie anti-rate limit.
3. **Automatic Meteora Fallback**: Jika GMGN Solana sempat terganggu, bot otomatis beralih sementara ke Meteora DLMM API.
4. **Direct GMGN / Dex Link**: Nama token langsung berupa link menuju chart & swap di `gmgn.ai` (atau DEX terkait).
5. **Deduplikasi Cerdas**: Satu token hanya tampil 1 kali (pool/kandidat terbaik).
6. **Lencana Visual**:
   - 🟠 **Lingkaran Orange**: Token Solana (SOL)
   - 🟢 **Lingkaran Hijau**: Token Robinhood (RH)
7. **Perintah Interaktif Lengkap**:
   - `/scan` - Pemindaian instan sesuai mode aktif
   - `/scan sol` - Quick scan khusus Solana 🟠
   - `/scan rh` - Quick scan khusus Robinhood 🟢
   - `/scan both` - Quick scan kedua rantai 🟠🟢
   - `/chain <both|sol|rh>` - Mengganti mode pemantauan otomatis langsung dari chat Telegram!

---

## 📋 Format Laporan Telegram

Pesan dikirim otomatis tiap 5 menit dengan format yang rapi dan terstruktur:

```text
🚀 CHOP RADAR (SOL & ROBINHOOD)
⏱ 17/09/2026 14:02 · Tiap 5 Menit
🏷 🟠 Solana │ 🟢 Robinhood
━━━━━━━━━━━━━━━━━━━━
🟢 SIAP LP (Fee ≥ $3/h & MC ≥ $500k)
• 🟢 musegram ➔ $4.20/h │ MC $520k │ ER 3.2
• 🟠 WISH ➔ $3.18/h │ MC $605k │ ER 14.8
━━━━━━━━━━━━━━━━━━━━
📡 ABSORPTION RADAR (MC ≥ $500k)
• 🟠 PAID ➔ $0.66/h │ MC $22.0M │ 🎯 Chopping Sideways
• 🟢 GOOGL ➔ $0.27/h │ MC $5.49M │ 🎯 Chopping Sideways
• 🟠 wifout ➔ $2.58/h │ MC $4.50M │ 🎯 Ugly Reaccumulation
━━━━━━━━━━━━━━━━━━━━
💡 Tap nama token untuk langsung membuka chart & swap di GMGN
```

- **Deduplikasi Cerdas**: Satu token hanya tampil **1 pool DLMM terbaik** (tidak ada lagi koin ganda seperti `PAID` muncul berulang).
- **🟢 SIAP LP**: Pool yang 100% lolos kriteria Chop Sideways LP ($ER \le 20$, $V/L \ge 2x$, $|p5| \le 15\%$, $|p1| \le 80\%$) dengan estimasi fee $\ge \$3.00$/jam dan Mcap $\ge \$500k$.
- **📡 ABSORPTION RADAR**: Pool dengan status `Absorption (Sweet Spot)` atau `Ugly Reaccumulation` di mana volume transaksi tinggi terserap dengan pergerakan harga sempit.

---

## 🛠️ Panduan Persiapan Bot Telegram

### 1. Buat Bot di Telegram
1. Buka Telegram dan cari **[@BotFather](https://t.me/BotFather)**.
2. Kirim perintah `/newbot`.
3. Masukkan nama bot Anda (misal: `Meteora LP Radar`).
4. Masukkan username bot (harus berakhiran `bot`, misal: `meteora_lp_radar_bot`).
5. Simpan **HTTP API Token** yang diberikan (contoh: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`).

### 2. Dapatkan Chat ID Anda
1. Buka Telegram dan cari **[@userinfobot](https://t.me/userinfobot)**.
2. Klik **Start**. Bot akan membalas dengan `Id` akun Anda (contoh: `987654321`).
3. Buka bot yang baru saja Anda buat di langkah 1, lalu klik **Start** agar bot memiliki izin mengirim pesan ke Anda.

### 3. Konfigurasi File `.env`
Salin file template `.env.example` menjadi `.env`:
```bash
# Di Windows Command Prompt / PowerShell:
copy .env.example .env

# Di Linux / Mac:
cp .env.example .env
```
Buka file `.env` dan masukkan Token dan Chat ID Anda:
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=987654321
SCAN_INTERVAL=300
POSITION_USD=100
MIN_LIQ=20000
```

---

## 🚀 Cara Menjalankan Bot

### 1. Uji Coba (Dry-Run / Preview tanpa kirim Telegram)
```bash
python bot_sol_lp.py --dry-run
```

### 2. Jalankan 1x Pemindaian & Langsung Kirim ke Telegram
```bash
python bot_sol_lp.py --once
```

### 3. Jalankan Otomatis 24/7 (Setiap 5 Menit)
```bash
python bot_sol_lp.py
```

### 4. Menjalankan di VPS (Background via Screen)
```bash
# Buat screen baru
screen -S sol_bot python3 bot_sol_lp.py

# Lepas screen agar tetap jalan 24/7 di background:
# Tekan tombol Ctrl + A, lalu tekan D

# Untuk masuk kembali ke screen sewaktu-waktu:
screen -r sol_bot
```

---

## 📤 Cara Push ke GitHub Pribadi

> File kredensial `.env` dan log runtime sudah dilindungi oleh `.gitignore` sehingga tidak akan bocor ke GitHub publik.

Jalankan perintah berikut di folder proyek Anda:

```bash
# 1. Inisialisasi Git (jika belum)
git init

# 2. Tambahkan file ke Git
git add bot_sol_lp.py .env.example .gitignore requirements.txt README.md

# 3. Commit perubahan
git commit -m "feat: Solana Meteora DLMM LP Telegram Bot 5-minute reporter"

# 4. Buat branch main
git branch -M main

# 5. Hubungkan ke repository GitHub Anda (ganti URL dengan repo Anda)
git remote add origin https://github.com/<username-anda>/<nama-repo-anda>.git

# 6. Push ke GitHub
git push -u origin main
```

---

## ⚙️ Ringkasan Parameter Filter

| Variabel | Default | Penjelasan |
| :--- | :--- | :--- |
| `SCAN_INTERVAL` | `300` | Frekuensi scan otomatis dalam detik (300 detik = 5 menit). |
| `POSITION_USD` | `100` | Modal dasar posisi simulasi per pool untuk menghitung fee. |
| `MIN_LIQ` | `20000` | Batas minimal likuiditas pool (\$20,000). |
| `MIN_VL` | `2.0` | Batas minimal rasio Volume 24h / Liquidity ($V/L$). |
| `MAX_5M` | `15.0` | Batas maksimal volatilitas harga 5 menit ($\le 15\%$). |
| `MAX_1H` | `80.0` | Batas maksimal volatilitas harga 1 jam ($\le 80\%$). |
| `MAX_ER` | `20.0` | Batas maksimal Efficiency Ratio ($\le 20.0$, ideal $\le 5.0$). |
| `MIN_MCAP` | `500000` | Batas minimal Market Cap token (\$500,000). |
| `MIN_FEE_SIAP_LP` | `1.0` | Batas minimal estimasi fee/jam untuk kategori Siap LP (\$1.00/jam). |
