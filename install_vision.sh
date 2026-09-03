#!/bin/bash
# Installation script for UI Mapping vision dependencies

echo "🎯 Installing UI Mapping System Dependencies..."
echo ""

# Install Tesseract OCR
echo "📦 Installing Tesseract OCR..."
sudo apt-get update
sudo apt-get install -y tesseract-ocr

# Install Python packages
echo "🐍 Installing Python packages..."
pip install pytesseract pillow opencv-python numpy

echo ""
echo "✅ Installation complete!"
echo ""
echo "Verify installation:"
python3 -c "import pytesseract, cv2, PIL; print('✅ All libraries installed successfully!')"

echo ""
echo "Test tesseract:"
tesseract --version

echo ""
echo "🚀 Ready to use! Try:"
echo "  python3 ui_mapper_advanced.py analyze Settings"
echo "  python3 ui_mapper_advanced.py deep-map"
echo "  python3 ui_mapper_advanced.py list"
