@echo off
REM ============================================================
REM  Build Duplicate Finder for Windows  (run this ON Windows)
REM  Requires: Python 3.10+ installed and on PATH.
REM ============================================================
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r dedup\requirements.txt pyinstaller
echo.
echo Building (this takes a few minutes)...
pyinstaller --noconfirm DuplicateFinder.spec
echo.
echo ============================================================
echo  Done.  App folder:  dist\DuplicateFinder\
echo  Run:                dist\DuplicateFinder\DuplicateFinder.exe
echo  To distribute: zip the whole dist\DuplicateFinder folder.
echo ============================================================
pause
