"""The export worker: takes photos from the export list, makes the finished pictures, stores them.

Run with:  python -m server.worker
Several can run at once, on this machine or another; AUTOEDITS_EXPORT_WORKERS says how many
this one starts (one per processor core it may use).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback

from . import blobs as blob_store
from .exports import Exports
from .culling import Culls
from .looks import Renderer
from .store import Store


def work(idle_exit: bool = False, data_dir: str | None = None, blobs=None) -> int:
    """Do exports until there is nothing left (idle_exit) or for ever. Returns how many were made."""
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"),
                  blobs if blobs is not None else blob_store.from_environment())
    exports = Exports(store)
    culls = Culls(store)
    renderer = Renderer()
    known = 0
    made = 0
    while True:
        job = culls.claim()
        if job:
            try:
                culls.run(job, renderer)
            except Exception:
                traceback.print_exc()
            continue
        items = exports.claim(2)
        if not items:
            if idle_exit:
                return made
            time.sleep(3)
            continue
        uploaded = store.uploaded_presets()
        if len(uploaded) != known:                               # presets added in the app since we last looked
            for p in uploaded:
                renderer.add_uploaded(p)
            known = len(uploaded)
        for item in items:
            started = time.time()
            try:
                path = store.preview_path(item["wedding"], item["photo"])
                data = renderer.export(path, item["look"], item["size"], item["iso"], item["quality"], item["watermark"])
                exports.save(item, data)
                exports.finish(item["export"], item["photo"], True, len(data), time.time() - started)
                made += 1
            except Exception as e:                               # one bad photo must not stop the rest
                traceback.print_exc()
                exports.finish(item["export"], item["photo"], False, error=f"{type(e).__name__}: {e}")


def main():
    n = max(1, int(os.environ.get("AUTOEDITS_EXPORT_WORKERS", "2")))
    procs = [mp.Process(target=work, daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    while True:                                                  # restart any worker that dies
        time.sleep(5)
        for i, p in enumerate(procs):
            if not p.is_alive():
                procs[i] = mp.Process(target=work, daemon=True)
                procs[i].start()


if __name__ == "__main__":
    main()
