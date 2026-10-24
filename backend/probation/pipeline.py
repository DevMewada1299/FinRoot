"""Orchestration: inbox -> extractor -> Pip (read-only) -> gateway (writer).

The pipeline holds both a Gateway and a read-only PipContext, and passes Pip only snapshots. Pip's output is a
Proposal object; whatever it says, the gateway re-derives the facts and applies its own limits.
"""
from __future__ import annotations

from probation import events
from probation.agent.extract import extract
from probation.agent.runner import PipContext, propose
from probation.gateway.core import Gateway
from probation.models import Denied, ExtractedInvoice, InboxDoc
from probation.policy.features import compute as compute_features


def ingest(gw: Gateway, doc: InboxDoc, set_name: str = "live", scenario: str | None = None) -> str:
    wid = gw.receive(doc, set_name, scenario)
    ex = extract(doc, gw.s)
    gw.record_extraction(wid, ex)
    events.publish("agent.step", {"work_item": wid, "kind": "extracted", "text": f"{ex.vendor_name} {ex.bill_no}"})
    return wid


def prepare(gw: Gateway, wid: str) -> dict:
    """Snapshot what Pip may see. Needs the gateway lock; the proposal itself does not."""
    it = gw.item(wid)
    doc = gw.doc(it["doc_id"])
    st = gw.agent_state()
    fresh = compute_features(doc, ExtractedInvoice(**it["extracted"]), gw.golden, gw.work, wid, set(st["frozen_vendors"]))
    return {"item": {**it, "facts": fresh}, "doc": doc.model_dump(), "tier": st["effective_tier"], "limit": st["limit_cents"]}


def think(ctx: PipContext, snap: dict, mode: str = "auto"):
    wid = snap["item"]["id"]

    def on_step(step: dict) -> None:
        events.publish("agent.step", {"work_item": wid, **step})

    return propose(ctx, snap["item"], snap["doc"], snap["tier"], snap["limit"], mode=mode, on_step=on_step)


def finish(gw: Gateway, ctx: PipContext, snap: dict, p) -> dict:
    try:
        return gw.submit_proposal(p)
    except Denied as d:
        if d.rule != "evidence":
            raise
        events.publish("agent.step", {"work_item": p.work_item, "kind": "denied", "text": d.reason})
        p2 = propose(ctx, snap["item"], snap["doc"], snap["tier"], snap["limit"], mode="rules")
        return gw.submit_proposal(p2)


def process(gw: Gateway, ctx: PipContext, wid: str, mode: str = "auto") -> dict:
    snap = prepare(gw, wid)
    return finish(gw, ctx, snap, think(ctx, snap, mode))


def run_batch(gw: Gateway, docs: list[tuple[InboxDoc, str, str | None]], mode: str = "auto") -> list[dict]:
    """Receive and extract every document first (so in-flight bills are visible to each other), then decide each."""
    ctx = PipContext(gw.s)
    wids = [ingest(gw, d, s, sc) for d, s, sc in docs]
    return [process(gw, ctx, w, mode) for w in wids]
