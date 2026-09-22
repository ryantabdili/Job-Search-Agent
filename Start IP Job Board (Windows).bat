@echo off
rem IP Job Board for Windows. Double-click this file to start.
rem The first run sets everything up (about 1-2 minutes). Later runs start in a few seconds.

setlocal
cd /d "%~dp0"
title IP Job Board
set PYTHONUTF8=1
cls
echo ======================================
echo   IP Job Board
echo ======================================
echo.

rem ---- 1) Find Python 3.10 or newer ----
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto have_python

echo Python needs to be installed first (one time only).
echo.
echo   1. The Python download page will open in your browser.
echo   2. Click "Download Python" and run the installer.
echo   3. IMPORTANT: on the first installer screen, tick the box
echo      "Add python.exe to PATH" before clicking Install Now.
echo   4. When it finishes, double-click "Start IP Job Board (Windows)" again.
echo.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:have_python

rem ---- 2) First-time setup ----
if exist ".venv\Scripts\streamlit.exe" goto run_app
echo First-time setup. This takes a minute or two and only happens once...
echo.
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto setup_failed
echo Setup done.
echo.

:run_app
rem ---- 3) Start the app; it opens in the web browser by itself ----
echo Starting... your browser will open in a few seconds.
echo.
echo   Leave this window open while you use the Job Board.
echo   To quit: close this window.
echo.
".venv\Scripts\python.exe" -m streamlit run ui.py --browser.gatherUsageStats false
exit /b 0

:setup_failed
echo.
echo Setup couldn't finish. Check the internet connection and try again.
echo (On a work laptop, IT may be blocking installs or downloads.)
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
