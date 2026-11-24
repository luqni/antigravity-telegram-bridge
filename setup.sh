#!/usr/bin/env bash
# ==============================================================================
# Setup script for Antigravity Telegram Bridge
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Initializing Antigravity Telegram Bridge Setup ==="

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 is not installed. Please install Python 3.10+ first."
    exit 1
fi

# Create virtual environment if not present
if [ ! -d "venv" ]; then
    echo "Creating virtual environment in ./venv..."
    python3 -m venv venv
fi

# Activate and install dependencies
echo "Installing dependencies from requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Create .env if missing
if [ ! -f ".env" ]; then
    echo "Creating .env configuration file from template..."
    cp .env.example .env
    echo "[NOTE] Please edit .env with your TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS."
fi

# Verify agy CLI presence
if command -v agy &>/dev/null; then
    echo "[OK] Antigravity CLI (agy) found: $(which agy)"
elif [ -f "$HOME/.local/bin/agy" ]; then
    echo "[OK] Antigravity CLI found at: $HOME/.local/bin/agy"
else
    echo "[WARNING] agy binary not found in standard paths. Ensure Antigravity CLI is installed."
fi

echo "=== Setup completed successfully! ==="
echo "To run the bridge:"
echo "  ./venv/bin/python bot.py"
