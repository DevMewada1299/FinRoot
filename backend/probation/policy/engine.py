"""Policy DSL evaluated by plain code. No free text, no code strings, no LLM.

A policy:
  {"id": "pol-001", "when": {"vendor_status": "known", "po_match": true, "price_delta_pct": "<=3",
   "bank_changed": false}, "then": "auto_approve", "max_amount_cents": 500000}
Operators in `when`: a literal (equality), "<=N", ">=N", "<N", ">N" (N may be $name from the context, e.g.
"$tier_limit_cents"), {"in": [...]}, {"not": literal}.
`then` is one of auto_approve | needs_approver | hold. needs_approver may carry "route_on": "<numeric feature>"
so the approver is chosen on that amount (e.g. the 7-day vendor total for split bills) and "min_role".
"""
from __future__ import annotations

import re

THEN = {"auto_approve", "needs_approver", "hold"}
TIGHTENING = {"needs_approver", "hold"}
FEATURES = {
    "vendor_status", "po_found", "po_match", "po_vendor_match", "price_delta_pct", "qty_over_po", "receipt_covers_invoice",
    "bank_changed", "sender_domain_on_file", "sender_lookalike", "tax_id_match", "arithmetic_ok", "duplicate_exact",
    "duplicate_near", "injection_suspected", "is_intercompany", "amount_cents", "vendor_7d_total_cents", "vendor_7d_count",
    "same_po_bills_7d", "same_po_7d_total_cents", "payments_to_account_on_file", "po_department", "vendor_group",
}
CONTEXT_VARS = {"tier_limit_cents"}
KEYS = {"id", "when", "then", "max_amount_cents", "route_on", "min_role", "source", "evidence", "text"}
ROLES = {"dept_manager", "controller", "cfo"}
_CMP = re.compile(r"^(<=|>=|<|>)\s*(\$?[A-Za-z_]+|-?\d+(?:\.\d+)?)$")


class PolicyInvalid(ValueError):
    pass


def validate(policy: dict, source: str | None = None) -> None:
    extra = set(policy) - KEYS
    if extra:
        raise PolicyInvalid(f"unknown keys {sorted(extra)}")
    if policy.get("then") not in THEN:
        raise PolicyInvalid("then must be auto_approve, needs_approver or hold")
    when = policy.get("when")
    if not isinstance(when, dict) or not when:
        raise PolicyInvalid("when must be a non-empty object")
    for k, cond in when.items():
        if k not in FEATURES:
            raise PolicyInvalid(f"unknown feature {k}")
        if isinstance(cond, str) and cond[:1] in "<>":
            m = _CMP.match(cond.strip())
            if not m:
                raise PolicyInvalid(f"bad comparison {cond!r}")
            ref = m.group(2)
            if ref.startswith("$") and ref[1:] not in CONTEXT_VARS:
                raise PolicyInvalid(f"unknown context variable {ref}")
        elif isinstance(cond, dict):
            if set(cond) - {"in", "not"} or len(cond) != 1:
                raise PolicyInvalid(f"bad operator in {k}")
            if "in" in cond and not isinstance(cond["in"], list):
                raise PolicyInvalid("in takes a list")
        elif not isinstance(cond, (str, int, float, bool)) and cond is not None:
            raise PolicyInvalid(f"bad condition for {k}")
    if policy.get("route_on") and policy["route_on"] not in FEATURES:
        raise PolicyInvalid("route_on must be a feature")
    if policy.get("min_role") and policy["min_role"] not in ROLES:
        raise PolicyInvalid("min_role must be a role")
    if "max_amount_cents" in policy and not isinstance(policy["max_amount_cents"], int):
        raise PolicyInvalid("max_amount_cents must be an integer")
    if policy["then"] == "auto_approve" and "max_amount_cents" not in policy:
        raise PolicyInvalid("auto_approve policies need max_amount_cents")
    # learned memory can only tighten
    if source == "lesson" and policy["then"] not in TIGHTENING:
        raise PolicyInvalid("a lesson may only add a check (needs_approver or hold), never an auto-approval")


def _num(ref: str, ctx: dict) -> float:
    return float(ctx.get(ref[1:], 0)) if ref.startswith("$") else float(ref)


def cond_holds(value, cond, ctx: dict) -> bool:
    if isinstance(cond, dict):
        if "in" in cond:
            return value in cond["in"]
        return value != cond["not"]
    if isinstance(cond, str) and cond[:1] in "<>":
        m = _CMP.match(cond.strip())
        if value is None or isinstance(value, bool):
            return False
        op, ref = m.group(1), _num(m.group(2), ctx)
        v = float(value)
        return {"<=": v <= ref, ">=": v >= ref, "<": v < ref, ">": v > ref}[op]
    return value == cond


def matches(policy: dict, features: dict, ctx: dict | None = None) -> bool:
    ctx = ctx or {}
    return all(cond_holds(features.get(k), c, ctx) for k, c in policy["when"].items())


def evaluate(policies: list[dict], features: dict, ctx: dict | None = None) -> dict:
    """Returns {"auto_approve": [ids that would allow], "tighten": [matching tightening policies], "matched": [...]}"""
    ctx = ctx or {}
    out = {"auto_approve": [], "tighten": [], "matched": []}
    for p in policies:
        if not matches(p, features, ctx):
            continue
        out["matched"].append(p["id"])
        if p["then"] == "auto_approve":
            if features.get("amount_cents", 0) <= p["max_amount_cents"]:
                out["auto_approve"].append(p["id"])
        else:
            out["tighten"].append(p)
    return out


def backtest(policy: dict, cases: list[dict], ctx: dict | None = None) -> dict:
    """cases: [{"features": {...}, "bad": bool, "ref": id}]. `bad` comes from runtime-visible outcomes only
    (human rejections, incidents, verified history), never from data/labels."""
    ctx = ctx or {}
    caught = missed = wrongly_held = approved_bad = approved_good = 0
    caught_refs, wrong_refs = [], []
    for c in cases:
        hit = matches(policy, c["features"], ctx)
        if policy["then"] == "auto_approve":
            if hit and c["features"].get("amount_cents", 0) <= policy["max_amount_cents"]:
                if c["bad"]:
                    approved_bad += 1
                    wrong_refs.append(c["ref"])
                else:
                    approved_good += 1
            continue
        if c["bad"]:
            if hit:
                caught += 1
                caught_refs.append(c["ref"])
            else:
                missed += 1
        elif hit:
            wrongly_held += 1
            wrong_refs.append(c["ref"])
    n_bad = sum(1 for c in cases if c["bad"])
    return {"n": len(cases), "bad": n_bad, "caught": caught, "missed": missed, "wrongly_held": wrongly_held,
            "approved_good": approved_good, "approved_bad": approved_bad, "caught_refs": caught_refs[:20],
            "wrong_refs": wrong_refs[:20]}


ONBOARDING_POLICIES = [
    {"id": "pol-001", "text": "Known vendor, PO matches within 3%, goods received, same bank account: pay, up to Pip's limit.",
     "when": {"vendor_status": "known", "po_match": True, "price_delta_pct": "<=3", "receipt_covers_invoice": True,
              "bank_changed": False, "sender_lookalike": False, "tax_id_match": True, "arithmetic_ok": True,
              "duplicate_exact": False, "duplicate_near": False, "injection_suspected": False},
     "then": "auto_approve", "max_amount_cents": 2_500_000, "source": "onboarding"},
    {"id": "pol-002", "text": "Recurring non-PO services (utilities, software, fuel) from a known vendor, same bank account, "
                              "no change in sender: pay, up to Pip's limit.",
     "when": {"vendor_status": "known", "po_found": False, "vendor_group": {"in": ["Utilities", "IT & Software", "Fuel & Fleet"]},
              "bank_changed": False, "sender_domain_on_file": True, "tax_id_match": True, "arithmetic_ok": True,
              "duplicate_exact": False, "duplicate_near": False, "injection_suspected": False},
     "then": "auto_approve", "max_amount_cents": 2_500_000, "source": "onboarding"},
    {"id": "pol-004", "text": "New or changed bank details: hold until a human confirms by phone on the number on file.",
     "when": {"bank_changed": True}, "then": "hold", "source": "onboarding"},
]
