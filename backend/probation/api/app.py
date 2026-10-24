"""HTTP API. The frontend sends decisions only; every write goes through the gateway."""
from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from probation import events
from probation.agent.runner import PipContext
from probation.api import views
from probation.config import REPO_ROOT, load_settings
from probation.gateway import audit, limits
from probation.gateway.core import PIP, Gateway
from probation.models import Denied, InboxDoc, usd

app = FastAPI(title="Probation")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

_lock = threading.RLock()
_state: dict = {"gw": None, "tamper": None}
CONTROLLER = "marisol.vega"


def gw() -> Gateway:
    if _state["gw"] is None:
        _state["gw"] = Gateway(load_settings())
    return _state["gw"]


def _denied(d: Denied):
    raise HTTPException(status_code=403, detail={"rule": d.rule, "reason": d.reason})


# ---------------------------------------------------------------- state
@app.get("/state")
def state():
    with _lock:
        g = gw()
        st = g.agent_state()
        counts = {r[0]: r[1] for r in g.work.execute("SELECT state, COUNT(*) FROM work_item GROUP BY state")}
        return {"agent": st, "now": g.clock.now(), "tiers": [{"n": t, "name": limits.TIER_NAME[t], "limit_cents": c}
                                                             for t, c in limits.TIER_LIMIT_CENTS.items()],
                "hard_limits": limits.HARD_LIMITS, "approval_matrix": limits.APPROVAL_MATRIX, "counts": counts,
                "llm_enabled": g.s.llm_enabled, "people": {k: {"name": a.name, "role": a.role} for k, a in limits.PEOPLE.items()},
                "frozen": [{"id": v, "name": (g.golden.execute("SELECT name FROM vendor WHERE id=?", (v,)).fetchone() or [v])[0]}
                           for v in st["frozen_vendors"]]}


@app.get("/agent/timeline")
def timeline():
    with _lock:
        return [dict(r) for r in gw().work.execute("SELECT * FROM tier_event ORDER BY id")]


# ---------------------------------------------------------------- work items
@app.get("/work-items")
def work_items(state: str | None = None, limit: int = 200):
    with _lock:
        g = gw()
        q = "SELECT id FROM work_item" + (" WHERE state=?" if state else "") + " ORDER BY updated_at DESC, id DESC LIMIT ?"
        ids = [r[0] for r in g.work.execute(q, ((state, limit) if state else (limit,)))]
        return [views.summarize(g.item(i)) for i in ids]


@app.get("/work-items/{wid}/evidence")
def item_evidence(wid: str):
    with _lock:
        try:
            return views.evidence(gw(), gw().item(wid))
        except KeyError:
            raise HTTPException(404, "no such work item")


@app.get("/records/{rid}")
def record(rid: str):
    with _lock:
        r = views.record(gw(), rid)
        if r is None:
            raise HTTPException(404, "no such record")
        return r


class Decision(BaseModel):
    decision: str
    actor: str = CONTROLLER
    reason: str = ""
    new_bank: dict | None = None


@app.post("/work-items/{wid}/decision")
def decide(wid: str, d: Decision):
    with _lock:
        g = gw()
        it = g.item(wid)
        nb = d.new_bank
        if d.decision == "verify" and nb is None and it["extracted"]:
            ex = it["extracted"]
            nb = {"bank_name": ex.get("remit_bank_name", ""), "routing_last4": ex.get("remit_routing_last4", ""),
                  "account_last4": ex.get("remit_account_last4", "")} if ex.get("remit_account_last4") else None
        try:
            out = g.decide(wid, d.decision, d.actor, d.reason, nb)
        except Denied as e:
            _denied(e)
        return views.summarize(out)


# ---------------------------------------------------------------- inbox
class Incoming(BaseModel):
    body: str = Field(min_length=10, max_length=40000)
    from_addr: str = ""
    subject: str = ""
    channel: str = "paste"


def _process(wid: str) -> None:
    """Snapshot under the lock, let Pip think without it (LLM calls can take a while), submit under the lock."""
    from probation.pipeline import finish, prepare, think
    try:
        with _lock:
            snap = prepare(gw(), wid)
            ctx = PipContext(gw().s)
        p = think(ctx, snap)
        with _lock:
            finish(gw(), ctx, snap, p)
    except Denied as d:
        events.publish("agent.step", {"work_item": wid, "kind": "denied", "text": d.reason})
    except Exception as e:  # keep the server up for the demo; the error is shown in the feed
        events.publish("agent.step", {"work_item": wid, "kind": "error", "text": str(e)[:300]})
    events.publish("work_item.updated", {"work_item": wid})


@app.post("/inbox/receive")
def receive(m: Incoming, bg: BackgroundTasks):
    from probation.pipeline import ingest
    with _lock:
        g = gw()
        doc = InboxDoc(id=g.new_doc_id(), received_at=g.clock.now(), channel="paste" if m.channel == "paste" else "email",
                       from_addr=m.from_addr, subject=m.subject or "Pasted invoice", body=m.body)
        wid = ingest(g, doc, "live")
    bg.add_task(_process, wid)
    return {"work_item": wid, "doc_id": doc.id}


@app.get("/inbox")
def inbox():
    with _lock:
        g = gw()
        out = []
        for r in g.work.execute("""SELECT d.id, d.received_at, d.channel, d.from_addr, d.subject, d.body, d.attachments, w.id AS wid, w.state
                                   FROM inbox_doc d LEFT JOIN work_item w ON w.doc_id=d.id ORDER BY d.received_at DESC, d.id DESC"""):
            d = dict(r)
            d["attachments"] = json.loads(d["attachments"] or "[]")
            out.append(d)
        return out


# ---------------------------------------------------------------- policies, incidents, lessons
@app.get("/policies")
def policies():
    from probation.sim.setup import policy_rows
    with _lock:
        return policy_rows(gw())


@app.post("/policies/{pid}/decision")
def policy_decision(pid: str, d: Decision):
    with _lock:
        try:
            gw().transition("policy", pid, d.decision, d.actor, patch={"decided_by": d.actor, "decided_at": gw().clock.now()},
                            reason=d.reason or f"Policy {d.decision}d")
        except Denied as e:
            _denied(e)
        return {"ok": True}


@app.post("/policies/{pid}/backtest")
def policy_backtest(pid: str):
    from probation.policy import engine
    from probation.sim.backtest import history_cases
    with _lock:
        g = gw()
        r = g.work.execute("SELECT body FROM policy WHERE id=?", (pid,)).fetchone()
        if r is None:
            raise HTTPException(404)
        body = dict(json.loads(r[0]), id=pid)
        return engine.backtest(body, history_cases(g), {"tier_limit_cents": g.agent_state()["limit_cents"]})


class Report(BaseModel):
    work_items: list[str]
    actor: str = CONTROLLER
    pattern: str | None = None
    summary: str = ""
    severity: str = "critical"


@app.post("/incidents")
def report_incident(r: Report):
    from probation.learning.lessons import propose_lesson, report_split_incident
    with _lock:
        g = gw()
        try:
            if r.pattern == "split_to_dodge_limit" or r.pattern is None and len(r.work_items) > 1:
                iid = report_split_incident(g, r.work_items, r.actor)
            else:
                iid = g.open_incident(r.work_items[0], "human_override", "human", r.severity,
                                      {"work_items": r.work_items, "summary": r.summary or "Reported by the controller",
                                       "tags": []}, r.actor)
            lid = propose_lesson(g, iid)
        except Denied as e:
            _denied(e)
        events.publish("lesson.proposed", {"lesson": lid, "incident": iid})
        return {"incident": iid, "lesson": lid}


@app.get("/incidents")
def incidents():
    with _lock:
        out = []
        for r in gw().work.execute("SELECT * FROM incident ORDER BY id DESC"):
            d = dict(r)
            d["details"] = json.loads(d["details"] or "{}")
            out.append(d)
        return out


@app.get("/lessons")
def lessons():
    from probation.learning.lessons import lessons as L
    with _lock:
        return L(gw())


@app.post("/lessons/{lid}/decision")
def lesson_decision(lid: str, d: Decision):
    from probation.learning.lessons import approve_lesson, reject_lesson
    with _lock:
        try:
            if d.decision == "approve":
                return {"applied": approve_lesson(gw(), lid, d.actor, d.reason)}
            reject_lesson(gw(), lid, d.actor, d.reason)
            return {"applied": None}
        except Denied as e:
            _denied(e)


@app.get("/skills")
def skills_list():
    from probation.agent import skills
    with _lock:
        g = gw()
        idx = skills.index(g.s.skills_dir)
        hist = [dict(r) for r in g.work.execute("SELECT skill, version, source_lesson, approved_by, at FROM skill_version ORDER BY skill, version")]
        return {"skills": idx, "versions": hist}


# ---------------------------------------------------------------- audit and ask
@app.get("/audit")
def audit_log(limit: int = 300, subject: str | None = None):
    with _lock:
        return audit.rows(gw().work, subject, limit)


@app.post("/audit/verify")
def audit_verify():
    with _lock:
        return audit.verify_chain(gw().work)


@app.post("/audit/tamper")
def audit_tamper():
    """Demo only: edit an old row in place (as an attacker with DB access would) or put it back."""
    with _lock:
        db = gw().work
        t = _state["tamper"]
        if t is None:
            r = db.execute("SELECT seq, reason FROM audit_log WHERE action='POST' AND allowed=1 ORDER BY seq LIMIT 1").fetchone()
            if r is None:
                raise HTTPException(400, "no posted payment to edit yet")
            edited = re.sub(r"\$([\d,]+)\.(\d\d)", lambda m: "$" + m.group(1)[:-1] + "0." + m.group(2), r["reason"], count=1)
            db.execute("UPDATE audit_log SET reason=? WHERE seq=?", (edited, r["seq"]))
            _state["tamper"] = {"seq": r["seq"], "orig": r["reason"]}
            return {"tampered": True, "seq": r["seq"], "before": r["reason"], "after": edited}
        db.execute("UPDATE audit_log SET reason=? WHERE seq=?", (t["orig"], t["seq"]))
        _state["tamper"] = None
        return {"tampered": False, "seq": t["seq"]}


class Ask(BaseModel):
    q: str = Field(min_length=2, max_length=500)


@app.post("/ask")
def ask(a: Ask):
    from probation.api.ask import answer
    with _lock:
        return answer(gw(), a.q)


# ---------------------------------------------------------------- simulation
@app.post("/sim/reset")
def sim_reset():
    from probation.sim import demo
    import gc
    with _lock:
        if _state["gw"] is not None:
            _state["gw"].close()
        _state["gw"] = None
        _state["tamper"] = None
        gc.collect()  # drop Pip's read-only connections before the files are replaced
        out = demo.reset(verbose=False)
        events.publish("sim.reset", out)
        return out


@app.post("/sim/play/{scenario}")
def sim_play(scenario: str, bg: BackgroundTasks):
    from probation.pipeline import ingest
    from probation.sim import demo
    if scenario not in ("planted_email", "split_repeat", "split_first", "price_mismatch", "near_duplicate", "new_vendor"):
        raise HTTPException(404)
    with _lock:
        g = gw()
        docs = demo.demo_docs(scenario)
        done = {r[0] for r in g.work.execute("SELECT id FROM inbox_doc")}
        docs = [d for d in docs if d.id not in done]
        if not docs:
            raise HTTPException(409, "already played; reset to run it again")
        wids = []
        for d in docs:
            d = d.model_copy(update={"received_at": g.clock.now()})
            wids.append(ingest(g, d, "demo", scenario))

    def run_all():
        for w in wids:
            _process(w)
    bg.add_task(run_all)
    return {"work_items": wids}


class FF(BaseModel):
    weeks: int = Field(default=4, ge=1, le=26)


@app.post("/sim/fast-forward")
def sim_fast_forward(f: FF):
    from probation.sim.fastforward import fast_forward
    with _lock:
        return fast_forward(gw(), f.weeks)


@app.get("/sim/weeks")
def sim_weeks():
    from probation.sim.fastforward import weeks
    with _lock:
        return weeks(gw())


@app.get("/eval/latest")
def eval_latest():
    p = REPO_ROOT / "eval" / "report.json"
    if not p.exists():
        raise HTTPException(404, "run make eval")
    d = json.loads(p.read_text())
    d.pop("rows_C", None)
    return d


# ---------------------------------------------------------------- SSE
@app.get("/events")
async def sse():
    q = events.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    msg = await asyncio.to_thread(q.get, True, 15)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


_ = (usd, PIP)
