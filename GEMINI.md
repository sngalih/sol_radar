# Robinhood Meme LP Terminal — Project Guidelines & Domain Rules

## 1. Core LP Strategy: Chop Sideways LP, Akashi Zone LP & Spot Runner
- **Fokus Utama**: Strategi utama: **Chop Sideways LP Farming**, **⚡ Akashi Zone LP**, **🚀 Runner Momentum (Spot Entry)**, dan **🎮 Bonus Stage (15M Supertrend Retrace)**.
- **Filosofi Inti**:
  - Jangan mengejar APR tinggi semata; cari meme coin yang **sudah pump → volume tetap tinggi → harga mulai chop/sideways**.
  - Volume ↑, Price → = Kandidat LP ideal.
  - Volume ↑↑, Price ↑↑↑ = Dilarang LP (sedang naik vertikal).
  - Price ↓↓↓ = Dilarang LP (sedang dump/crash bebas).
  - **⚡ Akashi Zone LP**: Retracement Fibonacci antara 0.236 dan 0.382 dari origin/low ke ATH dengan sideways konsolidasi stabil.
  - **🎮 Bonus Stage (15M Supertrend Retrace)**: Strategi spot/momentum wave lanjutan setelah token mencetak New ATH > $250k MC dengan usia < 48 jam dan distribusi pemegang sehat (`top70_sniper ≤ 15%`, `dev_hold ≤ 10%`, `top10 ≤ 35%`, `insider ≤ 12%`, `V/L ≥ 1.0x`). Sinyal terpicu saat Supertrend 15m berstatus **BULLISH** dan harga melakukan retrace menguji dynamic support Supertrend 15m (jarak `0.0% s/d +3.5%` di atas garis ST 15m atau wick test support), tanpa batasan ATH drawdown.
  - **🚀 Runner Momentum (Spot Entry)**: Strategi khusus entry spot momentum multi-bagger dengan 2 tingkatan karakter:
    - **🏛️ Tier 1 (Established Runner / Wave 2)**: MC $1M – $10M, usia ≥ 12h, konsolidasi re-akumulasi kuat, drawdown ATH ≥ -70%, buyer menopang (`Buy% ≥ 50%`, `V/L ≥ 1.0x`), siap meledak di wave 2.
    - **⚡ Tier 2 (Fresh Breakout / Pump.fun)**: MC $50k – $1M, usia < 24h, baru lulus bonding curve pump.fun, volume 5m masif (`Vol 5m ≥ $200k`, `V/L ≥ 2.0x`), candle pump up (`p5 > 0%`, `Buy% ≥ 52%`), anti-rug aman (renounced mint & freeze, dev hold ≤ 10%, insider ≤ 15%, bundler ≤ 55%).
    - **Format Alert Runner**: `🔹/🔸 <a href="...">Token</a> │ V/L X.Xx │ MC $X.XM │ Vol5m $XXXk │ [🏛️ Wave 2 / ⚡ Fresh Pump] (+XX% 5m)`.

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
  - `min_mcap >= $500,000` ($500k USD Market Cap)
  - `min_age_hours >= 12.0` (Usia token minimal 12 jam sejak pembuatan / open trading)
  - Token dengan Market Cap < $500k atau Usia < 12 jam di-drop langsung dari pemindaian LP (Hard Filter Drop).
  - **Pengecualian Khusus Spot Runner Tier 2**: Khusus strategi `🚀 Runner Momentum (Tier 2 Fresh Breakout)`, sistem mengevaluasi koin $50k – $1M & usia < 24h dari feed GMGN 1h dan 5m sebelum filter LP membuang koin muda, sementara seluruh strategi LP tetap terkunci di MC ≥ $500k & Usia ≥ 12h.
- **On-Chain Safety & Anti-Rug Multi-Layer Filters**:
  - `top10_rate <= 45%` (Whale risk: Hard Filter di LP, <= 40% di Runner Tier 1)
  - `dev_team_hold <= 20%` (Dev dump risk: Hard Filter di LP, <= 10% di Runner Tier 1 & Tier 2)
  - `insider_rate <= 15%` (Rat trader/insider risk: Hard Filter di LP & Tier 2, <= 10% di Tier 1)
  - `bundler_rate <= 55%` (Maksimal sniped supply bundle block 0)
  - `renounced_mint == 1` & `renounced_freeze == 1` (Mint & Freeze authority wajib dicabut pada rantai Solana)
  - `holder_count >= 150` (Distribusi pemegang token memadai untuk LP & Tier 1)
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
  - **Susunan Kolom Tabel (Table Mode)**: Urutan kolom wajib: `#` │ `TOKEN` │ `V/L` │ `AKSI` │ `MCAP` │ `LIQ` │ `ER` │ `1H / 5M` │ `BUY %` │ `SAFE` │ `LAST SINYAL`. Kolom `FEE/H` dan CA/VENUE dihapus agar tabel bersih, padat, tombol AKSI (GMGN) berada langsung di kolom ke-4, dan kolom `LAST SINYAL` berada di paling kanan untuk menampilkan riwayat sinyal tanpa memadati kolom TOKEN.

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
  - Judul kategori ditebalkan: `<b>RUNNER</b>`, `<b>🎮 BONUS STAGE (15M Supertrend Retrace)</b>`, `<b>SIAP LP (Chop Sideways)</b>`, `<b>AKASHI ZONE</b>`, `<b>5M MOMENTUM</b>`, `<b>BREAK ATH LP</b>`, `<b>ABSORPTION RADAR</b>`, `<b>GAPS RADAR</b>`.
- **Format Token Bersih**:
  - Diawali langsung dengan badge rantai (`🔹` RH / `🔸` SOL). **DILARANG ada bullet point `•`** di depan badge.
  - Nama token adalah link langsung tanpa kurung siku `[]` diikuti pemisah pipe `│` (contoh: `🔹 <a href="...">Token</a> │ V/L X.Xx │ MC $X.XM`).
  - **DILARANG menampilkan tag narasi di chat Telegram maupun Web Dashboard** seperti `[👑 CTO]`, `[🧠 Smart]`, `[💎 Bluechip]`, atau `[🤖 AI]`. Seluruh tag narasi dihapus agar tampilan chat Telegram maupun antarmuka Web Dashboard tetap padat, bersih, dan fokus pada data esensial.
  - **DILARANG menampilkan baris Contract Address (CA)** (`<code>{addr}</code>`).
  - **DILARANG memakai emoji `📋` dan `🔗`**.
  - **DILARANG menampilkan baris GMGN terpisah** karena nama token sudah menjadi tautan langsung menuju GMGN.
  - **Top 5 GAPS Radar Kompak**: Wajib disajikan dalam format 1 baris per token (`🔹/🔸 <a href="...">Token</a> │ V/L X.Xx │ MC $X.XM`). **DILARANG menampilkan baris alasan `❌ {alasan}`** dan tanpa jeda baris kosong antar token agar tampilan daftar GAPS sangat padat (compact).
  - Top 5 GAPS Radar wajib disertakan di bagian paling bawah laporan rutin.
  - **DILARANG Menampilkan Baris Riwayat Sinyal (`🕒 Sinyal:`) di Pesan Rutin**: Pada laporan pemindaian berkala 5 menit di Telegram, baris riwayat jam sinyal (`🕒 Sinyal: ... (Nx)`) **DILARANG DITAMPILKAN** agar seluruh daftar token murni dan strictly 1 baris per token. Riwayat 24 jam tetap dicatat di backend dan disajikan penuh pada Web Dashboard (Tab 7 `📜 HISTORY`) serta menu on-demand `/history`.

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

## 10. 1H Smart Money & KOL Surge Tracker (GMGN 24H Swaps — Solana Only)
- **Fokus & Karakter**:
  - Melacak token GMGN 24h trending volume teratas khusus jaringan Solana (`SOL`).
  - Menyimpan snapshot berkala per jam di `hourly-smart-tracker.json` (auto-pruning 25 jam) untuk menghitung selisih matematis ($\Delta$) pergerakan Smart Money (`smart_degen_count`) dan KOL (`renowned_count`) antara Jam $T$ dan Jam $T-1$.
- **Kriteria Pemicu Lonjakan Drastis (Surge Triggers)**:
  - **🚀 Dual Inflow Surge**: $\Delta \text{Smart} \ge +5$ wallet DAN $\Delta \text{KOL} \ge +2$ KOL dalam 1 jam (Konviksi tertinggi).
  - **🧠 Pure Smart Inflow**: $\Delta \text{Smart} \ge +10$ wallet baru dalam 1 jam (atau $+30\%$ growth jika base wallet $\ge 15$ dan $\Delta \text{Smart} \ge +6$).
  - **👑 KOL Inflow Spike**: $\Delta \text{KOL} \ge +3$ KOL baru dalam 1 jam.
- **Safety Gate & Anti-Trap Filter**:
  - Rentang Market Cap: $\$100\text{k} \le \text{MCap} \le \$25\text{M}$ (menolak koin zombie atau raksasa jenuh).
  - On-Chain Safety: `dev_team_hold <= 20%`, `top10_rate <= 45%`, `renounced_mint == 1`, `renounced_freeze == 1`, `is_honeypot == False`, `is_wash == False`.
  - Order Flow: `buy_ratio >= 48.0%` dan penurunan 1 jam `p1 >= -10.0%` (menolak falling knife).
- **Mekanisme & Format Notifikasi Telegram**:
  - **Pesan Terpisah di Menit :00**: Dipicu tepat pada jam dinding kelipatan 1 jam (menit `:00` WIB, misal 01:00, 02:00, 03:00 WIB). Jika tidak ada token lolos, bot **tidak mengirim pesan kosong** (silent).
  - **Format Minimalis 1 Baris**:
    ```html
    <b>🧠 1H SMART & KOL SURGE</b>
    <i>🕒 Snapshot HH:MM WIB · GMGN 24h Top Volume</i>

    🔸 <a href="https://gmgn.ai/sol/token/{addr}">{symbol}</a> │ 🧠 Smart +{d_smart} ({smart_total}) │ 👑 KOL +{d_kol} ({kol_total}) │ MC ${mcap} │ Vol ${vol} ({p1:+.0f}% 1h)
    ```
  - **On-Demand & Interactive**: Command `/surge` (atau `/smart`, `/kol`, tombol keyboard `🧠 1H Surge`, inline callback `action_surge`) menyajikan status surge 1 jam terakhir seketika.

