#!/usr/bin/env bash
# Run on the UBUNTU server. Fast-forwards the repo to origin/main.
# No bot restart needed: bot.py re-reads the JSON files whenever they change.
set -euo pipefail

# Derived from this script's own location, so a checkout anywhere works.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

before=$(git rev-parse HEAD)
git fetch --quiet origin main
git reset --hard --quiet origin/main
after=$(git rev-parse HEAD)

if [ "$before" != "$after" ]; then
    echo "$(date -Is)  updated ${before:0:8} -> ${after:0:8}"
    # Uncomment if you ever want a restart too (needs the sudoers rule, see README):
    # sudo systemctl restart aoe2bot
else
    echo "$(date -Is)  already up to date"
fi
