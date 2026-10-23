"""The photographer's own dashboard: the "Usage" tab of the Account window (Bharat, Oct 2026).

Per wedding: photos, exports (finished pictures), Lightroom applies, culls, credits spent (from the
ledger's wedding column), storage used, last activity; a totals row; the same as CSV.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends
from fastapi.responses import Response

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def per_wedding(store, user_id: str) -> list[dict]:
    with store._db() as db:
        have = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        exports = "export_items" in have
        culls = "cull_jobs" in have
        rows = db.execute(f"""
            SELECT w.id, w.name, w.created, w.last_used, w.archived, w.photos_kept,
              (SELECT COUNT(*) FROM photos p WHERE p.wedding_id=w.id) AS photos,
              (SELECT COALESCE(SUM(p.bytes),0) FROM photos p WHERE p.wedding_id=w.id) AS upload_bytes,
              (SELECT COUNT(*) FROM photos p WHERE p.wedding_id=w.id AND p.applied<>'') AS applied,
              {"(SELECT COUNT(*) FROM export_items i JOIN exports e ON e.id=i.export_id WHERE e.wedding_id=w.id AND i.status=1)" if exports else "0"} AS exported,
              {"(SELECT COALESCE(SUM(i.bytes),0) FROM export_items i JOIN exports e ON e.id=i.export_id WHERE e.wedding_id=w.id AND i.status=1)" if exports else "0"} AS export_bytes,
              {"(SELECT COUNT(*) FROM cull_jobs j WHERE j.wedding_id=w.id)" if culls else "0"} AS culls,
              (SELECT COALESCE(-SUM(l.delta),0) FROM ledger l WHERE l.wedding=w.id AND l.user_id=w.owner AND l.delta<0) AS spent,
              (SELECT COALESCE(SUM(l.delta),0) FROM ledger l WHERE l.wedding=w.id AND l.user_id=w.owner AND l.kind='refund') AS refunded,
              (SELECT COUNT(*) FROM ledger l WHERE l.wedding=w.id AND l.user_id=w.owner AND l.kind='lightroom') AS lightroom_runs,
              (SELECT COALESCE(SUM(l.photos),0) FROM ledger l WHERE l.wedding=w.id AND l.user_id=w.owner AND l.kind='lightroom') AS lightroom_photos
            FROM weddings w WHERE w.owner=? ORDER BY w.created DESC""", (user_id,)).fetchall()
    out = []
    for r in rows:
        out.append({"id": r["id"], "name": r["name"], "archived": bool(r["archived"]),
                    "photos": int(r["photos_kept"] if r["archived"] else r["photos"]),
                    "exports": int(r["exported"]), "lightroom": int(r["lightroom_photos"]), "culls": int(r["culls"]),
                    "credits": int(r["spent"]) - int(r["refunded"]),
                    "bytes": int(r["upload_bytes"]) + int(r["export_bytes"]),
                    "last_activity": float(r["last_used"] or r["created"]), "created": float(r["created"])})
    return out


def totals(rows: list[dict]) -> dict:
    keys = ("photos", "exports", "lightroom", "culls", "credits", "bytes")
    t = {k: sum(r[k] for r in rows) for k in keys}
    t["weddings"] = len(rows)
    t["last_activity"] = max((r["last_activity"] for r in rows), default=0)
    return t


def router(ctx: dict) -> APIRouter:
    r = APIRouter()
    store, signed_in = ctx["store"], ctx["signed_in"]

    @r.get("/api/usage")
    def usage(user: dict = Depends(signed_in)):
        rows = per_wedding(store, user["id"])
        return {"weddings": rows, "totals": totals(rows)}

    @r.get("/api/usage.csv")
    def usage_csv(user: dict = Depends(signed_in)):
        rows = per_wedding(store, user["id"])
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(["Wedding", "Photos", "Exports", "Lightroom applies", "Culls", "Credits spent", "Storage (MB)",
                      "Last activity (India time)", "Photos removed"])
        for w in rows:
            out.writerow([w["name"], w["photos"], w["exports"], w["lightroom"], w["culls"], w["credits"],
                          round(w["bytes"] / 1048576, 1),
                          dt.datetime.fromtimestamp(w["last_activity"], IST).strftime("%Y-%m-%d %H:%M"), "yes" if w["archived"] else ""])
        t = totals(rows)
        out.writerow(["Total", t["photos"], t["exports"], t["lightroom"], t["culls"], t["credits"], round(t["bytes"] / 1048576, 1), "", ""])
        day = dt.datetime.now(IST).strftime("%Y-%m-%d")
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="auto-edits-usage-{day}.csv"', "Cache-Control": "no-store"})

    return r
