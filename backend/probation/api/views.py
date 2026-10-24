"""Shape gateway rows for the UI. Read-only."""
from __future__ import annotations

import datetime as dt
import json

from probation.gateway import limits
from probation.models import usd

CHIP = {
    "bank_change": ("Bank details changed", "bad"), "lookalike_domain": ("Look-alike sender", "bad"),
    "injection": ("Instructions in email", "bad"), "duplicate_exact": ("Duplicate", "bad"),
    "duplicate_near": ("Possible duplicate", "bad"), "split_to_dodge_limit": ("Split to dodge limit", "bad"),
    "amount_tamper": ("Total doesn't add up", "bad"), "price_over_po": ("Over the PO", "warn"),
    "qty_over_po": ("More than ordered", "warn"), "receipt_shortfall": ("Short receipt", "warn"),
    "tax_id_mismatch": ("Tax id mismatch", "warn"), "new_vendor": ("New vendor", "info"),
    "intercompany": ("Intercompany", "info"), "vendor_frozen": ("Vendor frozen", "bad"),
}
ORDER = list(CHIP)
STATE_CHIP = {"POSTED": ("Paid", "good"), "REJECTED": ("Rejected", ""), "APPROVED": ("Approved", "good")}


def due(bill_date: str | None) -> str:
    try:
        d = dt.date.fromisoformat((bill_date or "")[:10]) + dt.timedelta(days=30)
        return d.strftime("%a %b %-d")
    except ValueError:
        return ""


def summarize(it: dict) -> dict:
    facts = it.get("facts") or {}
    p = it.get("proposal") or {}
    r = it.get("routing") or {}
    ex = it.get("extracted") or {}
    flags = set(facts.get("flags", [])) | set(p.get("flags") or [])
    top = next((f for f in ORDER if f in flags), None)
    if it["state"] in ("POSTED", "REJECTED") or top is None:
        chip, kind = STATE_CHIP.get(it["state"], ("Matched", "good") if it["state"] not in ("NEEDS_APPROVER",) else ("Needs approval", "warn"))
        if it["state"] == "POSTED":
            chip = "Paid by Pip" if (it.get("approved_by") or "").startswith("policy:") else "Paid, approved"
    else:
        chip, kind = CHIP[top]
    if it["state"] == "NEEDS_APPROVER" and top is None and r.get("rules") == ["G-03"]:
        chip, kind = "Over Pip's limit", "info"
    headline = (r.get("reasons") or [""])[0] if it["state"] not in ("POSTED",) else (
        f"Matched and paid · {r.get('auto_policy') or 'approved by ' + (it.get('approved_by') or '')}")
    approver = it.get("approved_by") or ""
    return {
        "id": it["id"], "doc_id": it["doc_id"], "state": it["state"], "vendor": facts.get("vendor_name") or ex.get("vendor_name"),
        "vendor_id": it.get("supplier"), "amount_cents": it["amount_cents"], "amount": usd(it["amount_cents"]),
        "bill_no": ex.get("bill_no"), "bill_date": ex.get("bill_date"), "due": due(ex.get("bill_date")),
        "chip": chip, "chip_kind": kind, "headline": headline, "flags": sorted(flags),
        "suggest": p.get("summary", ""), "question": p.get("question_for_human", ""), "pip_action": p.get("action"),
        "mode": p.get("mode"), "required_role": it.get("required_role"),
        "required_role_name": {"dept_manager": "Department manager", "controller": "Controller", "cfo": "CFO"}.get(it.get("required_role") or "", ""),
        "rules": r.get("rules", []), "policies": r.get("policies_matched", []),
        "approved_by": approver, "approved_by_name": limits.PEOPLE[approver].name if approver in limits.PEOPLE else approver,
        "paid_by_pip": approver.startswith("policy:"), "record_invoice": it.get("record_invoice"),
        "record_payment": it.get("record_payment"), "updated_at": it.get("updated_at"), "created_at": it.get("created_at"),
        "entity": it.get("entity_id"), "po": facts.get("po_id"),
    }


def evidence(gw, it: dict) -> dict:
    from probation.gateway.audit import rows as audit_rows
    facts = it.get("facts") or {}
    doc = gw.doc(it["doc_id"])
    st = gw.agent_state(expire=False)
    lim = st["limit_cents"]
    amt = it["amount_cents"]
    if it["state"] == "HELD":
        limit_line = f"At Level {st['effective_tier']} the limit is {usd(lim)}; the gateway's own checks stopped this bill, not its size."
    elif amt <= lim:
        limit_line = f"Inside Pip's current limit of {usd(lim)}. Anything held was held for the reasons above, not its size."
    else:
        limit_line = f"Over Pip's current limit of {usd(lim)}. A human decides."
    bank_hist = []
    if it.get("supplier"):
        for r in gw.golden.execute("""SELECT b.*, (SELECT COUNT(*) FROM payment p WHERE p.bank_account_id=b.id) AS n_payments,
                                      (SELECT MAX(paid_date) FROM payment p WHERE p.bank_account_id=b.id) AS last_paid
                                      FROM vendor_bank_account b WHERE vendor_id=? ORDER BY valid_from""", (it["supplier"],)):
            bank_hist.append(dict(r))
        recent = [dict(r) for r in gw.golden.execute(
            "SELECT id, vendor_invoice_no, invoice_date, total_cents, status, po_id FROM invoice WHERE vendor_id=? ORDER BY invoice_date DESC LIMIT 8",
            (it["supplier"],))]
    else:
        recent = []
    return {"item": summarize(it), "checks": facts.get("checks", []), "three_way": facts.get("three_way", []),
            "facts": {k: v for k, v in facts.items() if k not in ("checks", "three_way")},
            "doc": doc.model_dump(), "extracted": it.get("extracted"), "proposal": it.get("proposal"), "routing": {
                k: v for k, v in (it.get("routing") or {}).items()}, "limit_line": limit_line,
            "bank_history": bank_hist, "recent_invoices": recent, "audit": audit_rows(gw.work, it["id"])}


RECORD_TABLES = [("SUP-", "vendor"), ("SBA-", "vendor_bank_account"), ("PO-", "purchase_order"), ("PR-", "goods_receipt"),
                 ("PINV-", "invoice"), ("PAY-", "payment")]


def record(gw, rid: str) -> dict | None:
    for pre, table in RECORD_TABLES:
        if rid.startswith(pre):
            r = gw.golden.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
            if r is None:
                return None
            out = {"id": rid, "table": table, "row": dict(r)}
            child = {"purchase_order": ("po_line", "po_id"), "goods_receipt": ("receipt_line", "receipt_id"),
                     "invoice": ("invoice_line", "invoice_id")}.get(table)
            if child:
                out["lines"] = [dict(x) for x in gw.golden.execute(f"SELECT * FROM {child[0]} WHERE {child[1]}=?", (rid,))]
            return out
    for pre, table in [("lesson-", "lesson"), ("pol-", "policy"), ("prec-", "precedent"), ("inc-", "incident"), ("DOC-", "inbox_doc")]:
        if rid.startswith(pre):
            r = gw.work.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
            if r:
                d = dict(r)
                for k, v in list(d.items()):
                    if isinstance(v, str) and v[:1] in "[{":
                        try:
                            d[k] = json.loads(v)
                        except ValueError:
                            pass
                return {"id": rid, "table": table, "row": d}
    return None
