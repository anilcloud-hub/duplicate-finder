@echo off
REM ============================================================
REM  Run this ONCE before first use to install dependencies
REM ============================================================
cd /d "%~dp0"
echo Installing required Python packages...
python -m pip install -r requirements.txt
echo.
echo Done. You can now run 1_Scan.bat
pause
