#!/bin/bash
set -e

# Open Calculator application
/home/rajesh/Pictures/desktop/.venv/bin/python /home/rajesh/Pictures/desktop/agent.py "open calculator"

# Wait 2 seconds for it to load
sleep 2

# Take a screenshot
/home/rajesh/Pictures/desktop/.venv/bin/python /home/rajesh/Pictures/desktop/agent.py "take a screenshot"

# Verify screenshot was created
SCREENSHOT_DIR="/home/rajesh/Pictures/desktop/logs/screenshots"
LATEST_SCREENSHOT=$(ls -t "$SCREENSHOT_DIR"/desktop_screenshot_*.png 2>/dev/null | head -n1)

if [ -n "$LATEST_SCREENSHOT" ] && [ -f "$LATEST_SCREENSHOT" ]; then
    echo "PASS: Screenshot created at $LATEST_SCREENSHOT"
else
    echo "FAIL: No screenshot found in $SCREENSHOT_DIR"
fi

# Summary
echo "--- SUMMARY ---"
if [ -n "$LATEST_SCREENSHOT" ] && [ -f "$LATEST_SCREENSHOT" ]; then
    echo "PASS"
else
    echo "FAIL"
fi