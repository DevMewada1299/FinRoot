"""SimClock: logical time stored in work.db so fast-forward can move weeks in seconds."""
from __future__ import annotations

import datetime as dt
import sqlite3

START = "2026-10-24T16:00:00Z"  # 9:00 AM PT on demo day


def _parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SimClock:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.execute("INSERT OR IGNORE INTO sim_clock(id, now) VALUES(1, ?)", (START,))

    def now(self) -> str:
        return self.db.execute("SELECT now FROM sim_clock WHERE id=1").fetchone()[0]

    def today(self) -> str:
        return self.now()[:10]

    def set(self, iso: str) -> None:
        self.db.execute("UPDATE sim_clock SET now=? WHERE id=1", (iso,))

    def advance(self, days: float = 0, minutes: float = 0) -> str:
        n = _fmt(_parse(self.now()) + dt.timedelta(days=days, minutes=minutes))
        self.set(n)
        return n

    @staticmethod
    def add_days(iso: str, days: float) -> str:
        return _fmt(_parse(iso) + dt.timedelta(days=days))
