"""Incidents -> post-mortem -> lesson -> human review -> applied change -> regression case.

Safety rules (docs/LEARNING.md): lessons can only tighten or add; they never touch hard limits, tiers, freezes or
approval thresholds; an unapproved lesson has no effect; invoice text never enters a lesson as an instruction.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from probation.gateway.audit import rows as audit_rows, tx
from probation.gateway.core import GATEWAY, PIP, Gateway
from probation.gateway.db import next_id
from probation.models import Denied, usd
from probation.policy import engine
from probation.policy.features import injection_hits
from probation.sim.backtest import history_cases
from probation.sim.setup import propose_policy

FORBIDDEN = [
    r"\b(raise|increase|lift|loosen|relax|remove|disable|skip|bypass|waive|ignore)\b.{0,40}\b(limit|tier|freeze|frozen|check|"
    r"threshold|approval|callback|verification|g-0\d|hard)\b",
    r"\bunfreeze\b", r"\bauto[-_ ]?approve\b", r"\bpre-?approved\b", r"\bno human\b", r"\bignore (all |any )?(prior|previous)\b",
    r"\b(tier|limit)\s*(=|to)\s*\$?\d", r"\bg-0[1-5]\b.{0,30}\b(off|disabled|not apply)\b",
]
ALLOWED_ACTIONS = {"memory_note", "policy", "skill_patch"}
SKILL_PATCHES = {
    "split_bills": ("detect-duplicates", "\n4. Split to dodge the limit: several bills against the same PO within 7 days, each under "
                    "your limit, that together exceed it. Treat them as one purchase: propose ASK, flag split_to_dodge_limit, and "
                    "route on the combined amount. Cite the PO and the earlier bills.\n"),
}


class LessonInvalid(ValueError):
    pass


def validate_lesson(lesson: dict) -> None:
    pa = lesson.get("proposed_action") or {}
    if pa.get("type") not in ALLOWED_ACTIONS:
        raise LessonInvalid("proposed_action.type must be memory_note, policy or skill_patch")
    text = " ".join(str(lesson.get(k, "")) for k in ("rule_text", "root_cause", "what_happened"))
    text += " " + json.dumps(pa.get("note", "")) + " " + json.dumps(pa.get("patch_text", ""))
    low = text.lower()
    for pat in FORBIDDEN:
        if re.search(pat, low):
            raise LessonInvalid(f"lesson text tries to relax a control ({pat})")
    if injection_hits(text):
        raise LessonInvalid("lesson contains instruction-like text aimed at bypassing a check")
    if pa.get("type") == "policy":
        try:
            engine.validate(pa["policy"], source="lesson")
        except engine.PolicyInvalid as e:
            raise LessonInvalid(str(e)) from e
    if set(pa) & {"tier", "limit", "tier_limit_cents", "frozen_vendors", "approval_matrix", "hard_limits"}:
        raise LessonInvalid("a lesson cannot touch tiers, limits, freezes or the approval matrix")
    if pa.get("check") and pa["check"] not in SKILL_PATCHES:
        raise LessonInvalid("unknown learnable check")


# ---------------------------------------------------------------- post-mortem
def postmortem(gw: Gateway, incident_id: str) -> dict:
    """Pip writes the lesson from the audit log and the records cited there. Rules template; an LLM version can
    replace the prose but goes through the same validator."""
    inc = dict(gw.work.execute("SELECT * FROM incident WHERE id=?", (incident_id,)).fetchone())
    details = json.loads(inc["details"] or "{}")
    items = details.get("work_items") or ([inc["work_item"]] if inc["work_item"] else [])
    log = [r for w in items for r in audit_rows(gw.work, w)]
    facts = [gw.item(w)["facts"] or {} for w in items]
    total = sum(gw.item(w)["amount_cents"] for w in items)
    po = next((f.get("po_id") for f in facts if f.get("po_id")), None)
    kind = details.get("pattern")
    if kind == "split_to_dodge_limit":
        limit = max((json.loads(r["diff"]) if isinstance(r["diff"], str) else r["diff"]).get("patch", {}).get("routing", {}).get("tier_limit_cents", 0)
                    for r in log) if log else 0
        limit = limit or gw.agent_state()["limit_cents"]
        lesson = {
            "tags": ["split_to_dodge_limit", "general"],
            "what_happened": (f"{len(items)} bills from {facts[0].get('vendor_name')} against {po} totalling {usd(total)} were each "
                              f"under the {usd(limit)} limit and each was paid on its own ({', '.join(items)})."),
            "root_cause": "Pip judged each bill alone. Nothing compared bills against the same PO in the same week, so a single "
                          "purchase split into parts never reached a human.",
            "rule_text": "When a PO has been billed more than once in 7 days and the bills together exceed my limit, treat them as "
                         "one purchase: ask a human and route on the combined amount.",
            "proposed_action": {
                "type": "policy", "check": "split_bills",
                "policy": {"id": "", "text": "Bills against the same PO within 7 days whose total exceeds Pip's limit: route to "
                                             "a human on the combined amount.",
                           "when": {"same_po_bills_7d": ">=2", "same_po_7d_total_cents": ">$tier_limit_cents"},
                           "then": "needs_approver", "route_on": "same_po_7d_total_cents"},
                "skill": SKILL_PATCHES["split_bills"][0], "patch_text": SKILL_PATCHES["split_bills"][1],
            },
            "evidence": [x for x in [po, *items, *[f.get("vendor_id") for f in facts[:1]]] if x],
        }
    else:
        lesson = {
            "tags": sorted(set(details.get("tags", [])) | {"general"}),
            "what_happened": details.get("summary", "A decision was corrected by a human."),
            "root_cause": "The case was not covered by an existing check.",
            "rule_text": "When this pattern appears again, ask a human and cite the earlier incident.",
            "proposed_action": {"type": "memory_note", "note": details.get("summary", "")},
            "evidence": items,
        }
    lesson["evidence"] = [e for e in lesson["evidence"] if e]
    return lesson


def variants(gw: Gateway, items: list[str], n: int = 12, seed: int = 7) -> list[dict]:
    """Shuffled versions of the incident (different vendor, part count, amounts) as bad replay cases."""
    rng = random.Random(seed)
    base = [gw.item(w)["facts"] or {} for w in items]
    limit = 500_000
    out = []
    for i in range(n):
        parts = rng.randint(2, 5)
        amts = [rng.randint(int(limit * 0.6), int(limit * 0.99)) for _ in range(parts)]
        for k in range(1, parts + 1):
            f = dict(base[0])
            f.update(amount_cents=amts[k - 1], same_po_bills_7d=k, same_po_7d_total_cents=sum(amts[:k]),
                     vendor_id=f"variant-{i}")
            out.append({"features": f, "bad": k >= 2 and sum(amts[:k]) > limit, "ref": f"variant-{i}.{k}"})
    for w, f in zip(items, base):
        out.append({"features": f, "bad": f.get("same_po_bills_7d", 1) >= 2, "ref": w})
    return out


def propose_lesson(gw: Gateway, incident_id: str, lesson: dict | None = None) -> str:
    lesson = lesson or postmortem(gw, incident_id)
    validate_lesson(lesson)
    inc = dict(gw.work.execute("SELECT * FROM incident WHERE id=?", (incident_id,)).fetchone())
    items = json.loads(inc["details"] or "{}").get("work_items") or [inc["work_item"]]
    bt = None
    pa = lesson["proposed_action"]
    if pa["type"] == "policy":
        cases = history_cases(gw) + variants(gw, items)
        bt = engine.backtest(pa["policy"], cases, {"tier_limit_cents": 500_000})
    with tx(gw.work):
        lid = next_id(gw.work, "lesson", "lesson-", 3)
    gw.transition("lesson", lid, "propose", PIP,
                  patch={"incident_id": incident_id, "tags": lesson["tags"], "what_happened": lesson["what_happened"],
                         "root_cause": lesson["root_cause"], "rule_text": lesson["rule_text"], "proposed_action": pa,
                         "evidence": lesson["evidence"], "hits": 0, "backtest": bt},
                  reason=f"Post-mortem for {incident_id}: {lesson['rule_text']}", evidence=lesson["evidence"])
    return lid


def approve_lesson(gw: Gateway, lesson_id: str, actor: str, reason: str = "") -> dict:
    row = dict(gw.work.execute("SELECT * FROM lesson WHERE id=?", (lesson_id,)).fetchone())
    pa = json.loads(row["proposed_action"])
    validate_lesson({**row, "proposed_action": pa})       # re-check at approval time
    gw.transition("lesson", lesson_id, "approve", actor, patch={"approved_by": actor}, reason=reason or "Lesson approved")
    applied: dict = {}
    if pa["type"] == "policy":
        n = gw.work.execute("SELECT COUNT(*) FROM policy").fetchone()[0]
        pid = f"pol-{n + 20:03d}"
        body = dict(pa["policy"], id=pid)
        propose_policy(gw, body, "lesson", proposer=PIP, evidence=[lesson_id])
        gw.transition("policy", pid, "approve", actor, patch={"decided_by": actor, "decided_at": gw.clock.now()},
                      reason=f"Activated with {lesson_id}")
        applied["policy"] = pid
    if pa.get("skill") and pa.get("patch_text"):
        applied["skill_version"] = apply_skill_patch(gw, pa["skill"], pa["patch_text"], lesson_id, actor)
    st = gw.agent_state()
    gw.transition("agent", PIP, "score", GATEWAY, patch={"learned_count": (st["learned_count"] or 0) + 1},
                  reason=f"{lesson_id} approved; counts toward promotion")
    applied["regression"] = add_regression(gw, row["incident_id"], lesson_id)
    gw.maybe_promote()
    return applied


def reject_lesson(gw: Gateway, lesson_id: str, actor: str, reason: str = "") -> None:
    gw.transition("lesson", lesson_id, "reject", actor, patch={"approved_by": actor}, reason=reason or "Lesson rejected")


def apply_skill_patch(gw: Gateway, skill: str, patch_text: str, lesson_id: str, actor: str) -> int:
    path = Path(gw.s.skills_dir) / skill / "SKILL.md"
    holder: dict = {}

    def effect():
        text = path.read_text()
        m = re.search(r"^version:\s*(\d+)", text, re.M)
        v = int(m.group(1)) if m else 1
        if not gw.work.execute("SELECT 1 FROM skill_version WHERE skill=? AND version=?", (skill, v)).fetchone():
            gw.work.execute("INSERT INTO skill_version VALUES(?,?,?,?,?,?)", (skill, v, text, None, "initial", gw.clock.now()))
        new = re.sub(r"^version:\s*\d+", f"version: {v + 1}", text, count=1, flags=re.M)
        new = new.replace("\nCommon mistakes", patch_text + "\nCommon mistakes", 1) if "\nCommon mistakes" in new else new + patch_text
        gw.work.execute("INSERT INTO skill_version VALUES(?,?,?,?,?,?)", (skill, v + 1, new, lesson_id, actor, gw.clock.now()))
        path.write_text(new)
        holder["v"] = v + 1
        return {"skill": skill, "version": v + 1}

    gw.transition("skill", skill, "patch", GATEWAY, effect=effect, reason=f"{skill} patched from {lesson_id}, approved by {actor}",
                  evidence=[lesson_id])
    return holder["v"]


def add_regression(gw: Gateway, incident_id: str, lesson_id: str) -> str:
    inc = dict(gw.work.execute("SELECT * FROM incident WHERE id=?", (incident_id,)).fetchone())
    items = json.loads(inc["details"] or "{}").get("work_items") or [inc["work_item"]]
    doc_ids = [gw.item(w)["doc_id"] for w in items]
    rid = f"reg-{incident_id}"
    case = {"id": rid, "incident_id": incident_id, "lesson_id": lesson_id, "work_items": items, "doc_ids": doc_ids,
            "facts": [gw.item(w)["facts"] for w in items],
            "expect": "no bill after the first is auto-approved; Pip flags split_to_dodge_limit"}
    gw.transition("regression", rid, "add", GATEWAY, patch={"incident_id": incident_id, "lesson_id": lesson_id,
                                                            "doc_ids": doc_ids, "expect": case["expect"]},
                  reason=f"Regression case for {incident_id}")
    out = gw.s.regression_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rid}.json").write_text(json.dumps(case, indent=1, default=str))
    return rid


def check_regression(gw: Gateway, case: dict) -> dict:
    """Replays the stored facts through the current active policies and Pip's current checks."""
    from probation.agent.runner import PipContext, propose_rules
    ctx = PipContext(gw.s)
    limit = 500_000
    results = []
    for w, f in zip(case["work_items"], case["facts"]):
        pol = engine.evaluate(gw.active_policies(), f, {"tier_limit_cents": limit})
        p = propose_rules(ctx, {"id": w, "facts": f, "extracted": {}}, limit)
        later = f.get("same_po_bills_7d", 1) >= 2
        ok = (not later) or (bool(pol["tighten"]) and p.action != "pay")
        results.append({"work_item": w, "ok": ok, "pip": p.action, "tighten": [t["id"] for t in pol["tighten"]]})
    return {"id": case["id"], "passed": all(r["ok"] for r in results), "results": results}


def report_split_incident(gw: Gateway, items: list[str], actor: str) -> str:
    total = sum(gw.item(w)["amount_cents"] for w in items)
    return gw.open_incident(items[-1], "late_discovery", "human", "critical",
                            {"work_items": items, "pattern": "split_to_dodge_limit",
                             "summary": f"{len(items)} bills against one PO paid separately, {usd(total)} in total, each under the limit",
                             "tags": ["split_to_dodge_limit"]}, actor)


def lessons(gw: Gateway) -> list[dict]:
    out = []
    for r in gw.work.execute("SELECT * FROM lesson ORDER BY id DESC"):
        d = dict(r)
        for k in ("tags", "proposed_action", "evidence", "backtest"):
            d[k] = json.loads(d[k]) if d[k] else None
        out.append(d)
    return out


def ensure_not_denied(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Denied:
        return None
