#!/usr/bin/env bash
# The only jobs a remote request may run on the VPS (called by deploy/poll.sh with the job as $1).
# The GitHub repo is PUBLIC and results are posted there: print nothing private
# (no app logs, no emails, no .env).
main() {
  set -uo pipefail
  cd /opt/auto-edits/deploy

  status() {
    echo "== commit"; git -C /opt/auto-edits log -1 --oneline
    echo "== containers"; docker compose ps --format 'table {{.Service}}\t{{.Status}}'
  }

  case "${1:-}" in
    deploy)
      echo "== deploy"
      if ! /opt/auto-edits/deploy/deploy.sh >/tmp/autoedits-deploy.log 2>&1; then
        echo "DEPLOY FAILED (full output on the VPS in /tmp/autoedits-deploy.log)"
        tail -5 /tmp/autoedits-deploy.log | grep -viE "key|token|secret|password" || true
        return 1
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
      echo "Not allowed. Use one of: deploy, status, backups"; return 2
      ;;
  esac
}
# Whole script is read before running, so the git pull inside deploy can't change it mid-run.
main "$@"; exit
