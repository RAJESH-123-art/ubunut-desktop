#!/bin/bash
# Global-hotkey entry point -- this is the "Vy overlay" equivalent.
#
# Bound to a GNOME custom keybinding (see install_desktop_integration.sh).
# Pops a small input box, sends whatever you type straight to agent.py, and
# shows a desktop notification with the result -- so the whole interaction
# is: press hotkey -> type a command -> see it happen -> get notified.
#
# Deliberately a thin shell wrapper, not new Python: agent.py's run_command()
# is already the single, unified pipeline (see MASTER.md/VERCEPT_LEVEL_ROADMAP.md)
# -- this script's only job is desktop plumbing (popup + notification), never
# a second way to parse or execute a command.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# API keys, if configured -- never required, everything stays inert without
# them (see core/llm_planner.py, core/action_loop.py, core/semantic_vision.py).
if [ -f "$PROJECT_DIR/config/secrets.env" ]; then
    set -a  # auto-export every variable sourced below (plain KEY=value file)
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/config/secrets.env"
    set +a
fi

PYTHON="$PROJECT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

COMMAND="$(zenity --entry \
    --title="Desktop Agent" \
    --text="What should I do?" \
    --width=500 2>/dev/null || true)"

if [ -z "$COMMAND" ]; then
    exit 0  # cancelled / empty -- do nothing, no notification spam
fi

notify-send -t 3000 -u normal "Desktop Agent" "Working on: ${COMMAND:0:60}"

if "$PYTHON" agent.py "$COMMAND" > /tmp/agent_prompt_last_run.log 2>&1; then
    notify-send -t 5000 -u normal "Desktop Agent \xe2\x9c\x85" "Done: ${COMMAND:0:60}"
else
    notify-send -t 8000 -u critical "Desktop Agent \xe2\x9d\x8c" "Failed: ${COMMAND:0:60} (see /tmp/agent_prompt_last_run.log)"
fi
