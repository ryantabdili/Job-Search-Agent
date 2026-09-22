#!/bin/zsh
# Double-click in Finder to open the IP Job Board in your browser.
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate
PORT=8501
while lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done

echo "Starting the IP Job Board on port $PORT… Leave this window open while you use it. Close it (or press Ctrl+C) to stop."
streamlit run ui.py --server.headless true --server.port "$PORT" --browser.gatherUsageStats false >/tmp/ip-job-board-streamlit.log 2>&1 &
APP=$!
trap 'kill $APP 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 40); do
	if curl -s -o /dev/null "http://localhost:$PORT"; then break; fi
	sleep 0.5
done
if ! curl -s -o /dev/null "http://localhost:$PORT"; then
	echo "The Job Board could not start. Details: /tmp/ip-job-board-streamlit.log"
	cat /tmp/ip-job-board-streamlit.log
	read -r -p "Press Return to close."
	exit 1
fi
open "http://localhost:$PORT"
wait
