# Auto Edits: handover (copied from the claude.ai Project, 28 Oct 2026)

Read this first in any new Claude Code session. The full history of each feature is in `git log`.

## What it is
1. Web app at https://edits.cele-brat.solutions to cull, edit and export wedding photos on a server.
2. Two routes: standalone (browser upload of camera previews) and the Lightroom Classic plug-in (`plugin/`).
3. Being turned into a SaaS product for other photographers. Studio (admin) account: bharat@cele-brat.com.

## How to deploy (see also CLAUDE.md)
1. Run tests (`python -m pytest -q`), commit, `git push origin main`, then `./deploy/ops.sh deploy`.
2. ops.sh pushes a marker commit to branch `ops/deploy`; the VPS polls GitHub every minute, runs `deploy/remote.sh deploy` and posts the result as a comment. A deploy takes 3–4 minutes. If the image build fails, the old containers keep running.
3. Checks: `./deploy/ops.sh status`, `./deploy/ops.sh backups`, public `https://edits.cele-brat.solutions/healthz` (200 fine, 503 database/worker trouble).
4. If a deploy fails, the output is in `/tmp/autoedits-deploy.log` on the VPS. Bharat SSHes in from his Mac or the Hostinger browser terminal; give him ONE line to paste.
5. Repo is PUBLIC: never commit secrets; remote.sh never prints app logs, emails or .env.

## Testing the UI inside a Claude session
1. Install requirements + `python3 -m playwright install chromium`.
2. Run `uvicorn server.main:app --factory --port 8431` with AUTOEDITS_DATA=<scratch dir> and AUTOEDITS_KEY=<any 20+ char key>.
3. Add photos: POST `/api/weddings` + `/api/weddings/{id}/photos` (header X-Auto-Edits-Key; meta JSON needs uuid, folder, fileName, taken, iso, develop).
4. Run the worker once in-process: `from server.worker import work; work(idle_exit=True, data_dir=...)`.
5. Sign the browser in via POST `/api/login-link` → `/enter/<token>`, then screenshot with Playwright. Browser POSTs with the cookie need header `X-Requested-With`.
6. Careful: `pkill -f "uvicorn server.main"` inside a Bash call kills that shell too; run it in its own call.
7. Parallel building: one agent per batch in its own git worktree, new endpoints in new `server/<feature>.py` routers + new `static/<feature>.js`, one include/script line in main.py / index.html; merge one by one, keep both sides of import/script-tag conflicts. Never `open(path,"w").write(f(path))` (truncates first).

## Where things are
1. Code: `cele-brat/auto-edits`, branch `main`. Last live commit known: a36017b (26 Oct). 241+ tests.
2. VPS: Hostinger, clone at `/opt/auto-edits`. Containers `server` (FastAPI, 8431 behind Traefik) and `worker` (exports, culls, auto values, hourly nudges/retention, daily backup + health check).
3. Storage: Cloudflare R2 bucket `auto-edits`; DB backups daily in `backups/db/` (30 days).
4. Secrets only in `/opt/auto-edits/deploy/.env` and `/root/.autoedits-ops-token` on the VPS.
5. Review of 19 Oct: claude.ai Project doc `claude/review-2026-10-19.md` (items 1–29 fixed 19 Oct; features 30–39 built 23 Oct).
6. Schema is migrated on start (guarded ALTERs in `server/store.py`). `data/params.json` overrides DEFAULT_PARAMS in `engine/render.py`.

## Latest work (26 Oct)
1. 3109024 Auto matched across a folder: `engine/match.py`, `server/match.py`. Per folder + light scene, auto value = median + 0.5 × (own − median, clamped: 0.6 EV, 25 tone, 18 temp, 10 tint). Groups < 3 fall back to folder. Tune `BANDS`, `PULL`, `MIN_GROUP`.
2. 3109024 Recommended presets: `server/recommend.py`, `static/recommend.js`; `GET /api/weddings/{w}/folders/{f}/recommended` → top 3, scored by folder event words, light, warmth/brightness/colour; learned "My style <light>" scores highest.
3. c8ba7c8 Auto exposure led by faces; older photos re-measured; Folders list taller.
4. a36017b Safari before view (no separate before layer: flicker / washed yellow), photo area not selectable, Cmd+A ignored in Develop.
5. Not yet checked: real wedding folders for match bands; Lightroom route with matched values.

## Walk-through guide (29 Oct, live 559fd73; placement beside each part in a follow-up PR)
1. `server/guide.py`, `static/guide.js`: a 13-step card with "Show more" (next step) and "Close". Each step's card moves beside the part it explains (add photos, folders, cull, preset import, presets, Develop, Apply to, sliders, crop/masks, export, share, credits), with an arrow and an outline on that part; docked bottom right when the part is not on screen and on phones. Opens every time the app opens until Close; Close is saved per account (table `guide_closed`), so it never opens again on any device. Admin "view as" never sees or closes it.
2. Close hides it for that login only (ephemeral state `guide-session`, keyed by the session cookie); Close with "Don't show again" ticked hides it for good (table `guide_closed`).
3. Edit the steps in `GUIDE_STEPS` (guide.js). To show it to someone again after "Don't show again": `DELETE FROM guide_closed WHERE user_id=…`.

## Waiting on Bharat
1. Try Auto on a real event folder (30+ photos, mixed light); say if even. Under-corrected → raise PULL/bands; drift → lower.
2. Check Recommended picks on Haldi / Sangeet / Reception folders.
3. Razorpay webhooks: tick `subscription.charged`, `subscription.cancelled`, `subscription.halted`, `subscription.completed`, `payment.failed`; enable Subscriptions.
4. Uptime monitor on `/healthz` (deploy/UPTIME.md).
5. On the VPS once: `cd /opt/auto-edits/deploy && docker compose exec server python -m server.mailcheck`, add the TXT records (DMARC likely missing).
6. Staging when wanted: DNS for `staging.edits.cele-brat.solutions`, `deploy/.env.staging` with TEST keys, `./deploy/staging.sh up` (deploy/STAGING.md).
7. Real Lightroom: plug-in v0.9 settings window, Bring back and apply twice (white balance must not change), stars/labels.
8. Real weddings: Learn my style, Retouch, Compare view (C), galleries on a phone, History/Undo, zoom 1:1.
9. Open: protect `main`; R2 object versioning; CA to confirm SAC 998383 + invoice format; make repo private before launch (poll.sh first); reboot VPS at a quiet time; renew GitHub token ~Oct 2027.

## Not built yet
1. PDF invoices (wkhtmltopdf apt install failed 23 Oct; HTML attached instead). Retry: add `RUN apt-get update && apt-get install -y --no-install-recommends wkhtmltopdf fontconfig && rm -rf /var/lib/apt/lists/*` before `USER autoedits`.
2. Capacity: bigger machines per job, spending cap.
3. Lightroom route "not yet": retouch, drawn masks, straighten+crop, rotate, app masks of made presets.
4. Gallery downloads are proof size with mark.
5. Admin page does not label archived weddings.
6. Compare view loads 1600 px per pane (may be slow).
7. Transactional email service when volume grows.

## Known gaps
1. 45 MP export: 12.7 s, 2.3 GB peak (one at a time).
2. Cull ratings / burst grouping not checked on real photos.
3. Home page still noindex; hero photo consent to confirm.
4. Free trial per IP: mobile networks share addresses.
5. Backups cover the database only; photos single copy in R2.
6. Screens tested in headless Chrome; Safari partly fixed 26 Oct; Firefox untested.

## Feature map (what is live, by date; details in git log)
1. 10 Oct: accounts, sign-up/confirm/reset, Lightroom key per photographer, separation per account, credits, preview mark, /admin, home page + SEO.
2. 14 Oct: Razorpay payments LIVE (GST: Rajasthan CGST+SGST else IGST; invoices AE/2026-27/…, TEST/… for test mode), credit ledger (unchangeable rows), admin two-step login (`server/security.py`; reset: `docker compose exec server python -m server.security --reset-two-step`), limits (`server/limits.py`), policy pages (`server/legal.py`), daily backups (`server/restore.py`), free trial 15 days + reminders (`server/nudges.py`), crop + AI crop (`engine/crop.py`, `static/crop.js`).
3. 16–17 Oct: Lightroom-style screen (Library/Develop, panels, filmstrip), drawn masks (`engine/shapes.py`), rotate, per-photo editing, even slider strengths, precision drag, faster previews, create preset, add photos/folders, dehaze/sharpening.
4. 19 Oct: review fixes (money, Lightroom white balance, stability, restart-safe state in SQLite `ephemeral`, security headers `server/webguard.py`, plug-in download, export confirm), presets renamed to original Cele-Brat names (UUIDs unchanged).
5. 23 Oct: billing phase 2 (auto-renew storage, admin refunds + credit notes, emailed invoices, season pack, prices on /admin), bulk preset import, zoom/copy-paste/sync/history/snapshots/stars/labels, plug-in v0.9, learn my style, retouch, compare view, favourites, client galleries, retention 180 days, DPDP delete/change email, /healthz, mailcheck, staging, two-tab versions, Usage tab.
6. 26 Oct: see "Latest work".

## Pricing (Bharat's decisions)
1. 1 credit = ₹3, launch ₹1 until 31 Dec 2026; min 500, steps of 100; GST 18% on top.
2. Export / Lightroom apply 1 credit per photo; culling 1 per 10 photos.
3. Storage 50 GB for ₹150/month (+GST). Season pack 5,000 credits ₹4,000 + GST until 31 Dec 2026; 10,000 for ₹27,500 after. Editable on /admin › Prices.

## How Bharat likes to work
1. Replies in numbered points (1, 1a, 1b), no long paragraphs.
2. Decisions asked one at a time with options.
3. Batch deploys at natural stopping points; he keeps a veto.
4. When he must run something on the VPS, give ONE pasteable line.
