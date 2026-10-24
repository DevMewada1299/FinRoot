"""Day-zero state: hire Pip at Level 0 and load onboarding policies, all through the gateway."""
from __future__ import annotations

import json

from probation.gateway.core import GATEWAY, PIP, Gateway
from probation.policy import engine
from probation.sim.backtest import history_cases

CONTROLLER = "marisol.vega"


def hire(gw: Gateway) -> None:
    if gw.work.execute("SELECT 1 FROM tier_event WHERE kind='hire'").fetchone():
        return
    gw.transition("agent", PIP, "hire", GATEWAY,
                  patch={"tier": 0, "tier_event": {"from": None, "to": 0, "kind": "hire", "reason": "Pip starts at Level 0"}},
                  reason="Pip hired at Level 0. Suggests only.")


def propose_policy(gw: Gateway, body: dict, source: str, proposer: str = PIP, evidence: list[str] | None = None) -> str:
    engine.validate(body, source)
    bt = engine.backtest(body, history_cases(gw), {"tier_limit_cents": gw.agent_state()["limit_cents"]})
    pid = body["id"]
    gw.transition("policy", pid, "propose", proposer,
                  patch={"text": body.get("text", ""), "body": {k: v for k, v in body.items() if k not in ("id", "source")},
                         "source": source, "evidence": evidence or [], "backtest": bt},
                  reason=f"Proposed {source} policy {pid}: {body.get('text', '')}", evidence=evidence or [])
    return pid


def onboard(gw: Gateway, approve: bool = True) -> None:
    hire(gw)
    for p in engine.ONBOARDING_POLICIES:
        if gw.work.execute("SELECT 1 FROM policy WHERE id=?", (p["id"],)).fetchone():
            continue
        # Pip drafts these from 12 months of history with a read-only SQL job; the controller approves them
        propose_policy(gw, dict(p), "onboarding", proposer=PIP)
        if approve:
            gw.transition("policy", p["id"], "approve", CONTROLLER, patch={"decided_by": CONTROLLER, "decided_at": gw.clock.now()},
                          reason="Approved at onboarding after the replay test")
            if p["then"] == "auto_approve":
                st = gw.agent_state()
                gw.transition("agent", PIP, "score", GATEWAY, patch={"learned_count": (st["learned_count"] or 0) + 1},
                              reason=f"{p['id']} drafted by Pip and approved; counts toward promotion")


def policy_rows(gw: Gateway) -> list[dict]:
    out = []
    for r in gw.work.execute("SELECT * FROM policy ORDER BY id"):
        d = dict(r)
        d["body"] = json.loads(d["body"])
        d["backtest"] = json.loads(d["backtest"] or "null")
        d["evidence"] = json.loads(d["evidence"] or "[]")
        out.append(d)
    return out
