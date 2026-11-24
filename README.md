# Antigravity Telegram Bridge

Jembatan ringan, aman, dan modular untuk menghubungkan Telegram Bot dengan Google Antigravity (`agy`) CLI.

Proyek ini memungkinkan Anda mengoperasikan agen kecerdasan buatan Antigravity langsung melalui ruang obrolan Telegram dari ponsel maupun desktop tanpa perlu menyentuh terminal secara langsung.

---

## 🌟 Fitur Utama

- **Otorisasi Ketat (Admin Whitelist)**: Hanya akun Telegram pemilik (User ID terverifikasi) yang dapat memberikan instruksi dan perintah.
- **Percakapan Bersambung (Multi-Turn Continuity)**: Menjaga konteks percakapan secara dinamis menggunakan engine sesi bawaan Antigravity (`--conversation <id>`).
- **Dukungan Gambar (Vision Support)**: Kirimkan tangkapan layar (screenshot) kode, antarmuka, atau log terminal untuk dianalisis langsung oleh agen.
- **Navigasi Folder Cepat**: Berpindah direktori proyek dengan mudah menggunakan perintah `/cd`.
- **Pemantauan Host Real-time**: Periksa konsumsi RAM, CPU, kapasitas penyimpanan, dan status CLI lewat `/status`.
- **Format Bersih (Smart Chunking)**: Pemotongan pesan otomatis dan lampiran dokumen `.txt` jika keluaran terminal melebihi batas karakter Telegram.

---

## 🏗️ Detail Arsitektur Sistem

Sistem ini dibangun dengan pendekatan modular 4-lapisan (*4-tier architecture*) yang memisahkan antara antarmuka pengguna, pemrosesan pesan, eksekusi CLI, dan ruang kerja lokal.

### Diagram Alur Arsitektur

```text
┌────────────────────────────────────────────────────────┐
│         📱 Developer Phone / Desktop Client            │
│                 (Telegram Messenger)                   │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS (Long-Polling / Webhook)
                            ▼
┌────────────────────────────────────────────────────────┐
│  🤖 LAPISAN 1: GATEWAY & AUTH GUARD (bot.py)           │
│  • Whitelist User Verification (ALLOWED_USER_IDS)      │
│  • User Session Mapping (User ID ➔ Conversation ID)    │
│  • Workspace State Management (/cd, /pwd)              │
│  • Visual Input Handling (Temporary Image Downloader)  │
│  • Smart Chunking (> 4000 chars & File Attachment)     │
└───────────────────────────┬────────────────────────────┘
                            │ Python IPC / Popen
                            ▼
┌────────────────────────────────────────────────────────┐
│  ⚡ LAPISAN 2: CLI SUBPROCESS ENGINE (executor.py)     │
│  • Safe Argument Construction & Shell Sanitization     │
│  • Parameter: -p, --output-format json, --dangerously  │
│  • Multi-Turn Continuity Injection (--conversation)    │
│  • JSON Payload Parsing (Status, Usage, Duration)      │
│  • ANSI Terminal Escape Sequence Stripper              │
│  • Timeout Guard (Kill Process on Hang)                │
└───────────────────────────┬────────────────────────────┘
                            │ Subprocess Fork
                            ▼
┌────────────────────────────────────────────────────────┐
│  🪐 LAPISAN 3: GOOGLE ANTIGRAVITY ENGINE (agy binary)  │
│  • Agentic AI Reasoning & Planning                     │
│  • Automated Tool Calling (Read, Edit, Run Command)    │
└───────────────────────────┬────────────────────────────┘
                            │ Read / Write / Shell
                            ▼
┌────────────────────────────────────────────────────────┐
│  📁 LAPISAN 4: LOCAL WORKSPACE & SYSTEM ENVIRONMENT    │
│  • Project Repositories (~/projects, etc.)             │
│  • Host System Resources (CPU, Memory, Disk)           │
└────────────────────────────────────────────────────────┘
```

### Rincian Komponen Arsitektur

1. **Lapisan Gerbang & Keamanan (`bot.py`)**:
   - **Authentication Middleware**: Menggunakan decorator `@require_auth` untuk memvalidasi numeric User ID setiap pesan masuk terhadap daftar `ALLOWED_USER_IDS`. Pesan dari pengguna tidak dikenal langsung ditolak.
   - **Session State Isolation**: Menyimpan pemetaan `user_id -> conversation_id` di memori dengan penguncian thread (*thread lock*), memastikan percakapan setiap admin terisolasi.
   - **Continuous Typing Feedback**: Menjalankan thread terpisah yang mengirim sinyal status *"typing..."* ke Telegram setiap 4 detik agar pengguna mengetahui agen sedang memproses tugas.
   - **Smart Message Formatter**: Membagi respon panjang menjadi beberapa potongan pesan jika panjang teks 4.000–12.000 karakter, atau mengirimkan dokumen `.txt` jika teks melampaui 12.000 karakter agar obrolan tetap rapi.

2. **Lapisan Eksekusi Subprocess (`executor.py`)**:
   - **Non-Interactive Execution**: Memanggil binary `agy` menggunakan mode `-p "<prompt>"` dan `--dangerously-skip-permissions` untuk automasi tanpa hambatan interaktif terminal.
   - **Structured JSON Parsing**: Mengaktifkan `--output-format json` untuk menangkap metadata eksekusi secara terstruktur: waktu durasi (`duration_seconds`), total token pemakaian (`usage`), status eksekusi, serta ID sesi percakapan baru.
   - **Sanitasi Terminal**: Menghapus seluruh karakter ANSI escape (`\x1b[...]`) agar teks keluaran terminal dapat ditampilkan bersih di layar ponsel tanpa karakter aneh.
   - **Timeout Protection**: Mengawasi durasi kerja subprocess dengan batas waktu (`TIMEOUT_SECONDS`). Bila terjadi *deadlock*, proses akan dihentikan secara aman (`proc.kill()`).

3. **Lapisan Konfigurasi & Lingkungan (`config.py`)**:
   - **Auto-Discovery Binary**: Mendeteksi lokasi binary `agy` secara otomatis pada `PATH`, `~/.local/bin/agy`, `/usr/local/bin/agy`, atau path manual yang didefinisikan pada `.env`.
   - **Path Normalization**: Menangani konversi path direktori relatif, absolut, dan ekspansi tilde (`~`) untuk memastikan keamanan eksekusi perintah direktori.

4. **Lapisan Layanan Background (`antigravity-bridge.service`)**:
   - Menyediakan konfigurasi service *Systemd User Unit* sehingga bot dapat berjalan otomatis saat server/laptop menyala (*boot on startup*) dan melakukan *auto-restart* jika terjadi kendala jaringan.

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
```

### 4. Menjalankan Bot

```bash
./venv/bin/python bot.py
```

---

## 📱 Daftar Perintah Telegram

| Perintah | Fungsi |
| :--- | :--- |
| `/start` | Menampilkan ringkasan awal, direktori aktif, dan status bot |
| `/help` | Panduan lengkap tata cara penggunaan perintah |
| `/newchat` (atau `/reset`) | Mereset konteks percakapan dan memulai sesi baru |
| `/cd <folder>` | Berpindah ke direktori proyek tujuan |
| `/pwd` | Menampilkan path direktori kerja yang sedang aktif |
| `/status` | Menampilkan beban CPU, memori RAM, kapasitas disk, dan versi agy |
| `/model <nama>` | Mengganti atau melihat model kecerdasan buatan aktif |

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
