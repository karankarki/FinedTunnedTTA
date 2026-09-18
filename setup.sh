#!/usr/bin/env bash
set -e

echo "=========================================="
echo "  Kokoro Voice Studio - Environment Setup "
echo "=========================================="

# Check for espeak-ng
if ! command -v espeak-ng &> /dev/null; then
    echo "[!] espeak-ng is not installed."
    if command -v brew &> /dev/null; then
        echo "[*] Installing espeak-ng via Homebrew..."
        brew install espeak-ng
    else
        echo "[x] Error: Please install espeak-ng or Homebrew first."
        exit 1
    fi
else
    echo "[✓] espeak-ng is available: $(which espeak-ng)"
fi

# Set up virtual environment
if [ ! -d ".venv" ]; then
    echo "[*] Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "[*] Activating virtual environment..."
source .venv/bin/activate

echo "[*] Upgrading pip and wheel..."
pip install --upgrade pip wheel setuptools

echo "[*] Installing Kokoro Voice Studio dependencies..."
pip install -r requirements.txt

echo "=========================================="
echo "[✓] Setup completed successfully!"
echo "Run './start.sh' to launch the Studio web app."
echo "=========================================="
