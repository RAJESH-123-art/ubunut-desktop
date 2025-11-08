#!/usr/bin/env python3
"""
Simple test to verify browser automation is working.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.browser import brave
from core.logger import take_screenshot

# Test Brave browser automation
print("Starting Brave browser test...")
b = brave(headless=False)
b.start()
print("Browser started successfully!")

# Go to a simple site
print("Navigating to example.com...")
b.goto("https://example.com", wait_until="load")

# Take a screenshot
screenshot_path = "logs/screenshots/test_browser.png"
print(f"Taking screenshot: {screenshot_path}")
b.screenshot(screenshot_path)

print("Navigating to google.com...")
b.goto("https://www.google.com")

# Get page title
title = b.page.title()
print(f"Page title: {title}")

print("Closing browser...")
b.close()

print(f"Test completed! Check screenshot at: {screenshot_path}")