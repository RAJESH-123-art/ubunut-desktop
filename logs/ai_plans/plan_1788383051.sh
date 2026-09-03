#!/bin/bash
set -e

REPORT="/tmp/package_test.txt"

# Clear or create report
> "$REPORT"

echo "=== Installed Packages (first 5) ===" >> "$REPORT"
# List installed packages, skip header, take first 5 entries
apt list --installed 2>/dev/null | tail -n +2 | head -5 >> "$REPORT"

echo "" >> "$REPORT"
echo "=== Available Updates ===" >> "$REPORT"
# Check for upgradable packages based on current cache
UPGRADABLE=$(apt list --upgradable 2>/dev/null | tail -n +2)
if [ -z "$UPGRADABLE" ]; then
    echo "No updates available." >> "$REPORT"
else
    echo "$UPGRADABLE" >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "=== Verification ===" >> "$REPORT"
if [ -s "$REPORT" ]; then
    echo "PASS: Report generated at $REPORT" >> "$REPORT"
else
    echo "FAIL: Report is empty" >> "$REPORT"
fi

# Output summary to stdout
echo "=== SUMMARY ==="
if [ -s "$REPORT" ]; then
    echo "PASS: Report created successfully at $REPORT"
else
    echo "FAIL: Report creation failed"
fi