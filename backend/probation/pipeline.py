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


def process(gw: Gateway, ctx: PipContext, wid: str, mode: str = "auto") -> dict:
    it = gw.item(wid)
    doc = gw.doc(it["doc_id"])
    st = gw.agent_state()
    fresh = compute_features(doc, ExtractedInvoice(**it["extracted"]), gw.golden, gw.work, wid, set(st["frozen_vendors"]))
    snapshot = {**it, "facts": fresh}

    def on_step(step: dict) -> None:
        events.publish("agent.step", {"work_item": wid, **step})

    p = propose(ctx, snapshot, doc.model_dump(), st["effective_tier"], st["limit_cents"], mode=mode, on_step=on_step)
    try:
        return gw.submit_proposal(p)
    except Denied as d:
        if d.rule != "evidence":
            raise
        # the gateway refused a proposal that cited something that does not exist; it is logged.
        # Pip retries once in rules mode, citing only the gateway's own evidence ids.
        on_step({"kind": "denied", "text": d.reason})
        p2 = propose(ctx, snapshot, doc.model_dump(), st["effective_tier"], st["limit_cents"], mode="rules")
        return gw.submit_proposal(p2)


def run_batch(gw: Gateway, docs: list[tuple[InboxDoc, str, str | None]], mode: str = "auto") -> list[dict]:
    """Receive and extract every document first (so in-flight bills are visible to each other), then decide each."""
    ctx = PipContext(gw.s)
    wids = [ingest(gw, d, s, sc) for d, s, sc in docs]
    return [process(gw, ctx, w, mode) for w in wids]
