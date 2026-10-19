"""Short-lived server state kept in SQLite, so a restart forgets nothing that matters:
long looks handed over once ("lk=" keys), recent attempts for rate limits, two-step
login tickets and one-time sign-in links from the plug-in.

Each row has an expiry time; expired rows are ignored and removed now and then. Keys are
kept as hashes, so the database never holds a usable link or ticket.
"""
from __future__ import annotations

import hashlib
import json
import random
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS ephemeral (
    kind TEXT NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL DEFAULT '', until REAL NOT NULL,
    PRIMARY KEY (kind, k)
);
CREATE INDEX IF NOT EXISTS ephemeral_by_until ON ephemeral (until);
"""


def _h(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class State:
    def __init__(self, store):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)

    def _tidy(self, db, now: float) -> None:
        if random.random() < 0.02:                  # now and then, not on every request
            db.execute("DELETE FROM ephemeral WHERE until<?", (now,))

    def put(self, kind: str, key: str, value, seconds: float) -> None:
        now = time.time()
        with self.store._db() as db:
            db.execute("INSERT OR REPLACE INTO ephemeral (kind, k, v, until) VALUES (?,?,?,?)",
                       (kind, _h(key), json.dumps(value), now + seconds))
            self._tidy(db, now)

    def get(self, kind: str, key: str, default=None, touch: float | None = None):
        """The value, or default when missing or expired. touch: keep it this many seconds more."""
        now = time.time()
        with self.store._db() as db:
            r = db.execute("SELECT v FROM ephemeral WHERE kind=? AND k=? AND until>=?", (kind, _h(key), now)).fetchone()
            if r and touch:
                db.execute("UPDATE ephemeral SET until=? WHERE kind=? AND k=?", (now + touch, kind, _h(key)))
        return json.loads(r["v"]) if r else default

    def take(self, kind: str, key: str, default=None):
        """The value, removed at the same time (works once)."""
        now = time.time()
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT v, until FROM ephemeral WHERE kind=? AND k=?", (kind, _h(key))).fetchone()
            if r:
                db.execute("DELETE FROM ephemeral WHERE kind=? AND k=?", (kind, _h(key)))
        return json.loads(r["v"]) if r and r["until"] >= now else default

    def delete(self, kind: str, key: str) -> None:
        with self.store._db() as db:
            db.execute("DELETE FROM ephemeral WHERE kind=? AND k=?", (kind, _h(key)))

    def keep_at_most(self, kind: str, n: int) -> None:
        """Only the n rows of this kind that last longest stay."""
        with self.store._db() as db:
            db.execute("DELETE FROM ephemeral WHERE kind=? AND k NOT IN "
                       "(SELECT k FROM ephemeral WHERE kind=? ORDER BY until DESC LIMIT ?)", (kind, kind, n))

    def hit(self, kind: str, key: str, window: float, add: bool = True) -> int:
        """Attempts for this key within the last window seconds, counted before this one is added."""
        now = time.time()
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT v FROM ephemeral WHERE kind=? AND k=?", (kind, _h(key))).fetchone()
            recent = [t for t in (json.loads(r["v"]) if r else []) if t > now - window]
            if add:
                recent.append(now)
            n = len(recent) - (1 if add else 0)
            if recent:
                db.execute("INSERT OR REPLACE INTO ephemeral (kind, k, v, until) VALUES (?,?,?,?)",
                           (kind, _h(key), json.dumps(recent[-200:]), now + window))
            elif r:
                db.execute("DELETE FROM ephemeral WHERE kind=? AND k=?", (kind, _h(key)))
            self._tidy(db, now)
        return n
