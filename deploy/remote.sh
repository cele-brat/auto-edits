#!/usr/bin/env bash
# Gate for the GitHub deploy key. The VPS runs this for EVERY login with that key
# (authorized_keys "command=" option), so the key can only do the jobs below.
# The word the caller sent arrives in $SSH_ORIGINAL_COMMAND.
# The GitHub repo is PUBLIC: print nothing private here (no app logs, no emails, no .env).
set -uo pipefail
cd /opt/auto-edits/deploy

status() {
  echo "== commit"; git -C /opt/auto-edits log -1 --oneline
  echo "== containers"; docker compose ps --format 'table {{.Service}}\t{{.Status}}'
}

case "${SSH_ORIGINAL_COMMAND:-}" in
  deploy)
    echo "== deploy"
    if ! ../deploy/deploy.sh >/tmp/autoedits-deploy.log 2>&1; then
      echo "DEPLOY FAILED (full output on the VPS in /tmp/autoedits-deploy.log)"
      tail -5 /tmp/autoedits-deploy.log | grep -viE "key|token|secret|password" || true
      exit 1
    fi
    echo "deploy.sh finished"
    sleep 60
    status
    echo "== backup lines from worker"
    docker compose logs worker --tail 80 2>&1 | grep -i "database backup" || echo "(none yet)"
    ;;
  status)
    status
    ;;
  backups)
    docker compose exec -T server python -m server.restore --list
    ;;
  *)
    echo "Not allowed. Use one of: deploy, status, backups"; exit 2
    ;;
esac
