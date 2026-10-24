"""Fast-forward: the next weeks of inbox, decided by SimPeople, through the real gateway and trust engine.

The inbox stream is generated at runtime (sim/stream.py): mostly clean bills with occasional problems, no labels read.
Pip runs in rules mode for speed (stated in the UI and README). Each week is recorded in sim_week.
"""
from __future__ import annotations

import json
import random

from probation.config import REPO_ROOT
from probation.gateway.audit import tx
from probation.learning.drafts import draft_from_precedents, sim_review_policies
from probation.models import InboxDoc
from probation.pipeline import run_batch
from probation.sim.people import decide_open

PER_WEEK = 45


def _pool(gw) -> list[InboxDoc]:
    seen = {r[0] for r in gw.work.execute("SELECT id FROM inbox_doc")}
    docs = [json.loads(l) for l in open(REPO_ROOT / "data" / "inbox" / "eval.jsonl")]
    random.Random(42).shuffle(docs)
    return [InboxDoc(**{k: d[k] for k in ("id", "received_at", "channel", "from_addr", "subject", "body", "attachments")})
            for d in docs if d["id"] not in seen]


def fast_forward(gw, weeks: int = 4) -> dict:
    gw.work.execute("""CREATE TABLE IF NOT EXISTS sim_week(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, docs INTEGER,
                       auto_paid INTEGER, asked INTEGER, held INTEGER, tier INTEGER, effective_tier INTEGER, policies TEXT)""")
    from probation.sim.stream import week_docs
    out = []
    start = gw.work.execute("SELECT COUNT(*) FROM sim_week").fetchone()[0]
    for w in range(weeks):
        batch = week_docs(gw, PER_WEEK, seed=start + w + 1)
        stats = {"docs": 0, "auto": 0, "asked": 0, "held": 0}
        for day in range(5):
            chunk = batch[day * PER_WEEK // 5:(day + 1) * PER_WEEK // 5]
            gw.clock.advance(days=1)
            docs = [(d.model_copy(update={"received_at": gw.clock.now()}), "sim", None) for d in chunk]
            for it in run_batch(gw, docs, mode="rules"):
                stats["docs"] += 1
                if (it.get("approved_by") or "").startswith("policy:"):
                    stats["auto"] += 1
                elif it["state"] in ("HELD", "BLOCKED"):
                    stats["held"] += 1
                else:
                    stats["asked"] += 1
            decide_open(gw)
        gw.clock.advance(days=2)
        _record_exam(gw)
        drafted = draft_from_precedents(gw)
        reviewed = sim_review_policies(gw)
        st = gw.agent_state()
        with tx(gw.work):
            gw.work.execute("INSERT INTO sim_week(at, docs, auto_paid, asked, held, tier, effective_tier, policies) VALUES(?,?,?,?,?,?,?,?)",
                            (gw.clock.now(), stats["docs"], stats["auto"], stats["asked"], stats["held"], st["tier"],
                             st["effective_tier"], json.dumps({"drafted": drafted, "reviewed": reviewed})))
        out.append({**stats, "at": gw.clock.now(), "tier": st["tier"], "effective_tier": st["effective_tier"],
                    "drafted": drafted, "reviewed": reviewed})
    return {"weeks": out}


def weeks(gw) -> list[dict]:
    try:
        return [dict(r) for r in gw.work.execute("SELECT * FROM sim_week ORDER BY id")]
    except Exception:
        return []


def _record_exam(gw) -> None:
    """The replay exam is the latest `make eval` stage C result. The gateway records it; it is not computed here."""
    p = REPO_ROOT / "eval" / "report.json"
    if not p.exists():
        return
    score = json.loads(p.read_text())["stages"]["C"]["metrics"].get("exam_score")
    if score is not None and score != gw.agent_state()["exam_score"]:
        gw.transition("agent", "pip", "score", "gateway", patch={"exam_score": score},
                      reason=f"Replay exam from the latest make eval: {score:.1%}")
