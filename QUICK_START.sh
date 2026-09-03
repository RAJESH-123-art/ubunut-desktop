#!/bin/bash
# Quick Start Commands for Linux Desktop Automation

echo "============================================"
echo "🚀 Linux Desktop Automation - Quick Start"
echo "============================================"
echo ""

# Activate virtual environment
source .venv/bin/activate

# Use python3 consistently
PY=python3

show_menu() {
    echo ""
    echo "Choose an option:"
    echo ""
    echo "1)  Check environment (X11 validation)"
    echo "2)  List all available tasks"
    echo "3)  Run demo (showcase new features)"
    echo "4)  Take a screenshot"
    echo "5)  Natural language: Open browser and go to GitHub"
    echo "6)  Natural language: Wait 3 seconds"
    echo "7)  View recent screenshots"
    echo "8)  View logs"
    echo "9)  Interactive CLI menu"
    echo "10) Python API example"
    echo "0)  Exit"
    echo ""
    echo -n "Enter choice: "
}

while true; do
    show_menu
    read choice
    
    case $choice in
        1)
            echo ""
            echo "📊 Checking X11 environment..."
            $PY automation.py env
            ;;
        2)
            echo ""
            echo "📋 Available tasks:"
            $PY automation.py list
            ;;
        3)
            echo ""
            echo "🎮 Running feature demo..."
            python3 demo.sh
            echo "(or run: bash demo.sh)"
            ;;
        4)
            echo ""
            echo "📸 Taking screenshot..."
            $PY automation.py run system_screenshot --args '{}'
            ;;
        5)
            echo ""
            echo "🌐 Opening browser and navigating to GitHub..."
            $PY ui/assistant.py "go to github.com"
            ;;
        6)
            echo ""
            echo "⏳ Waiting 3 seconds..."
            $PY automation.py run wait_seconds --args '{"seconds": 3}'
            ;;
        7)
            echo ""
            echo "🖼️  Recent screenshots:"
            ls -lht logs/screenshots/ | head -10
            ;;
        8)
            echo ""
            echo "📜 Recent log entries:"
            tail -20 logs/automation.log
            ;;
        9)
            echo ""
            echo "🎛️  Launching interactive CLI..."
            $PY ui/cli.py --menu
            ;;
        10)
            echo ""
            echo "🐍 Python API Example:"
            echo ""
            cat << 'EOF'
from core.gui_controller import GUIController

# Initialize
gui = GUIController()

# Get windows
windows = gui.get_window_list()
print(f"Found {len(windows)} windows")

# Window operations
gui.maximize_window("Terminal")
gui.move_window("Firefox", 100, 100)
gui.resize_window("Chrome", 1920, 1080)

# Mouse/keyboard
gui.click(500, 300)
gui.type_text("Hello World")
gui.hotkey("ctrl", "c")

# Screenshot
path = gui.screenshot(name="test")
print(f"Screenshot: {path}")
EOF
            echo ""
            echo "Run with: python -c '<paste code>'"
            ;;
        0)
            echo ""
            echo "👋 Goodbye!"
            break
            ;;
        *)
            echo ""
            echo "❌ Invalid choice. Please try again."
            ;;
    esac
    
    echo ""
    echo "Press Enter to continue..."
    read
done
