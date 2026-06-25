#!/bin/bash
# ============================================================
#  Build Duplicate Finder for macOS  (run this ON a Mac)
#  Requires: Python 3.10+  (python3 on PATH).
# ============================================================
cd "$(dirname "$0")" || exit 1
echo "Installing dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r dedup/requirements.txt pyinstaller
echo
echo "Building (this takes a few minutes)..."
pyinstaller --noconfirm DuplicateFinder.spec
echo
echo "============================================================"
echo " Done.  App bundle:  dist/Duplicate Finder.app"
echo " Double-click it to run."
echo " To distribute:  compress 'Duplicate Finder.app' to a .zip"
echo "============================================================"
