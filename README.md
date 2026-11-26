# Antigravity Telegram Bridge

Jembatan ringan, aman, dan modular untuk menghubungkan Telegram Bot dengan Google Antigravity (`agy`) CLI.

Proyek ini memungkinkan Anda mengoperasikan agen kecerdasan buatan Antigravity langsung melalui ruang obrolan Telegram dari ponsel maupun desktop tanpa perlu membuka terminal atau koneksi SSH.

---

## 🌟 Fitur Utama (v1.1)

- **🛡️ Interaktif Human-in-the-Loop (Inline Approval)**: Menampilkan tombol interaktif `[✅ Setujui]`, `[📋 Plan / Dry-Run]`, dan `[❌ Batalkan]` sebelum perintah dieksekusi ke sistem.
- **⏱️ Fail-Secure / Default-Deny Timeout**: Jika instruksi didiamkan tanpa respons dalam batas waktu (default: 3 menit / 180 detik), sistem secara otomatis membatalkan eksekusi demi keamanan host.
- **🔁 Resumable Execution (Lanjutkan Kapan Saja)**: Saat timeout atau pembatalan terjadi, status checkpoint tersimpan di memori. Pengguna cukup menekan tombol `[🔁 Resume / Lanjutkan]` saat membuka ponsel kembali tanpa perlu mengetik ulang prompt dari awal.
- **🎛️ Saklar Mode Keamanan Fleksibel (`/safety`)**: Beralih instan antara **Mode Ketat (Strict/Guarded)** yang meminta konfirmasi dan **Mode Bebas (Direct)** untuk eksekusi instan tanpa hambatan.
- **📋 Mode Rencana / Dry-Run (`/plan`)**: Meminta agen merumuskan langkah-langkah kerja dan analisis dampak risiko sistem terlebih dahulu tanpa langsung memodifikasi kode atau berkas.
- **🔒 Otorisasi Ketat (Admin Whitelist)**: Hanya akun Telegram terverifikasi (`ALLOWED_USER_IDS`) yang memiliki wewenang mengontrol bridge.
- **💬 Percakapan Bersambung (Multi-Turn Continuity)**: Menjaga konteks percakapan secara dinamis menggunakan engine sesi bawaan Antigravity (`--conversation <id>`).
- **📸 Dukungan Gambar (Vision Support)**: Kirimkan tangkapan layar (*screenshot*) kode, antarmuka, atau log galat untuk dianalisis langsung oleh agen.
- **📂 Navigasi Folder Cepat**: Berpindah direktori proyek dengan mudah menggunakan perintah `/cd <path>` atau kembali dengan `/cd ..`.
- **📊 Pemantauan Host Real-time**: Periksa konsumsi RAM, CPU, kapasitas penyimpanan, dan status agen lewat `/status`.
- **📑 Format Bersih (Smart Chunking)**: Pemotongan pesan otomatis dan lampiran dokumen `.txt` jika keluaran terminal melebihi batas karakter Telegram.

---

## 🏗️ Detail Arsitektur Sistem (v1.1)

Sistem dibangun dengan arsitektur 4-lapisan (*4-tier architecture*) yang diperkaya dengan **Safety & Approval Guard Layer**:

### Diagram Alur Arsitektur

```text
┌────────────────────────────────────────────────────────┐
│         📱 Developer Phone / Desktop Client            │
│                 (Telegram Messenger)                   │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS (Long-Polling)
                            ▼
┌────────────────────────────────────────────────────────┐
│  🤖 LAPISAN 1: GATEWAY & AUTH GUARD (bot.py)           │
│  • Whitelist User Verification (ALLOWED_USER_IDS)      │
│  • User Session Mapping (User ID ➔ Conversation ID)    │
│  • Workspace State Management (/cd, /pwd)              │
│  • Visual Input Handling (Temporary Image Downloader)  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  🛡️ LAPISAN 1.5: SAFETY GUARD & APPROVAL ENGINE        │
│  • Dynamic Safety Switch (/safety: Strict vs Direct)   │
│  • Inline Approval Card ([Approve] [Plan] [Reject])    │
│  • Fail-Secure Default-Deny Timer (180s Auto-Cancel)   │
│  • Resumable Checkpoint Queue ([🔁 Resume Button])     │
└───────────────────────────┬────────────────────────────┘
                            │ Approved / Direct Trigger
                            ▼
┌────────────────────────────────────────────────────────┐
│  ⚡ LAPISAN 2: CLI SUBPROCESS ENGINE (executor.py)     │
│  • Dry-Run Plan Mode Formatter (/plan prefix injection)│
│  • Safe Argument Construction & Shell Sanitization     │
│  • Parameter: -p, --output-format json, --dangerously  │
│  • Multi-Turn Continuity Injection (--conversation)    │
│  • JSON Payload Parsing (Status, Usage, Duration)      │
│  • ANSI Terminal Escape Sequence Stripper              │
│  • Execution Timeout Guard (Kill Process on Hang)      │
└───────────────────────────┬────────────────────────────┘
                            │ Subprocess Fork
                            ▼
┌────────────────────────────────────────────────────────┐
│  🪐 LAPISAN 3: GOOGLE ANTIGRAVITY ENGINE (agy binary)  │
│  • Agentic AI Reasoning, Planning & Vision Analysis    │
│  • Automated Tool Calling (Read, Edit, Run Command)    │
└───────────────────────────┬────────────────────────────┘
                            │ Local OS File I/O
                            ▼
┌────────────────────────────────────────────────────────┐
│  📁 LAPISAN 4: LOCAL WORKSPACE & SYSTEM ENVIRONMENT    │
│  • Project Repositories (~/projects, etc.)             │
│  • Host System Resources (CPU, Memory, Disk)           │
└────────────────────────────────────────────────────────┘
```

---

## 🚀 Panduan Instalasi Cepat

### 1. Persyaratan Sistem
- Python 3.10 atau versi lebih baru
- Antigravity CLI (`agy`) sudah terpasang di sistem
- Token Telegram Bot dari `@BotFather`

### 2. Kloning dan Persiapan Lingkungan

```bash
git clone https://github.com/luqni/antigravity-telegram-bridge.git
cd antigravity-telegram-bridge
chmod +x setup.sh
./setup.sh
```

### 3. Konfigurasi `.env`

Salin berkas contoh konfigurasi:
```bash
cp .env.example .env
```

Sesuaikan nilai variabel di dalam `.env`:
```env
# Token bot dari @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCDefghIJKlmNoPQRstuVWXyz

# User ID Telegram Anda (pisahkan dengan koma jika lebih dari 1 admin)
ALLOWED_USER_IDS=123456789

# Direktori kerja default saat bot pertama kali dijalankan
WORKING_DIR=/home/user/projects

# Batas waktu maksimal eksekusi perintah (detik)
TIMEOUT_SECONDS=300

# Mode keamanan default (strict = butuh konfirmasi, direct = eksekusi instan)
DEFAULT_SAFETY_MODE=strict

# Timeout persetujuan fail-secure (detik)
APPROVAL_TIMEOUT_SECONDS=180
```

### 4. Menjalankan Bot

```bash
./venv/bin/python bot.py
```

---

## 📱 Daftar Perintah Telegram

| Perintah | Deskripsi |
| :--- | :--- |
| `/start` | Menampilkan ringkasan awal, mode keamanan, direktori kerja, dan status bot |
| `/safety [mode]` | Mengubah mode keamanan antara `strict` (Mode Ketat) dan `direct` (Mode Bebas) |
| `/plan <tugas>` | Menjalankan simulasi kerja (Dry-Run Plan) tanpa modifikasi berkas |
| `/resume` | Melanjutkan instruksi yang sebelumnya dibatalkan atau mengalami timeout |
| `/help` | Panduan lengkap tata cara penggunaan seluruh perintah |
| `/newchat` (atau `/reset`) | Mereset konteks percakapan dan memulai sesi baru |
| `/cd <folder>` | Berpindah ke direktori proyek tujuan |
| `/pwd` | Menampilkan path direktori kerja yang sedang aktif |
| `/status` | Menampilkan beban CPU, memori RAM, kapasitas disk, dan status agen |
| `/model <nama>` | Mengganti atau memeriksa model AI yang sedang digunakan |

---

## ⚙️ Menjalankan di Latar Belakang (Systemd Linux)

Agar bot berjalan 24 jam secara otomatis di server atau mesin lokal:

```bash
mkdir -p ~/.config/systemd/user/
cp antigravity-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable antigravity-bridge
systemctl --user start antigravity-bridge
```

Untuk memeriksa status layanan:
```bash
systemctl --user status antigravity-bridge
```

---

## 📄 Lisensi

Didistribusikan di bawah lisensi MIT oleh [Muhammad Luqni Baehaqi](https://github.com/luqni).
