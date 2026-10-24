"""search_memory: approved lessons, matching precedents and active policies. Read-only; no vector DB."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def open_work_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=1")
    return db


def search_memory(work: sqlite3.Connection, vendor_id: str | None, tags: list[str], limit: int = 8) -> list[dict]:
    tags_set = set(tags or [])
    out: list[tuple[float, dict]] = []
    for r in work.execute("SELECT * FROM lesson WHERE status='approved'"):
        lt = set(json.loads(r["tags"] or "[]"))
        overlap = len(lt & tags_set)
        general = "general" in lt
        score = 3 * overlap + (1 if general else 0)
        if overlap or general:
            pa = json.loads(r["proposed_action"] or "{}")
            out.append((score + 0.5, {"id": r["id"], "type": "lesson", "rule": r["rule_text"], "tags": sorted(lt),
                                      "check": pa.get("check"), "at": r["at"]}))
    for r in work.execute("SELECT * FROM precedent ORDER BY at DESC LIMIT 400"):
        pt = set(json.loads(r["tags"] or "[]"))
        overlap = len(pt & tags_set)
        vend = vendor_id is not None and r["vendor_id"] == vendor_id
        if overlap or vend:
            out.append((2 * overlap + (2 if vend else 0), {
                "id": r["id"], "type": "precedent", "vendor_id": r["vendor_id"], "tags": sorted(pt),
                "pip_proposed": r["agent_proposal"], "human_decided": r["human_decision"], "reason": r["reason"], "at": r["at"]}))
    for r in work.execute("SELECT id, text, body FROM policy WHERE status='active'"):
        b = json.loads(r["body"])
        keys = set(b.get("when", {}))
        out.append((0.5 + len(keys & tags_set), {"id": r["id"], "type": "policy", "text": r["text"], "then": b["then"]}))
    out.sort(key=lambda x: (-x[0], x[1].get("at") or ""), reverse=False)
    return [d for _, d in out[:limit]]
