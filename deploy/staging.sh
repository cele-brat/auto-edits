#!/usr/bin/env bash
# The staging copy of Auto Edits on the VPS (see deploy/STAGING.md).
#   ./deploy/staging.sh up        start (or update) server-staging + worker-staging on port 8432
#   ./deploy/staging.sh down      stop them (the staging data volume stays)
#   ./deploy/staging.sh status    containers and the last log lines
#   ./deploy/staging.sh logs      follow the logs
#   ./deploy/staging.sh reset     stop them AND remove the staging data volume (a clean slate)
# Settings: deploy/.env.staging (never committed). Production's deploy/.env is never read.
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE=.env.staging
COMPOSE=(docker compose -f docker-compose.staging.yml --env-file "$ENV_FILE")

need_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "No deploy/$ENV_FILE yet. Make one from deploy/.env (see deploy/STAGING.md), with TEST Razorpay keys only." >&2
    exit 1
  fi
  if grep -Eq '^RAZORPAY_KEY_ID=rzp_live_' "$ENV_FILE"; then
    echo "deploy/$ENV_FILE holds a LIVE Razorpay key. Staging only runs with rzp_test_ keys. Refusing." >&2
    exit 1
  fi
}

case "${1:-up}" in
  up)
    need_env
    # the same image as production (compose names production's build auto-edits-server); rebuilt from
    # the code checked out here, so staging shows what the next deploy will
    "${COMPOSE[@]}" up -d --build
    "${COMPOSE[@]}" ps
    echo "Staging is on 127.0.0.1:${STAGING_PORT:-8432} and, through Traefik, https://staging.edits.cele-brat.solutions"
    ;;
  down)    "${COMPOSE[@]}" down ;;
  status)  "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail 20 ;;
  logs)    "${COMPOSE[@]}" logs -f ;;
  reset)   "${COMPOSE[@]}" down -v ;;
  *) echo "usage: $0 up|down|status|logs|reset" >&2; exit 2 ;;
esac
