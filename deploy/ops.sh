#!/usr/bin/env bash
# Run a VPS job through GitHub Actions and wait for the result. For Claude's cloud
# windows, which can push to GitHub but cannot SSH to the VPS.
#   ./deploy/ops.sh deploy    # push main first! deploys whatever is on origin/main
#   ./deploy/ops.sh status
#   ./deploy/ops.sh backups
set -euo pipefail
job="${1:?usage: deploy/ops.sh deploy|status|backups}"
case "$job" in deploy|status|backups) ;; *) echo "unknown job: $job"; exit 2;; esac
repo="cele-brat/auto-edits"
cd "$(dirname "$0")/.."

git fetch -q origin main
if [ "$job" = deploy ] && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "Warning: local HEAD is not origin/main. The VPS deploys origin/main: $(git log -1 --oneline origin/main)"
fi

# A fresh marker commit on top of origin/main, pushed to ops/<job>, triggers the workflow.
marker=$(git commit-tree "origin/main^{tree}" -p origin/main -m "ops: $job ($(date -u +%FT%TZ))")
git push -q -f origin "$marker:refs/heads/ops/$job"
echo "Triggered ops/$job at ${marker:0:7}; waiting for the result (up to 15 min)..."

for _ in $(seq 1 90); do
  sleep 10
  body=$(gh api "repos/$repo/commits/$marker/comments" --jq '.[-1].body' 2>/dev/null || true)
  if [ -n "$body" ]; then echo "$body"; case "$body" in *": OK"*) exit 0;; *) exit 1;; esac; fi
done
echo "No result after 15 min. Check https://github.com/$repo/actions"; exit 1
