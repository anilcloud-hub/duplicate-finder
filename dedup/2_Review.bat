@echo off
REM ============================================================
REM  Re-open the review screen for an ALREADY scanned folder
REM  (use this if you closed the review window but didn't rescan)
REM ============================================================
cd /d "%~dp0.."
echo.
set /p FOLDER="Enter the SAME folder you scanned (or drag it here): "
set FOLDER=%FOLDER:"=%
python -m dedup.ui.review "%FOLDER%"
pause
