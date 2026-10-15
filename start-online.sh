#!/usr/bin/env bash
# Boot-time launcher: starts the party-game server in PUBLIC (online) mode with
# no prompt and no control window, so the Cloudflare link is pushed to the
# NOTIFY_WEBHOOK ntfy topic from .env as soon as the tunnel is up.
#
#   ./start-online.sh                 run it by hand
#   deploy/vibebox-online.service     runs it at boot (see that file)
#
# If the tunnel can't be opened, server.py would quietly fall back to local
# mode. This script refuses that: it exits non-zero so systemd retries until
# the online link actually works.

set -u
cd "$(cd "$(dirname "$0")" && pwd)" || exit 1

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

export MODE=public GUI=0 NO_HTTPS=1
export PORT="${ONLINE_PORT:-8000}"
unset PUBLIC_URL    # always open a fresh Cloudflare quick tunnel

PY="$(command -v python3)" || { echo "python3 not found"; exit 1; }
command -v cloudflared >/dev/null 2>&1 || {
    echo "cloudflared not found on PATH - online mode needs it."; exit 1; }
[ -n "${NOTIFY_WEBHOOK:-}" ] || echo "warning: NOTIFY_WEBHOOK not set in .env - no ntfy push will be sent."

# ---- wait (up to ~3 min) for the internet, so the tunnel doesn't fail at boot
for i in $(seq 1 60); do
    if "$PY" -c 'import socket; socket.create_connection(("api.trycloudflare.com", 443), 5).close()' 2>/dev/null; then
        break
    fi
    [ "$i" -eq 1 ] && echo "Waiting for the network..."
    if [ "$i" -eq 60 ]; then echo "No internet after 3 minutes - giving up."; exit 1; fi
    sleep 3
done

echo "Starting VibeBox in ONLINE mode on port $PORT..."

# Run the server, echoing its output; kill it if it falls back to local.
coproc SRV { exec "$PY" -u server.py --no-gui 2>&1; }
SRV_PID=$SRV_PID
trap 'kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; exit 0' TERM INT

went_local=0
while IFS= read -r line <&"${SRV[0]}"; do
    printf '%s\n' "$line"
    case "$line" in
        *"running LOCAL instead"*)
            echo "Tunnel failed - stopping instead of running local-only."
            went_local=1
            kill "$SRV_PID" 2>/dev/null
            ;;
    esac
done
wait "$SRV_PID"
STATUS=$?
[ "$went_local" -eq 1 ] && exit 1
exit "$STATUS"
