"""Simulated bank. No real money, no real account numbers."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from probation.models import Cents


class SimBank:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(Path(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS transfer(id TEXT PRIMARY KEY, entity_id TEXT,
            account TEXT, amount_cents INTEGER, memo TEXT, status TEXT)""")

    def send_ach(self, entity_id: str, account: str, amount: Cents, memo: str) -> str:
        if amount <= 0:
            raise ValueError("amount must be positive")
        tid = "ach_" + uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO transfer VALUES(?,?,?,?,?,?)",
                        (tid, entity_id, account, amount, memo[:140], "submitted"))
        self.db.commit()
        return tid

    def status(self, transfer_id: str) -> str:
        r = self.db.execute("SELECT status FROM transfer WHERE id=?", (transfer_id,)).fetchone()
        return r[0] if r else "unknown"

    def transfers(self) -> list[dict]:
        cols = ["id", "entity_id", "account", "amount_cents", "memo", "status"]
        return [dict(zip(cols, r)) for r in self.db.execute("SELECT * FROM transfer")]
