#!/bin/bash
# Ryan: double-click this to make a clean copy for Sepp on your Desktop.
# It leaves out your Gmail password (.env), your Python setup (.venv), and junk files.
DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="$(basename "$DIR")"
cd "$DIR/.." || exit 1
OUT="$HOME/Desktop/IP Job Board.zip"
rm -f "$OUT"
zip -r -q "$OUT" "$NAME" \
  -x "$NAME/.venv/*" "$NAME/.env" "$NAME/*__pycache__*" "$NAME/.pytest_cache/*" \
     "$NAME/jobs.xlsx" "$NAME/post_draft.txt" "$NAME/Make zip for Sepp.command" "$NAME/.DS_Store"
echo "Made: $OUT"
echo
echo "It includes your current job list (jobs.db). Your Gmail password is NOT included."
echo "Send that zip to Sepp by AirDrop or email."
open -R "$OUT"
read -r -p "Press Return to close."
