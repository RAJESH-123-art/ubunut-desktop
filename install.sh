#!/usr/bin/env bash
set -euo pipefail

# Ubuntu 24.04+ setup for Desktop Automation (Wayland/X11)

if [[ "${EUID}" -eq 0 ]]; then
  echo "Do not run as root. The script will use sudo as needed." >&2
  exit 1
fi

sudo apt-get update

# Core system packages (Wayland & X11 compatible)
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  jq curl git \
  tesseract-ocr \
  libopencv-dev \
  notify-osd \
  gnome-screenshot \
  wl-clipboard \
  # Optional X11/XWayland tools (for compatibility/fallbacks)
  xdotool wmctrl scrot

# Optional: Playwright browsers (Chromium/Firefox/WebKit)
# Using pip to manage versions inside venv; playwright will install browsers later.

# Create venv
PY=python3
$PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# Python dependencies
pip install \
  pynput mss \
  opencv-python-headless pytesseract \
  schedule pyyaml loguru click \
  python-telegram-bot==20.* \
  playwright

# Install Playwright browsers
python -m playwright install --with-deps

# .env template
cat > .env <<'EOF'
# Optional Telegram notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Optional OpenAI key for AI layer
OPENAI_API_KEY=
EOF

echo "Install complete. Activate venv with: source .venv/bin/activate"
