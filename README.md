# Antigravity Telegram Bridge

Jembatan ringan dan aman untuk menghubungkan Telegram Bot dengan Google Antigravity (`agy`) CLI.

Proyek ini memungkinkan Anda mengoperasikan Antigravity langsung melalui ruang obrolan Telegram dari ponsel maupun desktop secara praktis.

---

## 🌟 Fitur Utama

- **Otorisasi Ketat**: Hanya akun Telegram pemilik (User ID terdaftar) yang dapat memberikan perintah.
- **Percakapan Bersambung**: Menjaga konteks multi-turn otomatis dengan engine sesi Antigravity.
- **Dukungan Gambar (Vision)**: Kirimkan tangkapan layar (screenshot) kode atau error terminal untuk dianalisis langsung oleh agen.
- **Navigasi Folder**: Berpindah direktori proyek dengan mudah menggunakan `/cd`.
- **Pemantauan Host**: Periksa penggunaan RAM, CPU, dan status sistem lewat `/status`.
- **Format Bersih**: Pemotongan pesan otomatis agar tidak melampaui batas teks Telegram.

---

## 🚀 Panduan Instalasi Cepat

### 1. Persyaratan Sistem
- Python 3.10 atau versi lebih baru
- Antigravity CLI (`agy`) sudah terpasang
- Token Telegram Bot dari `@BotFather`

### 2. Kloning dan Persiapan Lingkungan

```bash
git clone https://github.com/luqni/antigravity-telegram-bridge.git
cd antigravity-telegram-bridge
chmod +x setup.sh
./setup.sh
```

### 3. Konfigurasi `.env`

Salin berkas contoh dan isi variabel penting:
```bash
cp .env.example .env
```

Isi berkas `.env`:
- `TELEGRAM_BOT_TOKEN`: Token bot Anda dari BotFather
- `ALLOWED_USER_IDS`: Angka User ID Telegram Anda (misal `123456789`)
- `WORKING_DIR`: Folder kerja utama proyek Anda

### 4. Menjalankan Bot

```bash
./venv/bin/python bot.py
```

---

## 📱 Daftar Perintah Telegram

- `/start` : Menampilkan ringkasan awal dan informasi status bot
- `/help` : Panduan lengkap penggunaan perintah
- `/newchat` : Memulai sesi percakapan baru (mereset konteks)
- `/cd <folder>` : Berpindah ke direktori proyek yang dituju
- `/pwd` : Menampilkan lokasi folder kerja saat ini
- `/status` : Menampilkan beban CPU, memori RAM, disk, dan versi CLI
- `/model <nama>` : Menentukan model AI yang ingin digunakan

---

## ⚙️ Menjalankan di Latar Belakang (Systemd)

Untuk Linux berbasis systemd, pasang service pengguna:

```bash
mkdir -p ~/.config/systemd/user/
cp antigravity-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable antigravity-bridge
systemctl --user start antigravity-bridge
```

---

## 📄 Lisensi

Didistribusikan di bawah lisensi MIT oleh Muhammad Luqni Baehaqi.
