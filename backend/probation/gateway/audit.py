"""Append-only, hash-chained audit log. Fixes are new rows, never edits."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager

GENESIS = "genesis"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(prev_hash: str, at: str, actor: str, action: str, subject: str, allowed: bool, reason: str,
             diff: dict, evidence: list[str]) -> str:
    # Spec: sha256(prev_hash | at | actor | action | subject | allowed | reason | diff). Evidence is included too,
    # so an edited citation is also detected.
    payload = "|".join([prev_hash, at, actor, action, subject, "1" if allowed else "0", reason, canon(diff), canon(evidence)])
    return hashlib.sha256(payload.encode()).hexdigest()


@contextmanager
def tx(db: sqlite3.Connection):
    """One write transaction on work.db. Nested use is not supported on purpose."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def append(db: sqlite3.Connection, at: str, actor: str, action: str, subject: str, allowed: bool, reason: str,
           diff: dict | None = None, evidence: list[str] | None = None) -> int:
    """Must be called inside tx(). Returns the new seq."""
    diff = diff or {}
    evidence = evidence or []
    last = db.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = last[1] if last else GENESIS
    seq = (last[0] + 1) if last else 1
    h = row_hash(prev, at, actor, action, subject, allowed, reason, diff, evidence)
    db.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (seq, at, actor, action, subject, int(allowed), reason, canon(diff), canon(evidence), prev, h))
    return seq


def verify_chain(db: sqlite3.Connection) -> dict:
    prev = GENESIS
    n = 0
    for r in db.execute("SELECT * FROM audit_log ORDER BY seq"):
        n += 1
        expect = row_hash(prev, r["at"], r["actor"], r["action"], r["subject"], bool(r["allowed"]), r["reason"],
                          json.loads(r["diff"]), json.loads(r["evidence"]))
        if r["prev_hash"] != prev or r["hash"] != expect:
            return {"ok": False, "rows": n, "first_bad_seq": r["seq"], "at": r["at"], "subject": r["subject"],
                    "action": r["action"]}
        prev = r["hash"]
    return {"ok": True, "rows": n, "head": prev}


def rows(db: sqlite3.Connection, subject: str | None = None, limit: int = 500) -> list[dict]:
    if subject:
        cur = db.execute("SELECT * FROM audit_log WHERE subject=? ORDER BY seq", (subject,))
    else:
        cur = db.execute("SELECT * FROM (SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?) ORDER BY seq", (limit,))
    out = []
    for r in cur:
        d = dict(r)
        d["allowed"] = bool(d["allowed"])
        d["diff"] = json.loads(d["diff"])
        d["evidence"] = json.loads(d["evidence"])
        out.append(d)
    return out
