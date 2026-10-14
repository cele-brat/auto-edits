#!/usr/bin/env bash
# Runs on the VPS every minute (cron: /etc/cron.d/auto-edits-ops). Port 22 stays closed to GitHub:
# instead the VPS looks at GitHub for new ops/<job> branch commits and runs that job.
#   ops/deploy  -> deploy.sh (pull main + rebuild) and checks
#   ops/status  -> commit + containers
#   ops/backups -> backup list
# The result is posted as a comment on the ops commit, with the token in /root/.autoedits-ops-token
# (fine-grained, this repo only, Contents read/write). The repo is PUBLIC: post nothing private.
#   deploy/poll.sh --init   remember the current ops commits without running them (first install)
main() {
  set -uo pipefail
  local repo="cele-brat/auto-edits" state=/var/lib/auto-edits-ops
  local token_file=/root/.autoedits-ops-token
  mkdir -p "$state"
  local refs
  refs=$(git ls-remote "https://github.com/$repo" 'refs/heads/ops/*' 2>/dev/null) || return 0
  while read -r sha ref; do
    [ -n "${sha:-}" ] || continue
    local job="${ref#refs/heads/ops/}"
    case "$job" in deploy|status|backups) ;; *) continue;; esac
    [ "$(cat "$state/$job" 2>/dev/null)" = "$sha" ] && continue
    echo "$sha" > "$state/$job"
    if [ "${1:-}" = "--init" ]; then echo "init: ops/$job at ${sha:0:7}"; continue; fi

    echo "$(date -u +%FT%TZ) running ops/$job for ${sha:0:7}"
    local out code=0
    out=$(/opt/auto-edits/deploy/remote.sh "$job" 2>&1) || code=$?
    local head="OK"; [ "$code" = 0 ] || head="FAILED (exit $code)"
    local body
    body=$(printf 'VPS %s: %s\n\n```\n%s\n```\n' "$job" "$head" "$(printf '%s' "$out" | tail -c 50000)")
    if [ -s "$token_file" ]; then
      python3 -c 'import json,sys; print(json.dumps({"body": sys.stdin.read()}))' <<<"$body" |
        curl -sS -o /dev/null -w "comment HTTP %{http_code}\n" -X POST \
          -H "Authorization: Bearer $(cat "$token_file")" -H "Accept: application/vnd.github+json" \
          "https://api.github.com/repos/$repo/commits/$sha/comments" --data-binary @-
    else
      echo "no token file; result not posted"; echo "$body"
    fi
  done <<<"$refs"
}
# Whole script is read before running, so a git pull during a deploy can't change it mid-run.
main "$@"; exit
