#!/bin/bash
# One-time setup: installs a global hotkey overlay + a systemd --user
# service, giving this project the same two-part desktop presence Vercept's
# Vy has (hotkey overlay for ad-hoc commands + always-on background
# scheduled workflows) -- see VERCEPT_LEVEL_ROADMAP.md for the research
# this was based on.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

echo "== 1/2: Global hotkey overlay (Ctrl+Alt+A) =="
python3 - "$PROJECT_DIR/scripts/command_bar.sh" <<'PYEOF'
import ast
import subprocess
import sys

script_path = sys.argv[1]
SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def gset(schema_path, key, value):
    subprocess.run(["gsettings", "set", schema_path, key, value], check=True)


def gget(schema, key):
    out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, check=True)
    return out.stdout.strip()


current = gget(SCHEMA, "custom-keybindings")
try:
    paths = ast.literal_eval(current.replace("@as ", "")) if current.startswith("[") else []
except (ValueError, SyntaxError):
    paths = []

NAME = "Desktop Agent (hotkey overlay)"
BINDING = "<Control><Alt>a"

target = None
for p in paths:
    schema_path = f"{SCHEMA}.custom-keybinding:{p}"
    try:
        if gget(schema_path, "name").strip("'") == NAME:
            target = p
            break
    except subprocess.CalledProcessError:
        continue

if target is None:
    n = 0
    existing = set(paths)
    while f"{BASE}custom{n}/" in existing:
        n += 1
    target = f"{BASE}custom{n}/"
    paths.append(target)
    gset(SCHEMA, "custom-keybindings", "[" + ", ".join(f"'{p}'" for p in paths) + "]")

schema_path = f"{SCHEMA}.custom-keybinding:{target}"
gset(schema_path, "name", NAME)
gset(schema_path, "command", script_path)
gset(schema_path, "binding", BINDING)
print(f"Hotkey bound: {BINDING} -> {script_path}")
PYEOF

echo ""
echo "== 2/2: Background daemon as a systemd --user service =="
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"
sed \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    "$PROJECT_DIR/scripts/desktop-agent.service.template" > "$SERVICE_DIR/desktop-agent.service"

systemctl --user daemon-reload
systemctl --user enable --now desktop-agent.service
echo "Daemon service installed and started."
echo ""
echo "== Done =="
echo "Press Ctrl+Alt+A anywhere to open the command bar (recent-commands dropdown, live status while it runs, and a confirm prompt before sending/deleting/installing/power actions)."
echo "Scheduled triggers in config/triggers.yaml now run 24/7 via systemd -- check with:"
echo "  systemctl --user status desktop-agent.service"
echo "To remove: systemctl --user disable --now desktop-agent.service && rm ~/.config/systemd/user/desktop-agent.service"
