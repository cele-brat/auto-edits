"""Limits that keep one account from crowding out the rest (Bharat, Oct 2026).

  - up to PHOTOS_PER_WEDDING photos in one wedding (sending a photo again does not count twice)
  - up to STORAGE_GB of uploaded previews plus finished export pictures per photographer
  - the studio (admin) account has no limits; the admin can raise them for one photographer
    on /admin (users.limit_photos, users.limit_gb; 0 means the default)
  - up to PRESETS_PER_ACCOUNT uploaded presets

Sharing the worker fairly ("take turns") is in exports.claim and culling.claim.
"""
from __future__ import annotations

PHOTOS_PER_WEDDING = 10_000
STORAGE_GB = 50
PRESETS_PER_ACCOUNT = 1_000
GB = 1024 ** 3


class LimitReached(Exception):
    pass


def extra_gb(store, user_id: str) -> int:
    """Extra storage bought and still running (payments.py: storage_grants)."""
    import time
    with store._db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_grants'").fetchone():
            return 0
        now = time.time()
        return int(db.execute("SELECT COALESCE(SUM(gb),0) FROM storage_grants WHERE user_id=? AND start<=? AND until>?",
                              (user_id, now, now)).fetchone()[0])


def for_user(user: dict, store=None) -> dict:
    """This photographer's limits (None = no limit): the base allowance plus any extra storage bought."""
    if user.get("admin"):
        return {"photos_per_wedding": None, "storage_gb": None, "base_gb": None, "extra_gb": 0}
    base = int(user.get("limit_gb") or 0) or STORAGE_GB
    extra = extra_gb(store, user["id"]) if store is not None else 0
    return {"photos_per_wedding": int(user.get("limit_photos") or 0) or PHOTOS_PER_WEDDING,
            "storage_gb": base + extra, "base_gb": base, "extra_gb": extra}


def check_upload(store, user: dict, wedding_id: str, lr_uuid: str, size: int) -> None:
    """Raise LimitReached with a message the photographer can act on."""
    lim = for_user(user, store)
    if lim["photos_per_wedding"] is None:
        return
    again = store.has_photo(wedding_id, lr_uuid)
    if not again and store.photo_count(wedding_id) >= lim["photos_per_wedding"]:
        raise LimitReached(f"This wedding has reached {lim['photos_per_wedding']:,} photos, the most one wedding can hold. "
                           "Send the rest as a second wedding (for example \"… day 2\").")
    if store.used_bytes(user["id"]) + size > lim["storage_gb"] * GB:
        raise LimitReached(f"Your storage is full ({lim['storage_gb']} GB of uploads and exports). Add 50 GB more for "
                           "Rs 150 a month in the app (Account window), or remove a finished wedding or old exports.")


def usage(store, user: dict) -> dict:
    lim = for_user(user, store)
    return {"used_bytes": store.used_bytes(user["id"]), **lim}
