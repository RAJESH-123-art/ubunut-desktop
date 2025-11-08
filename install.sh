#!/usr/bin/env bash
set -euo pipefail

# Ubuntu 24.04+ setup for Desktop Automation (Wayland & X11)

if [[ "${EUID}" -eq 0 ]]; then
  echo "Do not run as root. The script will use sudo as needed." >&2
  exit 1
fi

sudo apt-get update

# Core system packages
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  xdotool wmctrl jq curl git \
  libx11-dev libxtst6 libxss1 libxcb1 \
  libnss3 libgconf-2-4 libasound2 \
  tesseract-ocr tesseract-ocr-eng \
  scrot grim slurp wl-clipboard \
  libopencv-core-dev libopencv-imgproc-dev libopencv-highgui-dev \
  libgl1-mesa-glx libglib2.0-0 \
  libgtk-3-0 libxkbcommon0 notify-osd

# Optional: Playwright browsers (Chromium/Firefox/WebKit)
# Using pip to manage versions inside venv; playwright will install browsers later.

# Create venv
PY=python3
$PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# Python dependencies
pip install \
  pyautogui pynput mss \
  opencv-python-headless pytesseract \
  schedule pyyaml loguru click \
  python-telegram-bot==20.* \
  playwright

# Install Playwright browsers
python -m playwright install --with-deps

# Wayland notes
# pyautogui may require X11 compatibility layers. On Wayland, prefer xdotool via XWayland-enabled apps
# and use grim/slurp for screenshots.

# Create .env template
cat > .env <<'EOF'
# Optional Telegram notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Optional OpenAI key for AI layer
OPENAI_API_KEY=
EOF

echo "Install complete. Activate venv with: source .venv/bin/activate"
