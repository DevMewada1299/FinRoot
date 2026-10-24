"""Corrections -> drafted policies. After three similar human approvals, Pip drafts a narrow policy from them.
It is replay-tested and needs a human; it may never touch hard limits (bank, sender, duplicates, injection)."""
from __future__ import annotations

import json
import math

from probation.gateway.core import PIP
from probation.policy.engine import ONBOARDING_POLICIES
from probation.sim.setup import propose_policy

BASE = ONBOARDING_POLICIES[0]["when"]


def draft_from_precedents(gw) -> list[str]:
    groups: dict[str, list[tuple[str, float]]] = {}
    for r in gw.work.execute("""SELECT p.id, p.work_item, p.tags FROM precedent p
                                WHERE p.agent_proposal='ask' AND p.human_decision='approve'"""):
        tags = set(json.loads(r["tags"] or "[]"))
        if tags != {"price_over_po"}:
            continue
        it = gw.item(r["work_item"])
        f = it["facts"] or {}
        grp = f.get("vendor_group")
        if grp:
            groups.setdefault(grp, []).append((r["id"], float(f.get("price_delta_pct") or 0)))
    out = []
    existing = {str(json.loads(r[0]).get("when", {}).get("vendor_group")) for r in gw.work.execute("SELECT body FROM policy")}
    for grp, precs in groups.items():
        if len(precs) < 3 or grp in existing:
            continue
        cap = min(10, math.ceil(max(d for _, d in precs)))
        n = gw.work.execute("SELECT COUNT(*) FROM policy").fetchone()[0]
        pid = f"pol-{n + 30:03d}"
        when = dict(BASE, price_delta_pct=f"<={cap}", vendor_group=grp)
        body = {"id": pid, "text": f"{grp}: price rises up to {cap}% over the PO from known vendors, everything else matching: "
                                   f"pay and note the change.", "when": when, "then": "auto_approve", "max_amount_cents": 500_000}
        propose_policy(gw, body, "correction", proposer=PIP, evidence=[p for p, _ in precs[:6]])
        out.append(pid)
    return out


def sim_review_policies(gw, actor: str = "marisol.vega") -> list[dict]:
    """The simulated controller's rubric: approve only if the replay test wrongly approved nothing bad."""
    out = []
    for r in gw.work.execute("SELECT id, backtest, source FROM policy WHERE status='proposed'").fetchall():
        bt = json.loads(r["backtest"] or "{}")
        ok = bt.get("approved_bad", 0) == 0 and bt.get("wrongly_held", 0) <= max(2, bt.get("n", 0) // 200)
        gw.transition("policy", r["id"], "approve" if ok else "reject", actor,
                      patch={"decided_by": actor, "decided_at": gw.clock.now()},
                      reason=f"[sim] replay test {'passed' if ok else 'failed'}: {bt.get('approved_good', 0)} good approved, "
                             f"{bt.get('approved_bad', 0)} bad approved")
        if ok:
            st = gw.agent_state()
            gw.transition("agent", PIP, "score", "gateway", patch={"learned_count": (st["learned_count"] or 0) + 1},
                          reason=f"{r['id']} approved; counts toward promotion")
        out.append({"id": r["id"], "approved": ok})
    return out
