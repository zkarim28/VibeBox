#!/bin/bash
# Double-click this in Finder to start the party-game server.
# (It opens a Terminal window; close it or press Ctrl+C there to stop the game.)

cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || echo /usr/bin/python3)"

echo "Starting the party-game server…"
echo

"$PY" server.py
STATUS=$?

echo
if [ $STATUS -ne 0 ]; then
    echo "The server exited with an error (code $STATUS)."
    echo "Leave this window open and send a screenshot if it keeps happening."
fi
echo "You can close this window now."
