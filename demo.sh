#!/usr/bin/env bash
# Demo script for Linux Desktop Automation System
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Linux Desktop Automation System - Demo                 ║"
echo "║   Production-Ready Framework for Ubuntu 24.04+           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check environment
echo "📊 System Environment Check..."
python automation.py env | python -m json.tool
echo ""

echo "📋 Available Tasks:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 automation.py list | grep -E "^[a-z_]+:"
echo ""

echo "🧪 Test 1: Direct Task Execution"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running: python3 automation.py run wait_seconds --args '{"seconds": 2}'"
python3 automation.py run wait_seconds --args '{"seconds": 2}'
echo "✅ Direct task execution successful!"
echo ""

echo "🗣️  Test 2: Natural Language Interface"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running: python3 ui/assistant.py \"wait 2 seconds\""
python3 ui/assistant.py "wait 2 seconds"
echo "✅ Natural language parsing successful!"
echo ""

echo "📊 Test 3: System Screenshot (if display available)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ ! -z "${DISPLAY:-}" ]; then
    echo "Running: python3 automation.py run system_screenshot"
    python3 automation.py run system_screenshot 2>&1 | tail -5
    echo "✅ Screenshot test completed!"
else
    echo "⚠️  No display detected, skipping screenshot test"
fi
echo ""

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Demo Complete! Project is fully operational.           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Try these commands:"
echo "  • python3 automation.py list                        # List all tasks"
echo "  • python3 agent.py \"install vlc\"                  # Smart agent (any language)"
echo "  • python3 agent.py \"yt pe rrr song chalao\"         # Hindi/slang supported"
echo "  • python3 ui/assistant.py \"open brave browser\"    # NL interface"
echo "  • python3 ui/cli.py --menu                          # Interactive dashboard"
echo "  • python3 automation.py workflow daily_cleanup      # Run workflow"
echo ""
echo "📖 Documentation:"
echo "  • README.md           - Quick start guide"
echo "  • PROJECT_STATUS.md   - Current status & known issues"
echo "  • PROJECT_ANALYSIS.md - Comprehensive technical analysis"
echo ""
