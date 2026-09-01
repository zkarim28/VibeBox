#!/bin/bash
# Double-click this in Finder to start the party-game server on macOS.
# (It opens a Terminal window; close it or press Ctrl+C there to stop the game.)
# All the real work lives in start-party-games.sh, shared with Linux.

cd "$(dirname "$0")" || exit 1
exec ./start-party-games.sh
