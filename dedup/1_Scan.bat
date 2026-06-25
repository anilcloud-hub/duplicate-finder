@echo off
REM ============================================================
REM  Scan a folder for duplicates, then open the review screen
REM ============================================================
cd /d "%~dp0.."
echo.
set /p FOLDER="Enter the folder to scan (or drag it here): "
set FOLDER=%FOLDER:"=%
python -m dedup.main "%FOLDER%"
echo.
pause
