# Uptime monitoring

The server answers a public health check, with no secrets and no rate limit:

```
https://edits.cele-brat.solutions/healthz
```

- `200` and `{"ok": true, "version": "...", "db": "ok", "worker_heartbeat_age_s": 42}` when the database answers
  and a worker wrote its heartbeat in the last 10 minutes.
- `503` and `{"ok": false, ...}` when the database fails or the worker has gone quiet (`worker_heartbeat_age_s`
  says for how long; `null` means no worker has ever run on this data).

## Pointing a free monitor at it (5 minutes, once)

**UptimeRobot** (free: 50 monitors, checks every 5 minutes)
1. Sign up at uptimerobot.com → **New monitor**.
2. Monitor type **HTTP(s)**, friendly name `Auto Edits`, URL `https://edits.cele-brat.solutions/healthz`.
3. Interval 5 minutes. Under **Advanced**, keep "Alert when HTTP status is not 2xx" (a `503` from the check is then an alert).
4. Alert contacts: your email, and the UptimeRobot app for a push on the phone.

**Better Stack** (free: 10 monitors, checks every 3 minutes)
1. Sign up at betterstack.com → **Uptime** → **Monitors** → **Create monitor**.
2. URL `https://edits.cele-brat.solutions/healthz`, "Alert us when: URL becomes unavailable".
3. Optional: under **Advanced**, "Response must contain" `"ok": true`.
4. Add your email and phone (the free plan includes calls/SMS for a few alerts a month).

Either one emails you within minutes when the site is down or the worker has stopped. Nothing else is needed:
the check is a plain GET, and `deploy/remote.sh` is not involved.

The staging copy (deploy/STAGING.md) has the same check at `https://staging.edits.cele-brat.solutions/healthz`;
it is not worth a monitor.
