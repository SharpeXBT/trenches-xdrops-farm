#!/bin/bash
# TRENCHES XDROPS FARM - Mac install. Double-click me (right-click > Open the first time).
cd "$(dirname "$0")"
echo
echo " Checking Python..."
if command -v python3 >/dev/null 2>&1 && [ "$(python3 -c 'import sys; print(sys.version_info >= (3, 9))')" = "True" ]; then
    echo " Python is ready: $(python3 --version)"
    echo
    echo " Next steps:"
    echo "   1. Open bot.py (in this folder), scroll to the bottom,"
    echo "      fill in API_KEY / API_SECRET / API_PASSPHRASE."
    echo "   2. Double-click start.command (this folder)"
    exit 0
fi
echo " Python 3.9+ not found."
if command -v brew >/dev/null 2>&1; then
    echo " Installing via Homebrew..."
    brew install python3 && echo " Done - run me again to verify." && exit 0
fi
echo " Install it from https://www.python.org/downloads/ then run me again."
open "https://www.python.org/downloads/" 2>/dev/null || true
exit 1
