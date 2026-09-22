#!/bin/bash
# IP Job Board: double-click this file in Finder to start.
# The first run sets everything up (about 1–2 minutes). Later runs start in a few seconds.

cd "$(dirname "$0")" || exit 1
clear
echo "======================================"
echo "  IP Job Board"
echo "======================================"
echo

# 1) Find a Python that's new enough (3.10+)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$(command -v "$c")"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "Python needs to be installed first (one time only)."
  echo
  echo "  1. The Python download page will open in your browser."
  echo "  2. Click the yellow 'Download Python' button and run the installer."
  echo "  3. When it finishes, double-click 'Start IP Job Board' again."
  echo
  open "https://www.python.org/downloads/macos/"
  read -r -p "Press Return to close this window."
  exit 1
fi

# 2) First-time setup: private Python environment + libraries
if [ ! -x ".venv/bin/streamlit" ]; then
  echo "First-time setup. This takes a minute or two and only happens once…"
  echo
  rm -rf .venv
  "$PY" -m venv .venv || { echo "Couldn't create the environment."; read -r -p "Press Return to close."; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  if ! .venv/bin/python -m pip install --quiet -r requirements.txt; then
    echo
    echo "Setup couldn't download what it needs. Check the internet connection and try again."
    echo "(On a firm laptop, IT may be blocking downloads. See README, 'Running it on Sepp's Mac'.)"
    rm -rf .venv
    read -r -p "Press Return to close."
    exit 1
  fi
  echo "Setup done."
  echo
fi

# 3) Start the app on a free port and open it in the browser
PORT=8501
while lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done

echo "Starting… your browser will open in a few seconds."
echo
echo "  Leave this window open while you use the Job Board."
echo "  To quit: close this window (or press Control+C)."
echo
.venv/bin/streamlit run ui.py --server.headless true --server.port "$PORT" \
  --browser.gatherUsageStats false >/dev/null 2>&1 &
APP=$!
trap 'kill $APP 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://localhost:$PORT"; then break; fi
  sleep 0.5
done
open "http://localhost:$PORT"
wait $APP
