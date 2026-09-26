# Robinhood Meme LP Terminal — Project Guidelines & Domain Rules

## 1. Core LP Strategy: Chop Sideways LP (Tunggal & Terfokus)
- **Fokus Utama**: Hanya gunakan strategi tunggal: **Chop Sideways LP Farming**.
- **Larangan Keras**: Jangan pernah menambahkan strategi sekunder (seperti "Pump Momentum", "Trend Following", dsb.) atau mengubah penandaan sinyal secara sepihak tanpa instruksi eksplisit dari pengguna.
- **Filosofi Inti**:
  - Jangan mengejar APR tinggi semata; cari meme coin yang **sudah pump → volume tetap tinggi → harga mulai chop/sideways**.
  - Volume ↑, Price → = Kandidat LP ideal.
  - Volume ↑↑, Price ↑↑↑ = Dilarang LP (sedang naik vertikal).
  - Price ↓↓↓ = Dilarang LP (sedang dump/crash bebas).

## 2. Indikator & Metrik Wajib
- **Efficiency Ratio (ER)**:
  - Rumus: `ER = abs(p1) / vl` (di mana `p1` = % change 1 jam, `vl` = Volume/Liquidity).
  - ER rendah (≤ 20, idealnya ≤ 5) menandakan volume tinggi dengan pergerakan harga sempit (sweet spot LP).
- **Volatilitas Simetris**:
  - Pengecekan 5m (`|p5| <= max_5m`) dan 1h (`|p1| <= max_1h`) harus simetris (menolak koin yang pump gila maupun koin yang dump bebas).
- **On-Chain Safety Filters**:
  - `top10_rate <= 45%` (Whale risk)
  - `dev_team_hold <= 20%` (Dev dump risk)
  - `is_wash == False` & `is_honeypot == False` (**Hard Filter Mutlak** untuk SEMUA strategi: Siap LP, 5M Momentum, Break ATH, Absorption Radar, dan Gaps Radar tanpa pengecualian)
- **Fee Decay & Monitoring**:
  - Pantau fee per jam menggunakan perbandingan rolling 2-window untuk mendeteksi pelemahan dini.
  - Tiga pemicu keluar LP: (1) Fee/hour mati/melemah, (2) Breakout harga directional, (3) Toxic inventory.

## 3. Struktur Dashboard & Workspace Invariants
- **Port Invariant**:
  - `dashboard.py`: Port `8765` (baseline / jangan dirusak).
  - `Dashboard Rev1.py`: Port `8766` (versi pengembangan aktif).
  - `dashboard v4.py`: Port `8769` (versi V4 Robinhood Chain).
  - `dashboard v4 sol.py`: Port `8770` (versi V4 Solana Chain).
  - `dashboard v5.py`: Port `8769` (versi V5 Unified Multi-Chain: Robinhood + Solana + Arc).
  - `dashboard v6.py`: Port `8772` (versi V6 Unified Multi-Chain + Second Wave Hunter Panel).
  - `dashboard hp.py`: Port `8771` (versi Mobile / VPS Multi-Chain).
  - `dashboard sol hp.py`: Port `8771` (versi Mobile / VPS Solana Meteora DLMM Edition: Anti-429, zero rate limits, real fees, listen `0.0.0.0`).
- **Tampilan UI**:
  - Tampilkan **Current MC** (Market Cap), bukan sekadar harga koin.
  - Tabel utama hanya berisi koin yang **100% lolos kriteria (CHOP)**.
  - Koin yang belum lolos kriteria harus masuk ke panel **Belum Kriteria (Gaps)** beserta label alasan spesifik.
  - **Desktop Screen Resolution**: Layar laptop pengguna adalah `1920 x 1200`. Variabel `--container-max` di `web.py` diatur ke `1840px` agar tabel penuh dan tidak terpotong horizontal scrollbar.
  - **Susunan Kolom Tabel (Table Mode)**: Urutan kolom wajib: `#` │ `TOKEN` │ `FEE/H` │ `V/L` │ `AKSI` │ `MCAP` │ `LIQ` │ `ER` │ `1H / 5M` │ `BUY %` │ `SAFE`. Kolom CA/VENUE dihapus agar tabel padat dan tombol AKSI (GMGN) berada langsung di kolom ke-5.

## 4. VPS Deployment Invariants
- **Path Folder VPS**: Proyek berada di `~/sol_radar` (BUKAN `~/LP`).
- **PM2 Services**:
  - `sol_bot`: Menjalankan bot Telegram `bot_sol_lp.py`.
  - `sol_web`: Menjalankan web dashboard `web.py`.
- **Perintah Deploy Standar**:
  ```bash
  cd ~/sol_radar
  git pull
  pm2 restart sol_bot
  pm2 restart sol_web
  ```

## 5. Telegram Bot Reporting Rules (`bot_sol_lp.py`)
- **Tampilan Ultra-Minimalis**:
  - Tanpa dekorasi garis pembatas panjang (`━━━━━━━━━━━━`).
  - Judul kategori ditebalkan: `<b>SIAP LP (Chop Sideways)</b>`, `<b>5M MOMENTUM</b>`, `<b>BREAK ATH LP</b>`, `<b>ABSORPTION RADAR</b>`, `<b>GAPS RADAR</b>`.
- **Format Token Bersih**:
  - Diawali langsung dengan badge rantai (`🔹` RH / `🔸` SOL). **DILARANG ada bullet point `•`** di depan badge.
  - Nama token adalah link langsung tanpa kurung siku `[]` diikuti pemisah pipe `│` (contoh: `🔹 <a href="...">Token</a> │ $X.XX/h │ MC $X.XM`).
  - **DILARANG menampilkan baris Contract Address (CA)** (`<code>{addr}</code>`).
  - **DILARANG memakai emoji `📋` dan `🔗`**.
  - **DILARANG menampilkan baris GMGN terpisah** karena nama token sudah menjadi tautan langsung menuju GMGN.
  - Baris detail/alasan di bawah token diawali indentasi 2 spasi (contoh: `  ❌ {alasan}`).
  - Karakter pembanding pada alasan Gaps wajib di-escape HTML (`&lt;` dan `&gt;`) agar tidak memicu error Telegram 400.
  - Top 5 GAPS Radar wajib disertakan di bagian paling bawah laporan rutin.

## 6. Sinkronisasi Waktu Pemindaian (Scan Timing Synchronization)
- **Jadwal Jam Dinding Kelipatan 5 Menit**:
  - `bot_sol_lp.py` (Telegram bot) dan `web.py` (Web dashboard) **WAJIB** mengeksekusi pemindaian pada waktu yang sama persis di setiap kelipatan 5 menit jam dinding (:00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55).
  - Formula perhitungan boundary: `next_boundary = int((time.time() // interval + 1) * interval)` di mana `interval = 300` detik.
  - Hal ini menjamin data di chat Telegram dan data di Web Dashboard selalu 100% konsisten, mutakhir, dan tersinkronisasi tanpa jeda (drift).
