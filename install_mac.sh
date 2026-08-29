#!/bin/bash
# TRENCHES XDROPS FARM - Mac installation. Run with:  bash install_mac.sh
echo
echo " Checking Python..."
if command -v python3 >/dev/null 2>&1; then
    V=$(python3 -c 'import sys; print(sys.version_info >= (3, 9))')
    if [ "$V" = "True" ]; then
        echo " Python is ready: $(python3 --version)"
        echo
        echo " Next steps:"
        echo "   1. Open bot.py with TextEdit, scroll to the bottom,"
        echo "      fill in API_KEY / API_SECRET / API_PASSPHRASE."
        echo "   2. Run the bot with:   python3 bot.py"
        exit 0
    fi
fi
echo " Python 3.9+ not found."
if command -v brew >/dev/null 2>&1; then
    echo " Installing via Homebrew..."
    brew install python3 && echo " Done - run this script again to verify." && exit 0
fi
echo " Install it manually: https://www.python.org/downloads/  then run this script again."
open "https://www.python.org/downloads/" 2>/dev/null || true
exit 1
