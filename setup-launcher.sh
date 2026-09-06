#!/usr/bin/env bash
# Install the VibeBox Launcher as a systemd --user service and print the two
# commands you still need to run with sudo. Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${LAUNCHER_PORT:-8790}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/vibebox-launcher.service"
PY="$(command -v python3)"

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=VibeBox Launcher — phone-friendly public game-server control panel
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$PY $REPO/launcher.py
EnvironmentFile=-$REPO/.env
Environment=LAUNCHER_PORT=$PORT
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now vibebox-launcher.service
sleep 1
systemctl --user --no-pager status vibebox-launcher.service | sed -n '1,10p' || true

echo
echo "──────────────────────────────────────────────────────────────────────"
echo " Launcher is running locally on http://127.0.0.1:$PORT"
echo
echo " Password:"
if grep -q '^LAUNCHER_PASSWORD=' "$REPO/.env" 2>/dev/null; then
  echo "   (from .env — LAUNCHER_PASSWORD)"
else
  echo "   $(cat "$HOME/.vibebox-launcher/password" 2>/dev/null || echo '(will be shown in the logs on first start)')"
  echo "   -> journalctl --user -u vibebox-launcher -o cat | grep -i password"
fi
echo
echo " STILL TO DO — run these yourself (they need sudo / the Tailscale admin):"
echo
echo "   # 1. keep the launcher alive when you're logged out:"
echo "   sudo loginctl enable-linger $USER"
echo
echo "   # 2. let your user drive Tailscale, then put the launcher on the internet:"
echo "   sudo tailscale set --operator=$USER"
echo "   tailscale funnel --bg $PORT"
echo
echo "   If 'tailscale funnel' says Funnel isn't enabled, open the link it prints"
echo "   and toggle Funnel for this machine, then re-run it."
echo
echo " Then from your phone:  https://$(tailscale status --json 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo '<your-machine>.<tailnet>.ts.net')/"
echo "──────────────────────────────────────────────────────────────────────"
