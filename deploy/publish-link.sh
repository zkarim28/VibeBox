#!/usr/bin/env bash
# Publishes the current VibeBox tunnel URL to the portfolio site, so
# https://zkarim28.github.io/vibebox/ always forwards to the live server.
#
#   deploy/publish-link.sh https://foo-bar.trycloudflare.com
#
# server.py runs this automatically when the tunnel comes up, if .env has
# PUBLISH_LINK=1. It works in its own clone of the site repo (never your
# working copy) and only ever writes vibebox/link.json. Every attempt starts
# from a fresh `reset --hard origin/main`, so merge conflicts can't happen:
# if a push is rejected because the remote moved, it just refetches and retries.

set -u
URL="${1:?usage: publish-link.sh <url>}"
REPO="${PUBLISH_LINK_REPO:-git@github.com:zkarim28/zkarim28.github.io.git}"
BRANCH="${PUBLISH_LINK_BRANCH:-main}"
DIR="${PUBLISH_LINK_DIR:-$HOME/.local/share/vibebox/site-bot}"
FILE=vibebox/link.json
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15"

log() { echo "publish-link: $*"; }

if [ ! -d "$DIR/.git" ]; then
    mkdir -p "$(dirname "$DIR")"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DIR" \
        || { log "clone failed"; exit 1; }
fi
cd "$DIR" || exit 1
git config user.name  "VibeBox bot"
git config user.email "vibebox-bot@users.noreply.github.com"

for attempt in 1 2 3 4 5 6; do
    if git fetch --quiet --depth 1 origin "$BRANCH" \
        && git reset --quiet --hard "origin/$BRANCH"; then
        # same URL already published -> nothing to do
        if grep -qF "\"url\": \"$URL\"" "$FILE" 2>/dev/null; then
            log "already pointing at $URL"; exit 0
        fi
        mkdir -p vibebox
        printf '{"url": "%s", "updated": "%s"}\n' \
            "$URL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$FILE"
        git add "$FILE"
        git commit --quiet -m "vibebox: link -> $URL"
        if git push --quiet origin "HEAD:$BRANCH"; then
            log "published $URL"; exit 0
        fi
    fi
    log "attempt $attempt failed, retrying"
    sleep $((attempt * 10))
done
log "giving up"
exit 1
