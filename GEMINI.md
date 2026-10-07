# Robinhood Meme LP Terminal — Project Guidelines & Domain Rules

## 1. Core LP Strategy: Chop Sideways LP, Akashi Zone LP & Spot Runner
- **Fokus Utama**: Strategi utama: **Chop Sideways LP Farming**, **⚡ Akashi Zone LP**, dan **🚀 Runner Momentum (Spot Entry)**.
- **Filosofi Inti**:
  - Jangan mengejar APR tinggi semata; cari meme coin yang **sudah pump → volume tetap tinggi → harga mulai chop/sideways**.
  - Volume ↑, Price → = Kandidat LP ideal.
  - Volume ↑↑, Price ↑↑↑ = Dilarang LP (sedang naik vertikal).
  - Price ↓↓↓ = Dilarang LP (sedang dump/crash bebas).
  - **⚡ Akashi Zone LP**: Retracement Fibonacci antara 0.236 dan 0.382 dari origin/low ke ATH dengan sideways konsolidasi stabil.
  - **🚀 Runner Momentum (Spot Entry)**: Strategi khusus entry spot momentum multi-bagger dengan 2 tingkatan karakter:
    - **🏛️ Tier 1 (Established Runner / Wave 2)**: MC $1M – $10M, usia ≥ 12h, konsolidasi re-akumulasi kuat, drawdown ATH ≥ -70%, buyer menopang (`Buy% ≥ 50%`, `V/L ≥ 1.0x`), siap meledak di wave 2.
    - **⚡ Tier 2 (Fresh Breakout / Pump.fun)**: MC $50k – $1M, usia < 24h, baru lulus bonding curve pump.fun, volume 5m masif (`Vol 5m ≥ $20k`, `V/L ≥ 2.0x`), candle pump up (`p5 > 0%`, `Buy% ≥ 52%`), anti-rug aman (renounced mint, dev hold ≤ 15%, bundler ≤ 55%).

## 2. Indikator & Metrik Wajib
- **Efficiency Ratio (ER)**:
  - Rumus: `ER = abs(p1) / vl` (di mana `p1` = % change 1 jam, `vl` = Volume/Liquidity).
  - ER rendah (≤ 20, idealnya ≤ 5) menandakan volume tinggi dengan pergerakan harga sempit (sweet spot LP).
- **Anti-Burn & Anti-Drill-Down Protection Engine**:
  - `buy_ratio >= 46.0%`: Minimal 46% transaksi adalah BUY (menolak koin yang didominasi kepanikan jual / panic dumping).
  - `ath_drawdown >= -85.0%`: MCap saat ini tidak boleh drop lebih dari 85% dari All-Time High (menolak koin zombie / kuburan bagholder).
  - `max_1h <= 20.0%` & `max_5m <= 15.0%`: Pengetatan rentang volatilitas 1 jam simetris untuk konsolidasi sideways sejati.
  - **Asymmetric Downside Guard**: `p1 >= -8.0%` (penurunan 1 jam maks -8%) dan `p5 >= -4.0%` (penurunan 5 menit maks -4%). Menolak koin yang sedang meluncur bebas (*falling knife*).
  - `min_vl >= 0.5x`: Standar rasio perputaran volume terhadap likuiditas (Turnover V/L) universal untuk SEMUA strategi (Siap LP, Akashi Zone, 5M Momentum, Break ATH, Absorption, dan Gaps). Menggantikan filter ambang batas fee/jam arbitrer ($/h) demi objektivitas konsisten lintas timeframe dan pool.
- **Hard Filter Screening Skala Global (Front Gate Filter)**:
  - `min_mcap >= $1,000,000` ($1M USD Market Cap)
  - `min_age_hours >= 24.0` (Usia token minimal 24 jam sejak pembuatan / open trading)
  - Token dengan Market Cap < $1M atau Usia < 24 jam di-drop langsung dari pemindaian LP (Hard Filter Drop).
  - **Pengecualian Khusus Spot Runner Tier 2**: Khusus strategi `🚀 Runner Momentum (Tier 2 Fresh Breakout)`, sistem mengevaluasi koin $50k – $1M & usia < 24h dari feed GMGN 1h dan 5m sebelum filter LP membuang koin muda, sementara seluruh strategi LP tetap terkunci di MC ≥ $1M & Usia ≥ 24h.
- **On-Chain Safety & Anti-Rug Multi-Layer Filters**:
  - `top10_rate <= 45%` (Whale risk)
  - `dev_team_hold <= 20%` (Dev dump risk)
  - `bundler_rate <= 55%` (Maksimal sniped supply bundle block 0)
  - `renounced_mint == 1` (Mint authority wajib dicabut pada rantai Solana)
  - `holder_count >= 100` (Distribusi pemegang token memadai)
  - `is_wash == False` & `is_honeypot == False` & `is_rug_risk == False` (**Hard Filter Mutlak** untuk SEMUA strategi: Siap LP, Akashi Zone, 5M Momentum, Break ATH, Absorption Radar, dan Gaps Radar tanpa pengecualian)
- **Narrative & Social Detection Engine**:
  - Deteksi otomatis tag narasi: `👑 CTO` (Community Take Over), `🤖 AI` (AI Agents), `🧠 Smart` (Smart Money Inflow), `💎 Bluechip` (High market cap & liquidity).
  - Integrasi tautan sosial (Twitter/X & Telegram) untuk verifikasi cepat komunitas.
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
  - **Susunan Kolom Tabel (Table Mode)**: Urutan kolom wajib: `#` │ `TOKEN` │ `V/L` │ `AKSI` │ `MCAP` │ `LIQ` │ `ER` │ `1H / 5M` │ `BUY %` │ `SAFE`. Kolom `FEE/H` dan CA/VENUE dihapus agar tabel bersih, padat, dan tombol AKSI (GMGN) berada langsung di kolom ke-4.

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
  - Judul kategori ditebalkan: `<b>🚀 RUNNER MOMENTUM (Spot Entry)</b>`, `<b>SIAP LP (Chop Sideways)</b>`, `<b>⚡ AKASHI ZONE LP (Fibonacci 0.236-0.382)</b>`, `<b>5M MOMENTUM</b>`, `<b>BREAK ATH LP</b>`, `<b>ABSORPTION RADAR</b>`, `<b>GAPS RADAR</b>`.
- **Format Token Bersih**:
  - Diawali langsung dengan badge rantai (`🔹` RH / `🔸` SOL). **DILARANG ada bullet point `•`** di depan badge.
  - Nama token adalah link langsung tanpa kurung siku `[]` diikuti pemisah pipe `│` (contoh: `🔹 <a href="...">Token</a> │ V/L X.Xx │ MC $X.XM │ [👑 CTO]`).
  - **DILARANG menampilkan baris Contract Address (CA)** (`<code>{addr}</code>`).
  - **DILARANG memakai emoji `📋` dan `🔗`**.
  - **DILARANG menampilkan baris GMGN terpisah** karena nama token sudah menjadi tautan langsung menuju GMGN.
  - **Top 5 GAPS Radar Kompak**: Wajib disajikan dalam format 1 baris per token (`🔹/🔸 <a href="...">Token</a> │ V/L X.Xx │ MC $X.XM`). **DILARANG menampilkan baris alasan `❌ {alasan}`** dan tanpa jeda baris kosong antar token agar tampilan daftar GAPS sangat padat (compact).
  - Top 5 GAPS Radar wajib disertakan di bagian paling bawah laporan rutin.
  - **Batasan Riwayat Sinyal 3 Terakhir di Chat**: Pada baris jam riwayat token (`🕒 Sinyal:` atau menu `/history`), batasi hanya menampilkan **maksimal 3 sinyal terakhir ke belakang** (contoh: `🕒 Sinyal: 08:15, 08:20, 08:25 WIB (41x)`). Dashboard Web tetap menampilkan seluruh 24 jam penuh.

## 6. Sinkronisasi Waktu Pemindaian (Scan Timing Synchronization)
- **Jadwal Jam Dinding Kelipatan 5 Menit**:
  - `bot_sol_lp.py` (Telegram bot) dan `web.py` (Web dashboard) **WAJIB** mengeksekusi pemindaian pada waktu yang sama persis di setiap kelipatan 5 menit jam dinding (:00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55).
  - Formula perhitungan boundary: `next_boundary = int((time.time() // interval + 1) * interval)` di mana `interval = 300` detik.
  - Hal ini menjamin data di chat Telegram dan data di Web Dashboard selalu 100% konsisten, mutakhir, dan tersinkronisasi tanpa jeda (drift).

## 7. Sinkronisasi Dua Arah Mode Rantai (Two-Way Chain Mode Sync)
- **Penyimpanan Terpusat**:
  - Pengaturan mode rantai (`RH`, `SOL`, `BOTH`) disimpan bersama dalam file `sol-hp-filters.json`.
- **Integrasi Telegram & Web Dashboard**:
  - Menu bot Telegram (`/menu`) atau tombol inline chat berfungsi sebagai pengontrol utama yang langsung mengubah `chain_mode` di `sol-hp-filters.json`.
  - Background daemon di `web.py` mendeteksi perubahan file konfigurasi secara otomatis (maks 3 detik), memperbarui state, dan memicu scan baru.
  - Header Web Dashboard memiliki toggle interaktif (`🔹 RH`, `🔸 SOL`, `🔸🔹 DUAL`) yang tersinkronisasi dua arah dengan Telegram bot. Mengubah mode di web dashboard akan menyimpan ke `sol-hp-filters.json` dan otomatis terbaca oleh bot Telegram pada jadwal scan berikutnya.

## 8. Arsitektur Shared Scan Engine & Caching (Single Source of Truth)
- **Mesin Pemindaian Tunggal**:
  - Fungsi `bot_sol_lp.execute_full_scan(conf)` adalah *Single Source of Truth* untuk semua pemindaian data GMGN Open API dan fallback Meteora.
  - `web.py` memanggil `bot_sol_lp.execute_full_scan()` dan dilarang menduplikasi kode scraping atau kalkulasi filter.
- **Atomic Caching & Anti-429 Rate Limit**:
  - Hasil scan disimpan ke memori dan disk file `sol-hp-cache.json` di bawah kunci `"last_scan"` dengan TTL 50 detik dan `SCAN_LOCK`.
  - Jika bot dan web berjalan berdekatan di jam dinding yang sama, proses kedua langsung menyajikan data dari cache tanpa request HTTP ulang ke GMGN (mengurangi beban API 50% dan mencegah 429).
  - Manual scan (tombol "⚡ Scan" di web atau command `/scan` di Telegram) menggunakan flag `force=True` untuk mengambil data baru seketika.

## 9. 24H Signal History Logging & WIB Timezone Invariant
- **Zona Waktu WIB (UTC+7)**:
  - Seluruh pencatatan waktu, tampilan jam pemindaian (`scanned_at`), dan format pelaporan di Telegram Bot maupun Web Dashboard **WAJIB** dikonversi ke zona waktu **WIB (UTC+7)** (`timezone(timedelta(hours=7))`).
  - Mengatasi inkonsistensi waktu server VPS yang umumnya menggunakan sistem jam UTC.
- **Pencatatan Riwayat Sinyal 24 Jam (`signal-history.json`)**:
  - Setiap pemindaian (interval 5 menit) mencatat token yang lolos strategi aktif: `Runner Momentum`, `5M Momentum`, `Siap LP`, `Akashi Zone`, `Break ATH`, dan `Absorption Radar`.
  - Rolling retention: Data yang berusia lebih dari 24 jam (86.400 detik) otomatis di-prune/dihapus secara berkala.
  - Penyimpanan persisten ganda: file `signal-history.json` dan key `"signal_history"` di `sol-hp-cache.json`.
  - Format agregasi mencakup frekuensi kemunculan (`count`) dan daftar jam kemunculan WIB (contoh: `09:05, 20:30, 23:20 WIB`).
- **Integrasi Telegram Bot & Web Dashboard**:
  - Telegram Bot: Command `/history` (atau `/log`), tombol reply keyboard `📜 History 24h`, dan tombol inline callback. Pada laporan berkala 5 menit, token yang muncul berulang (> 1x) menampilkan baris riwayat yang dibatasi **maksimal 3 sinyal terakhir ke belakang**: `🕒 Sinyal: 08:15, 08:20, 08:25 WIB (41x)` agar chat Telegram tetap ringkas dan padat.
  - Web Dashboard: Tab ke-7 `📜 HISTORY (24H)` (Hotkey `7`) menyajikan tampilan Cards & Table khusus riwayat sinyal 24 jam penuh (tanpa batasan 3 sinyal), serta badge riwayat kemunculan pada kartu/tabel di tab aktif lainnya.

