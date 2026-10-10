#!/usr/bin/env bash
# Update Auto Edits on the VPS: fetch the latest code and restart its own containers only.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
cd deploy
docker compose up -d --build
docker compose ps
