#!/bin/bash
# Double-click this in Finder to start the party-game server.
# (It opens a Terminal window; close it or press Ctrl+C there to stop the game.)

cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || echo /usr/bin/python3)"

# A previous game window left open (or a crash) can leave the server running and
# holding port 8000. Stop it so a fresh double-click always works.
for PORT in 8000 8443; do
    OLD=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)
    if [ -n "$OLD" ]; then
        echo "Stopping a previous game server on port $PORT…"
        kill $OLD 2>/dev/null
        sleep 1
        STILL=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)
        [ -n "$STILL" ] && kill -9 $STILL 2>/dev/null && sleep 1
    fi
done

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
