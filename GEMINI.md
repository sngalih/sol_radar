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
  - `insider_rate <= 10%` (Rat trader risk)
  - `is_wash == False` & `is_honeypot == False`
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
  - Semua parameter filter (Min Liq, Min Mcap, Min V/L, Max ER, dsb.) harus dapat disesuaikan lewat web UI dan tersimpan di `rev1-filters.json`.
