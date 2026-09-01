#!/usr/bin/env bash
# Cross-platform launcher for the party-game server (Linux + macOS).
#
#   ./start-party-games.sh                 start the server (+ control window)
#   ./start-party-games.sh --no-gui        start without the control window
#   ./start-party-games.sh install-shortcut   add a desktop/menu entry (Linux)
#
# On macOS you can also just double-click "Start Party Games.command".

set -u
cd "$(cd "$(dirname "$0")" && pwd)" || exit 1

# ---- locate a usable Python 3 -------------------------------------------------
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
            PY="$cand"; break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.8+ is required but wasn't found."
    case "$(uname -s)" in
        Linux)  echo "  Fedora:  sudo dnf install python3"
                echo "  Debian:  sudo apt install python3" ;;
        Darwin) echo "  Install from https://python.org or:  brew install python" ;;
    esac
    read -r -p "Press Enter to close." _ || true
    exit 1
fi

# ---- install-shortcut: Linux .desktop entry --------------------------------
if [ "${1:-}" = "install-shortcut" ]; then
    if [ "$(uname -s)" != "Linux" ]; then
        echo "On macOS the shortcut is the \"Start Party Games.command\" file - "
        echo "double-click it in Finder (right-click > Open the first time)."
        exit 0
    fi
    HERE="$(pwd)"
    APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$APPS"
    DEST="$APPS/party-games.desktop"
    cat > "$DEST" <<EOF
[Desktop Entry]
Type=Application
Name=Party Games
Comment=Start the local party-game server
Exec=$HERE/start-party-games.sh
Path=$HERE
Icon=applications-games
Terminal=true
Categories=Game;
EOF
    chmod +x "$DEST"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$APPS" >/dev/null 2>&1
    echo "Added \"Party Games\" to your application menu."
    echo "  ($DEST)"
    exit 0
fi

# ---- free the ports a stale/previous instance may still hold ----------------
free_port() {
    local port="$1" pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    elif command -v fuser >/dev/null 2>&1; then
        pids="$(fuser "$port"/tcp 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)"
    elif command -v ss >/dev/null 2>&1; then
        pids="$(ss -lptnH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
    fi
    [ -z "$pids" ] && return 0
    echo "Stopping a previous game server on port $port..."
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    for p in $pids; do kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null || true; done
    sleep 1
}
free_port 8000
free_port 8443

# ---- optional-tools note ---------------------------------------------------
command -v cloudflared >/dev/null 2>&1 || \
    echo "note: 'cloudflared' not found - Public (internet) mode will be unavailable."
"$PY" -c 'import tkinter' 2>/dev/null || {
    case "$(uname -s)" in
        Linux)  echo "note: Tkinter missing - no control window. Install it with:"
                if command -v dnf >/dev/null 2>&1; then echo "        sudo dnf install python3-tkinter"
                elif command -v apt >/dev/null 2>&1; then echo "        sudo apt install python3-tk"
                elif command -v pacman >/dev/null 2>&1; then echo "        sudo pacman -S tk"
                else echo "        (your distro's python3 tkinter package)"; fi ;;
        Darwin) echo "note: Tkinter missing - no control window. Try: brew install python-tk" ;;
    esac
}

echo "Starting the party-game server..."
echo
"$PY" server.py "$@"
STATUS=$?

echo
if [ "$STATUS" -ne 0 ] && [ "$STATUS" -ne 130 ]; then
    echo "The server exited with an error (code $STATUS)."
    echo "Leave this window open and send a screenshot if it keeps happening."
fi
echo "You can close this window now."
# keep the window up when it was double-clicked (stdin not a terminal prompt)
[ -t 0 ] || read -r -p "" _ || true
