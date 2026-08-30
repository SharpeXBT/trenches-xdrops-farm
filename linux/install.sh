#!/bin/bash
# TRENCHES XDROPS FARM - Linux install. Run:  bash install.sh
cd "$(dirname "$0")"
echo
echo " Checking Python..."
if command -v python3 >/dev/null 2>&1 && [ "$(python3 -c 'import sys; print(sys.version_info >= (3, 9))')" = "True" ]; then
    echo " Python is ready: $(python3 --version)"
    chmod +x start.sh 2>/dev/null
    echo
    echo " Next steps:"
    echo "   1. Open bot.py (in this folder), scroll to the bottom,"
    echo "      fill in API_KEY / API_SECRET / API_PASSPHRASE."
    echo "   2. Run:  ./start.sh"
    exit 0
fi
echo " Python 3.9+ not found. Installing for your distribution..."
# every branch installs the same thing; only the package manager differs
if   command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3
elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y python3
elif command -v pacman  >/dev/null 2>&1; then sudo pacman -Sy --noconfirm python
elif command -v zypper  >/dev/null 2>&1; then sudo zypper install -y python3
elif command -v apk     >/dev/null 2>&1; then sudo apk add python3
else
    echo " No known package manager found."
    echo " Install Python 3.9+ with your distribution's tools, then run me again."
    exit 1
fi
echo " Done - run me again to verify."
