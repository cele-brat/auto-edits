"""The export worker: takes photos from the export list, makes the finished pictures, stores them.

Run with:  python -m server.worker
Several can run at once, on this machine or another; AUTOEDITS_EXPORT_WORKERS says how many
this one starts (one per processor core it may use).

Each loop a worker takes turns: a piece of a cull (culling.CHUNK photos), then a couple of
export photos, then the automatic values of a few newly uploaded photos (kept in the database,
so the Lightroom list never waits for them). Full-size exports (above BIG_SIDE) take a lock
file shared by every worker on this machine, so only one of them is in memory at a time.
"""
from __future__ import annotations

import fcntl
import multiprocessing as mp
import os
import time
import traceback
from contextlib import contextmanager

from . import blobs as blob_store, health
from .backups import Backups
from .exports import Exports
from .culling import Culls
from .looks import TOGGLES, Renderer, auto_parts
from .store import Store

BIG_SIDE = 2560                    # exports larger than this (long side) are made one at a time per machine
AUTOS_PER_TURN = 4


@contextmanager
def big_export_lock(store, size: int, path=None):
    """One full-size export at a time on this machine (a 45 MP one needs over 2 GB)."""
    big = size == 0 or size > BIG_SIDE
    if big and path is not None:
        try:
            from engine.images import jpeg_size
            with open(path, "rb") as f:
                dims = jpeg_size(f.read(1 << 18))
            big = not dims or max(dims) > BIG_SIDE          # a small upload "at full size" is small
        except OSError:
            pass
    if not big:
        yield
        return
    with open(store.root / "big-export.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reload_presets(store, renderer, seen: dict) -> None:
    """Uploaded presets added, changed (a new "added" time) or removed since we last looked."""
    now = store.preset_versions()
    for pid in [p for p in seen if p not in now]:
        renderer.remove_uploaded(pid)
    changed = {pid for pid, v in now.items() if seen.get(pid) != v}
    if changed:
        for p in store.uploaded_presets():
            if p["id"] in changed:
                renderer.add_uploaded(p)
    seen.clear()
    seen.update(now)


def work_out_autos(store, renderer, n: int = AUTOS_PER_TURN) -> int:
    """The automatic values of photos uploaded since, worked out once and kept: those of the photo's
    look now, and those of Auto (every toggle) and of exposure alone, the usual choices."""
    from .looks import clean_look, effective_look
    done = 0
    for r in store.autos_due(n):
        p = store.photo(r["wedding_id"], r["id"])
        try:
            if p is not None:
                path = store.preview_path(r["wedding_id"], r["id"])
                iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
                toggles = clean_look(effective_look(p["settings"], p["folder_settings"]))["toggles"]
                parts = dict.fromkeys(auto_parts(toggles, iso) + auto_parts(TOGGLES, iso) + auto_parts(("exposure",), iso))
                for part in parts:
                    renderer.auto_part(path, part)
        except Exception:
            traceback.print_exc()
        finally:
            store.put_autos(r["id"], {})                     # marked done, even if the photo could not be read
        done += 1
    return done


def work(idle_exit: bool = False, data_dir: str | None = None, blobs=None) -> int:
    """Do exports until there is nothing left (idle_exit) or for ever. Returns how many were made."""
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"),
                  blobs if blobs is not None else blob_store.from_environment())
    exports = Exports(store)
    culls = Culls(store)
    renderer = Renderer()
    renderer.autos_store = store
    seen: dict = {}
    made, beaten = 0, 0.0
    while True:
        if time.time() - beaten > 60:                            # alive (server/health.py), once a minute
            health.beat(store)
            beaten = time.time()
        busy = False
        job = culls.claim()
        if job:
            busy = True
            try:
                culls.run(job, renderer)
            except Exception:
                traceback.print_exc()
        items = exports.claim(2)
        if items:
            busy = True
            reload_presets(store, renderer, seen)
        for item in items:
            started = time.time()
            try:
                path = store.preview_path(item["wedding"], item["photo"])
                with big_export_lock(store, item["size"], path):
                    data = renderer.export(path, item["look"], item["size"], item["iso"], item["quality"], item["watermark"])
                exports.save(item, data)
                exports.finish(item["export"], item["photo"], True, len(data), time.time() - started)
                made += 1
            except Exception as e:                               # one bad photo must not stop the rest
                traceback.print_exc()
                exports.finish(item["export"], item["photo"], False, error=f"{type(e).__name__}: {e}",
                               retry=isinstance(e, (MemoryError, OSError)) and not isinstance(e, FileNotFoundError))
        if work_out_autos(store, renderer):
            busy = True
        if not busy:
            if idle_exit:
                return made
            time.sleep(3)


def main():
    n = max(1, int(os.environ.get("AUTOEDITS_EXPORT_WORKERS", "2")))
    procs = [mp.Process(target=work, daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    store = Store(os.environ.get("AUTOEDITS_DATA", "data/server"), blob_store.from_environment())
    backups = Backups(store)
    from .accounts import Accounts
    from .mail import Mailer
    from .nudges import Nudges
    mailer = Mailer()
    admin = os.environ.get("AUTOEDITS_ADMIN_EMAIL", "")
    nudges = Nudges(Accounts(store, admin), mailer, os.environ.get("AUTOEDITS_BASE_URL", "https://edits.cele-brat.solutions"))
    while True:                                                  # restart any worker that dies
        time.sleep(5)
        backups.run_if_due()                                     # the daily database copy (see server/backups.py)
        nudges.run_if_due()                                      # reminders and the end of free credits (server/nudges.py)
        try:
            health.run_if_due(store, mailer, admin, backups)     # emails the studio only when something is wrong
        except Exception:
            traceback.print_exc()
        for i, p in enumerate(procs):
            if not p.is_alive():
                procs[i] = mp.Process(target=work, daemon=True)
                procs[i].start()


if __name__ == "__main__":
    main()
