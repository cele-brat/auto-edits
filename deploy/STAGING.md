# Staging copy of Auto Edits

A second pair of containers (`server-staging`, `worker-staging`) on the same VPS, built from the
**same image** as production, on port **8432**, with its **own data volume**. Nothing it does can
touch production's database, photos or money:

- `AUTOEDITS_ENV=staging` shows a red **STAGING** banner in the top bar.
- Only Razorpay **test** keys (`rzp_test_...`) are accepted; with a live key the server refuses to start.
- No email leaves the machine except to the studio address (`AUTOEDITS_ADMIN_EMAIL`); everyone else's is written to the log.

## 1. One-time setup on the VPS (from your Mac: `ssh root@187.126.117.49`)

```
cd /opt/auto-edits && cp deploy/.env deploy/.env.staging && nano deploy/.env.staging
```

In `deploy/.env.staging` change these lines (leave the SMTP lines as they are):

| Setting | Value |
|---|---|
| `AUTOEDITS_BASE_URL` | `https://staging.edits.cele-brat.solutions` |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | the **test** keys from the Razorpay dashboard (Test mode), or empty |
| `R2_BUCKET` | a separate bucket such as `auto-edits-staging` (make it in Cloudflare first), or leave all four `R2_*` empty to keep staging photos on the VPS disk |
| `AUTOEDITS_KEY` | any new secret of 16+ characters (not production's) |

`.env.staging` is in `.gitignore` and is never committed.

## 2. Start it: ONE line

```
cd /opt/auto-edits && ./deploy/staging.sh up
```

Other commands: `./deploy/staging.sh status`, `logs`, `down`, and `reset` (stops it and wipes the staging data).
To update staging to the code checked out in `/opt/auto-edits` (`git pull` first, or check out a branch),
run `./deploy/staging.sh up` again: it rebuilds the image.

`deploy/remote.sh` is unchanged: staging is started by hand over SSH, never from GitHub.

## 3. Pointing `staging.edits.cele-brat.solutions` at it

Nothing in Traefik is edited. The labels are already in `deploy/docker-compose.staging.yml`:

```
traefik.enable=true
traefik.http.routers.autoedits-staging.rule=Host(`staging.edits.cele-brat.solutions`)
traefik.http.routers.autoedits-staging.entrypoints=websecure
traefik.http.routers.autoedits-staging.tls.certresolver=letsencrypt
traefik.http.services.autoedits-staging.loadbalancer.server.port=8000
```

Two things to do by hand:

1. **DNS (Cloudflare):** add an `A` record `staging.edits` → `187.126.117.49` (same as `edits`), proxy off (grey cloud) until the certificate is issued, then proxy on if you like.
2. **Traefik's network:** Traefik only sees containers on a network it is attached to. If production's
   `server` container is on a shared network named in the main `docker-compose.yml` (look for `external: true`),
   add the same block to `docker-compose.staging.yml` under `networks:` and list it under each service.
   If the live app works with the file as it is (its own `autoedits` network), Traefik is in host/discovery
   mode and the staging file works the same way without changes.

Check: `curl -s https://staging.edits.cele-brat.solutions/healthz` should answer `{"ok": true, ...}`
(it answers `503` with `ok: false` for the first minute, until the staging worker has written its heartbeat).

## 4. Testing payments on staging

Use Razorpay's test cards/UPI (Test mode). Invoices made on staging start with `TEST/` and are not tax
invoices. Sign-up emails to anyone but the studio address are not sent: look at
`./deploy/staging.sh logs` for the `[staging: mail held]` lines and open the confirm link from the
`[mail not set up]` text if you need to, or sign up with the studio address.
