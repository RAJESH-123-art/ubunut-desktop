#!/bin/bash
# Launches the GTK command-bar popup (ui/command_bar.py) -- the upgraded,
# persistent replacement for the old zenity --entry popup in
# scripts/agent_prompt.sh. Bind THIS to a hotkey (see
# install_desktop_integration.sh) for the richer UI: a recent-commands
# dropdown, live streaming status while a command runs, and a Yes/No
# confirmation dialog before consequential actions (send/delete/install/
# power/raw-shell).
#
# No need to `source config/secrets.env` here -- ui/command_bar.py imports
# agent.py, whose module-level _load_secrets_env() already loads it.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="$PROJECT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

exec "$PYTHON" ui/command_bar.py
