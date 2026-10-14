# Auto Edits: notes for Claude

## Deploying (Claude cloud windows cannot SSH to the VPS; use GitHub)
1. Batch the work. Deploy once per window at a natural stopping point; Bharat keeps a veto.
2. Run the tests, commit, then `git push origin main`.
3. `./deploy/ops.sh deploy` pushes a marker commit to branch `ops/deploy`. The VPS checks GitHub every minute (`deploy/poll.sh`, cron `/etc/cron.d/auto-edits-ops`), sees the new commit and runs `deploy/remote.sh deploy`, which runs `deploy/deploy.sh` (pull main + rebuild). The result is posted as a comment on the marker commit. `ops.sh` waits for it and prints it: commit, container status, backup lines.
4. Other checks: `./deploy/ops.sh status` (commit + containers), `./deploy/ops.sh backups` (backup list in R2).
5. Port 22 is firewalled to Bharat's addresses only; nothing from GitHub or Claude SSHes in. A remote request can only run `deploy/remote.sh` with `deploy`, `status` or `backups`. To change what it may do, edit `deploy/remote.sh`, push, deploy. The VPS posts results with a fine-grained GitHub token (this repo only) in `/root/.autoedits-ops-token`. If results stop arriving, the token may have expired. Poll log: `/var/log/autoedits-ops.log`.
6. If a deploy fails, the full output stays on the VPS in `/tmp/autoedits-deploy.log`. Ask Bharat to SSH in from his Mac. Give him ONE pasteable line to run inside the VPS session.

## Rules
- This repo is PUBLIC. Never commit secrets, and never print app logs, emails or `.env` from the workflow (its output is public).
- Secrets live only in `/opt/auto-edits/deploy/.env` on the VPS. Never paste them in chat.
- Only touch `/opt/auto-edits` and its own containers (`server`, `worker`). Never touch Traefik or other apps on the VPS.

## Where things are
- Live: https://edits.cele-brat.solutions. VPS: Hostinger 187.126.117.49, clone at `/opt/auto-edits`.
- Storage: Cloudflare R2 bucket `auto-edits`. Daily database backups go to `backups/db/`. Restore: `python -m server.restore --list` / `--day YYYY-MM-DD`.
- Full project state and the to-do list are in the claude.ai Project doc `claude/auto-edits-handover.md`.
