"""SimPeople: the department manager, controller and CFO as simple personas. They decide from the evidence the
gateway shows (facts and flags), never from the answer key, and their decisions go through gateway.decide like a
human's. The controller is sometimes conservative: price rises over 10% are rejected."""
from __future__ import annotations

from probation.gateway import limits
from probation.models import Denied

BY_ROLE = {"dept_manager": "alicia.brandt", "controller": "marisol.vega", "cfo": "renata.ellis"}
UP = {"alicia.brandt": "marisol.vega", "marisol.vega": "renata.ellis", "renata.ellis": "renata.ellis"}


def choose_actor(it: dict) -> str:
    a = BY_ROLE.get(it.get("required_role") or "dept_manager", "marisol.vega")
    while a in (it.get("requested_by"), it.get("proposed_by")) and UP[a] != a:
        a = UP[a]
    return a


def judgement(it: dict) -> tuple[str, str]:
    f = it.get("facts") or {}
    flags = set(f.get("flags", [])) | set((it.get("proposal") or {}).get("flags") or [])
    if it["state"] == "BLOCKED":
        return "reject", "Confirmed duplicate"
    if it["state"] == "HELD":
        if flags & {"injection", "lookalike_domain", "vendor_frozen"}:
            return "reject", "Called the number on file: the vendor did not send this"
        if "bank_change" in flags and f.get("sender_domain_on_file"):
            return "verify", "Called the number on file: vendor confirmed the new account"
        return "reject", "Could not verify"
    if flags & {"duplicate_exact", "duplicate_near", "amount_tamper", "qty_over_po", "receipt_shortfall", "tax_id_mismatch",
                "po_vendor_mismatch"}:
        return "reject", "Sent back to the vendor: " + ", ".join(sorted(flags & {"duplicate_exact", "duplicate_near", "amount_tamper",
                                                                                   "qty_over_po", "receipt_shortfall", "tax_id_mismatch"}))
    if "price_over_po" in flags:
        if (f.get("price_delta_pct") or 0) <= 10:
            return "approve", f"Price rise of {f.get('price_delta_pct')}% accepted"
        return "reject", f"Price rise of {f.get('price_delta_pct')}% not agreed"
    return "approve", "Checked the evidence; approved"


def decide_open(gw, limit: int = 500) -> list[dict]:
    out = []
    ids = [r[0] for r in gw.work.execute("SELECT id FROM work_item WHERE state IN ('NEEDS_APPROVER','HELD','BLOCKED') ORDER BY id LIMIT ?", (limit,))]
    for wid in ids:
        it = gw.item(wid)
        decision, reason = judgement(it)
        actor = choose_actor(it) if decision != "verify" else "marisol.vega"
        try:
            gw.decide(wid, decision, actor, f"[sim] {reason}")
            if decision == "verify":
                it2 = gw.item(wid)
                gw.decide(wid, "approve", choose_actor(it2), "[sim] Approved after phone verification")
            out.append({"id": wid, "decision": decision, "actor": actor})
        except Denied as d:
            out.append({"id": wid, "decision": "denied", "reason": d.reason})
    return out


_ = limits
