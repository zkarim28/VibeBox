#!/usr/bin/env bash
# Health check for vibebox-online.service, run every 2 minutes by
# vibebox-watchdog.timer. The server and cloudflared can stay "running" while
# the site is dead (e.g. Cloudflare ERROR 1033: the tunnel lost its edge
# connection), so systemd alone never notices. This checks the real pages and,
# after FAILS_NEEDED bad checks in a row, restarts the service — which opens a
# fresh tunnel and pushes the new link to ntfy.

set -u
SVC=vibebox-online.service
PORT="${ONLINE_PORT:-8000}"
FAILS_NEEDED=3         # x 2-minute timer = ~6 min of downtime before a restart
WARMUP=240             # seconds after a (re)start before checking
STATE="${XDG_RUNTIME_DIR:-/tmp}/vibebox-watchdog.fails"

log() { echo "watchdog: $*"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$1"; }

if ! systemctl --user is-active --quiet "$SVC"; then
    if systemctl --user is-enabled --quiet "$SVC"; then
        log "$SVC isn't running — starting it."
        systemctl --user start "$SVC"
    fi
    echo 0 > "$STATE"; exit 0
fi

since=$(systemctl --user show -p ActiveEnterTimestamp --value "$SVC")
age=$(( $(date +%s) - $(date -d "$since" +%s 2>/dev/null || echo 0) ))
[ "$age" -lt "$WARMUP" ] && { echo 0 > "$STATE"; exit 0; }

# The link this run of the service handed out (newest in its log).
inv=$(systemctl --user show -p InvocationID --value "$SVC")
url=$(journalctl --user -u "$SVC" _SYSTEMD_INVOCATION_ID="$inv" -o cat --no-pager \
      | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)

problem=""
local_code=$(code "http://127.0.0.1:$PORT/sudoku")
if [ "$local_code" != 200 ]; then
    problem="local server answered $local_code"
elif [ -z "$url" ]; then
    problem="no tunnel link in the log"
else
    pub_code=$(code "$url/sudoku")
    if [ "$pub_code" != 200 ]; then
        # Don't blame the tunnel when the whole internet is down — a restart
        # can't fix that, and would only burn the current link.
        if ! curl -s -o /dev/null --max-time 10 https://api.trycloudflare.com; then
            log "public link down ($pub_code) but no internet — waiting."
            exit 0
        fi
        problem="public link $url answered $pub_code"
    fi
fi

if [ -z "$problem" ]; then
    echo 0 > "$STATE"; exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
log "$problem (bad check $fails/$FAILS_NEEDED)"
if [ "$fails" -ge "$FAILS_NEEDED" ]; then
    log "restarting $SVC for a fresh server + tunnel (new link goes to ntfy)."
    echo 0 > "$STATE"
    systemctl --user restart "$SVC"
fi
