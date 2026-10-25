"""The gateway: the only writer. Every state change goes through transition(), which writes the audit row in the
same work.db transaction. Denied attempts are logged and raise Denied.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Callable

from probation import events
from probation.config import Settings
from probation.gateway import audit, limits
from probation.gateway.audit import tx
from probation.gateway.db import next_id, open_work
from probation.models import (
    BankAccountRecord, Denied, ExtractedInvoice, InboxDoc, InvoiceLine, PaymentEntry, Proposal,
    PurchaseInvoice, State, usd,
)
from probation.policy import engine as policy_engine
from probation.policy.features import compute as compute_features, open_golden_ro
from probation.sim.clock import SimClock

# (kind, from_state, action) -> to_state. "*" matches any state and keeps it.
TRANSITIONS: dict[tuple[str, str | None, str], str] = {
    ("work_item", None, "create"): State.NEW,
    ("work_item", State.NEW, "extract"): State.EXTRACTED,
    ("work_item", State.EXTRACTED, "propose"): State.PROPOSED,
    ("work_item", State.PROPOSED, "auto_approve"): State.AUTO_APPROVED,
    ("work_item", State.PROPOSED, "route_to_approver"): State.NEEDS_APPROVER,
    ("work_item", State.PROPOSED, "hold"): State.HELD,
    ("work_item", State.PROPOSED, "block"): State.BLOCKED,
    ("work_item", State.AUTO_APPROVED, "post"): State.POSTED,
    ("work_item", State.NEEDS_APPROVER, "approve"): State.APPROVED,
    ("work_item", State.NEEDS_APPROVER, "reject"): State.REJECTED,
    ("work_item", State.APPROVED, "post"): State.POSTED,
    ("work_item", State.HELD, "verify"): State.NEEDS_APPROVER,
    ("work_item", State.HELD, "reject"): State.REJECTED,
    ("work_item", State.BLOCKED, "reject"): State.REJECTED,
    ("work_item", State.BLOCKED, "reopen"): State.NEEDS_APPROVER,
    ("policy", None, "propose"): "proposed",
    ("policy", "proposed", "approve"): "active",
    ("policy", "proposed", "reject"): "rejected",
    ("policy", "active", "retire"): "retired",
    ("lesson", None, "propose"): "proposed",
    ("lesson", "proposed", "approve"): "approved",
    ("lesson", "proposed", "reject"): "rejected",
    ("lesson", "approved", "retire"): "retired",
    ("incident", None, "open"): "open",
    ("incident", "open", "close"): "closed",
    ("agent", "*", "hire"): "*",
    ("agent", "*", "promote"): "*",
    ("agent", "*", "demote"): "*",
    ("agent", "*", "tighten"): "*",
    ("agent", "*", "restore"): "*",
    ("agent", "*", "score"): "*",
    ("vendor", "*", "freeze"): "*",
    ("vendor", "*", "unfreeze"): "*",
    ("vendor", "*", "change_bank"): "*",
    ("precedent", None, "record"): "recorded",
    ("skill", "*", "patch"): "*",
    ("regression", None, "add"): "active",
}

SYSTEM_ONLY = {"auto_approve", "route_to_approver", "hold", "block", "post", "promote", "demote", "tighten", "restore",
               "score", "hire", "add"}
EVIDENCE_PATTERNS = [
    (re.compile(r"^SUP-\d+$"), "golden", "vendor"),
    (re.compile(r"^SBA-\d+$"), "golden", "vendor_bank_account"),
    (re.compile(r"^PO-\d+$"), "golden", "purchase_order"),
    (re.compile(r"^PR-\d+$"), "golden", "goods_receipt"),
    (re.compile(r"^PINV-\d+$"), "golden", "invoice"),
    (re.compile(r"^PAY-\d+$"), "golden", "payment"),
    (re.compile(r"^DOC-[A-Z]?\d+$"), "work", "inbox_doc"),
    (re.compile(r"^WI-\d+$"), "work", "work_item"),
    (re.compile(r"^pol-\d+$"), "work", "policy"),
    (re.compile(r"^lesson-\d+$"), "work", "lesson"),
    (re.compile(r"^prec-\d+$"), "work", "precedent"),
    (re.compile(r"^inc-\d+$"), "work", "incident"),
]
PIP = "pip"
GATEWAY = "gateway"


def _j(x) -> str:
    return json.dumps(x, default=str, sort_keys=True)


class Gateway:
    def __init__(self, settings: Settings, record=None, bank=None):
        from probation.ports.simbank import SimBank
        from probation.ports.sqlite import SqliteRecord
        self.s = settings
        self.work: sqlite3.Connection = open_work(settings.work_db)
        if record is None and settings.record_backend == "erpnext":
            from probation.ports.erpnext import ErpnextRecord
            record = ErpnextRecord()
        self.record = record or SqliteRecord(settings.record_db)
        self.bank = bank or SimBank(settings.bank_db)
        self.clock = SimClock(self.work)
        self._golden: sqlite3.Connection | None = None
        self.work.execute("INSERT OR IGNORE INTO agent_state VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (PIP, 0, 0, 0, 0, None, None, None, "[]", 0, None))

    def close(self) -> None:
        for c in (self.work, getattr(self.record, "db", None), getattr(self.bank, "db", None), self._golden):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass

    # ---------- read helpers ----------
    @property
    def golden(self) -> sqlite3.Connection:
        if self._golden is None:
            self._golden = open_golden_ro(self.s.golden_db)
        return self._golden

    def resync(self) -> dict:
        from probation.sync.job import run_sync
        out = run_sync(self.record, self.s.golden_db)
        return out

    def item(self, wid: str) -> dict:
        r = self.work.execute("SELECT * FROM work_item WHERE id=?", (wid,)).fetchone()
        if r is None:
            raise KeyError(wid)
        d = dict(r)
        for k in ("extracted", "facts", "proposal", "routing"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def doc(self, doc_id: str) -> InboxDoc:
        r = self.work.execute("SELECT * FROM inbox_doc WHERE id=?", (doc_id,)).fetchone()
        if r is None:
            raise KeyError(doc_id)
        return InboxDoc(id=r["id"], received_at=r["received_at"], channel=r["channel"], from_addr=r["from_addr"],
                        subject=r["subject"], body=r["body"], attachments=json.loads(r["attachments"] or "[]"))

    def agent_state(self, agent_id: str = PIP, expire: bool = True) -> dict:
        d = dict(self.work.execute("SELECT * FROM agent_state WHERE agent_id=?", (agent_id,)).fetchone())
        d["frozen_vendors"] = json.loads(d["frozen_vendors"] or "[]")
        if expire:
            self._expire_tightening(d)
        d["effective_tier"] = self.effective_tier(d)
        d["limit_cents"] = limits.TIER_LIMIT_CENTS[d["effective_tier"]]
        return d

    def effective_tier(self, st: dict) -> int:
        if st["tightened_until"] and st["tightened_until"] > self.clock.now():
            return min(st["tier"], limits.TIGHTEN_TO_TIER)
        return st["tier"]

    def _expire_tightening(self, st: dict) -> None:
        if st["tightened_until"] and st["tightened_until"] <= self.clock.now():
            # the 7-day precaution ends on its own; logged as a gateway transition
            self.transition("agent", PIP, "restore", GATEWAY,
                            patch={"tightened_until": None, "tightened_from_tier": None},
                            reason=f"Tightening expired; limit back to Level {st['tier']}")
            st["tightened_until"] = None

    def active_policies(self) -> list[dict]:
        out = []
        for r in self.work.execute("SELECT id, body, source FROM policy WHERE status='active' ORDER BY id"):
            b = json.loads(r["body"])
            b["id"], b["source"] = r["id"], r["source"]
            out.append(b)
        return out

    # ---------- the one write path ----------
    def transition(self, kind: str, subject: str, action: str, actor: str, patch: dict | None = None, reason: str = "",
                   evidence: list[str] | None = None, effect: Callable[[], dict] | None = None,
                   ctx: dict | None = None) -> str:
        patch = dict(patch or {})
        evidence = list(evidence or [])
        now = self.clock.now()
        try:
            with tx(self.work):
                cur = self._state_of(kind, subject)
                to = TRANSITIONS.get((kind, cur, action)) or (TRANSITIONS.get((kind, "*", action)) if cur is not None else None)
                if to is None:
                    raise Denied(f"no transition {kind}:{cur} --{action}-->", rule="state_machine")
                if to == "*":
                    to = cur
                self.can(actor, action, kind, subject, cur, ctx or {})
                self.check_evidence(evidence)
                if effect is not None:
                    patch.update(effect() or {})
                diff = self._apply(kind, subject, cur, to, patch, now)
                audit.append(self.work, now, actor, action.upper(), subject, True, reason, diff, evidence)
        except Denied as d:
            with tx(self.work):
                audit.append(self.work, now, actor, action.upper(), subject, False, f"DENIED ({d.rule}): {d.reason}",
                             {"attempted": action, "patch_keys": sorted(patch)}, evidence)
            events.publish("gateway.decision", {"subject": subject, "action": action, "allowed": False, "reason": d.reason,
                                                "rule": d.rule, "actor": actor})
            raise
        events.publish("gateway.decision", {"subject": subject, "action": action, "allowed": True, "to": str(to),
                                            "actor": actor, "reason": reason})
        return str(to)

    def _state_of(self, kind: str, subject: str):
        table = {"work_item": "work_item", "policy": "policy", "lesson": "lesson", "incident": "incident"}.get(kind)
        if table:
            col = "state" if kind == "work_item" else "status"
            r = self.work.execute(f"SELECT {col} FROM {table} WHERE id=?", (subject,)).fetchone()
            return (State(r[0]) if kind == "work_item" else r[0]) if r else None
        if kind == "agent":
            return "active" if self.work.execute("SELECT 1 FROM agent_state WHERE agent_id=?", (subject,)).fetchone() else None
        if kind == "vendor":
            return "on_file" if self.golden.execute("SELECT 1 FROM vendor WHERE id=?", (subject,)).fetchone() else None
        if kind == "precedent":
            return "recorded" if self.work.execute("SELECT 1 FROM precedent WHERE id=?", (subject,)).fetchone() else None
        if kind == "regression":
            return "active" if self.work.execute("SELECT 1 FROM regression_case WHERE id=?", (subject,)).fetchone() else None
        if kind == "skill":
            return "on_disk"
        raise Denied(f"unknown subject kind {kind}", rule="state_machine")

    # ---------- permissions ----------
    def can(self, actor: str, action: str, kind: str, subject: str, cur, ctx: dict) -> None:
        role = limits.role_of(actor)
        if role == "unknown":
            raise Denied(f"unknown actor {actor}", rule="identity")
        if action in SYSTEM_ONLY and actor != GATEWAY:
            raise Denied(f"only the gateway may {action}", rule="G-01" if actor == PIP else "role")
        if kind == "work_item":
            if action in ("create", "extract") and role != "system":
                raise Denied("only the inbox pipeline creates and extracts work items", rule="role")
            if action == "propose" and actor != PIP:
                raise Denied("only Pip proposes", rule="role")
            if action in ("approve", "reject", "verify", "reopen"):
                it = self.item(subject)
                if not limits.is_human(actor):
                    raise Denied(f"{actor} is not a human approver" + (" (Pip never approves its own proposal)" if actor == PIP else ""),
                                 rule="G-01" if actor == PIP else "role")
                if actor in (it["requested_by"], it["proposed_by"]):
                    raise Denied("the requester or proposer cannot approve or reject their own request", rule="separation_of_duties")
                if action == "approve":
                    need = it["required_role"] or "dept_manager"
                    if not limits.role_can_approve(actor, need):
                        raise Denied(f"needs {need}; {actor} is {role}", rule="approval_matrix")
                    frozen = set(self.agent_state(expire=False)["frozen_vendors"])
                    if it["supplier"] in frozen:
                        raise Denied("vendor is frozen; verify by phone first", rule="G-04")
                if action in ("verify", "reopen") and not limits.role_can_approve(actor, "controller"):
                    raise Denied("phone verification and reopening need the controller", rule="G-04")
                if action == "reject" and not limits.role_can_approve(actor, "dept_manager"):
                    raise Denied("rejecting needs an approver role", rule="role")
        elif kind in ("policy", "lesson"):
            if action == "propose" and actor not in (PIP, GATEWAY, "sim"):
                raise Denied("policies and lessons are proposed by Pip or onboarding", rule="role")
            if action in ("approve", "reject", "retire"):
                if not limits.is_human(actor) or not limits.role_can_approve(actor, "controller"):
                    raise Denied("only the controller or CFO approves policies and lessons", rule="human_approval")
        elif kind == "vendor":
            if action in ("change_bank", "unfreeze") and not (limits.is_human(actor) and limits.role_can_approve(actor, "controller")):
                raise Denied("only a human controller can change bank details or lift a freeze", rule="G-02")
            if action == "freeze" and not (actor == GATEWAY or limits.is_human(actor)):
                raise Denied("freeze by gateway or a human", rule="role")
        elif kind == "incident":
            if action == "open" and actor not in (GATEWAY, "sim") and not limits.is_human(actor):
                raise Denied("incidents are opened by the gateway, a human or ground truth", rule="role")
            if action == "close" and not limits.is_human(actor):
                raise Denied("only a human closes an incident", rule="role")
        elif kind == "skill":
            if action == "patch" and actor != GATEWAY:
                raise Denied("skills change only through an approved patch applied by the gateway", rule="role")
        elif kind == "precedent":
            if actor != GATEWAY:
                raise Denied("precedents are recorded by the gateway from human decisions", rule="role")

    def check_evidence(self, ids: list[str]) -> None:
        for eid in ids:
            for pat, store, table in EVIDENCE_PATTERNS:
                if pat.match(eid):
                    db = self.golden if store == "golden" else self.work
                    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (eid,)).fetchone()
                    if row is None:
                        raise Denied(f"cited evidence {eid} does not exist", rule="evidence")
                    if table == "lesson" and row["status"] != "approved":
                        raise Denied(f"cited lesson {eid} is not approved", rule="evidence")
                    if table == "policy" and row["status"] != "active":
                        raise Denied(f"cited policy {eid} is not active", rule="evidence")
                    break
            else:
                raise Denied(f"cited evidence {eid!r} is not a known record id", rule="evidence")

    # ---------- apply patches ----------
    def _apply(self, kind: str, subject: str, cur, to, patch: dict, now: str) -> dict:
        diff: dict = {"from": str(cur) if cur is not None else None, "to": str(to)}
        if kind == "work_item":
            if cur is None:
                cols = {"id": subject, "kind": "purchase_invoice", "state": str(to), "created_at": now, "updated_at": now, **patch}
                for k in ("extracted", "facts", "proposal", "routing"):
                    if k in cols and not isinstance(cols[k], str):
                        cols[k] = _j(cols[k])
                self.work.execute(f"INSERT INTO work_item({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", list(cols.values()))
            else:
                sets = {"state": str(to), "updated_at": now}
                for k, v in patch.items():
                    sets[k] = _j(v) if k in ("extracted", "facts", "proposal", "routing") else v
                self.work.execute(f"UPDATE work_item SET {','.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), subject])
            diff["patch"] = {k: v for k, v in patch.items() if k not in ("extracted", "facts")}
        elif kind in ("policy", "lesson", "incident"):
            table = kind
            if cur is None:
                cols = {"id": subject, "status": str(to), **patch}
                if kind != "policy":
                    cols.setdefault("at", now)
                else:
                    cols.setdefault("created_at", now)
                cols = {k: (_j(v) if isinstance(v, (dict, list)) else v) for k, v in cols.items()}
                self.work.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", list(cols.values()))
            else:
                sets = {"status": str(to), **{k: (_j(v) if isinstance(v, (dict, list)) else v) for k, v in patch.items()}}
                self.work.execute(f"UPDATE {table} SET {','.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), subject])
            diff["patch"] = {k: v for k, v in patch.items() if k not in ("body",)}
        elif kind == "agent":
            sets = {k: (_j(v) if isinstance(v, (dict, list)) else v) for k, v in patch.items() if k != "tier_event"}
            if sets:
                self.work.execute(f"UPDATE agent_state SET {','.join(f'{k}=?' for k in sets)} WHERE agent_id=?", [*sets.values(), subject])
            te = patch.get("tier_event")
            if te:
                self.work.execute("INSERT INTO tier_event(at, agent_id, from_tier, to_tier, kind, reason) VALUES(?,?,?,?,?,?)",
                                  (now, subject, te["from"], te["to"], te["kind"], te["reason"]))
            diff["patch"] = patch
        elif kind == "precedent":
            cols = {"id": subject, "at": now, **{k: (_j(v) if isinstance(v, (dict, list)) else v) for k, v in patch.items()}}
            self.work.execute(f"INSERT INTO precedent({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", list(cols.values()))
            diff["patch"] = patch
        elif kind == "regression":
            cols = {"id": subject, "created_at": now, **{k: (_j(v) if isinstance(v, (dict, list)) else v) for k, v in patch.items()}}
            self.work.execute(f"INSERT INTO regression_case({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", list(cols.values()))
            diff["patch"] = patch
        else:  # vendor, skill: side effects were applied by `effect`; record what changed
            diff["patch"] = patch
        return diff

    # ======================================================================
    # Inbox -> work item
    # ======================================================================
    def receive(self, doc: InboxDoc, set_name: str = "live", scenario: str | None = None) -> str:
        """Stores the raw document. Untrusted; nothing in it is ever executed."""
        with tx(self.work):
            exists = self.work.execute("SELECT 1 FROM inbox_doc WHERE id=?", (doc.id,)).fetchone()
            if not exists:
                self.work.execute("INSERT INTO inbox_doc VALUES(?,?,?,?,?,?,?,?,?)",
                                  (doc.id, doc.received_at, doc.channel, doc.from_addr, doc.subject, doc.body,
                                   _j(doc.attachments), set_name, scenario))
            wid = next_id(self.work, "work_item", "WI-")
        self.transition("work_item", wid, "create", "inbox", patch={"doc_id": doc.id, "requested_by": "inbox"},
                        reason=f"{doc.channel} from {doc.from_addr or 'paste box'}: {doc.subject[:80]}", evidence=[doc.id])
        return wid

    def new_doc_id(self, prefix: str = "DOC-L") -> str:
        with tx(self.work):
            return next_id(self.work, f"doc_{prefix}", prefix, 5)

    def record_extraction(self, wid: str, ex: ExtractedInvoice) -> None:
        it = self.item(wid)
        doc = self.doc(it["doc_id"])
        st = self.agent_state()
        facts = compute_features(doc, ex, self.golden, self.work, wid, set(st["frozen_vendors"]))
        requester = limits.actor_id_for_name(facts.get("po_requested_by") or "")
        self.transition("work_item", wid, "extract", "extractor",
                        patch={"extracted": ex.model_dump(), "facts": facts, "supplier": facts.get("vendor_id"),
                               "entity_id": facts.get("entity_id"), "amount_cents": ex.grand_total_cents,
                               "requested_by": requester},
                        reason=f"{ex.extractor} extraction: {ex.vendor_name} {ex.bill_no} {usd(ex.grand_total_cents)}",
                        evidence=[e for e in facts["evidence"] if not e.startswith("WI-")])

    # ======================================================================
    # Proposal -> routing (hard limits, tiers, approval matrix, policies)
    # ======================================================================
    def submit_proposal(self, p: Proposal) -> dict:
        it = self.item(p.work_item)
        self.transition("work_item", p.work_item, "propose", PIP,
                        patch={"proposal": p.model_dump(), "proposed_by": PIP},
                        reason=f"Pip proposes {p.action.upper()}: {p.summary[:300]}", evidence=p.evidence)
        self._count_memory_hits(p.evidence)
        return self.route(p.work_item)

    def _count_memory_hits(self, ids: list[str]) -> None:
        now = self.clock.now()
        for i in ids:
            if i.startswith("lesson-"):
                self.work.execute("UPDATE lesson SET hits=COALESCE(hits,0)+1, last_hit=? WHERE id=?", (now, i))

    def decide_route(self, it: dict) -> dict:
        """Pure decision from the gateway's own facts. The proposal can make things stricter, never looser."""
        doc = self.doc(it["doc_id"])
        st = self.agent_state()
        ex = ExtractedInvoice(**it["extracted"])
        facts = compute_features(doc, ex, self.golden, self.work, it["id"], set(st["frozen_vendors"]))
        p = it["proposal"] or {}
        flags = set(facts["flags"])
        amount = ex.grand_total_cents
        tier_limit = st["limit_cents"]
        ctx = {"tier_limit_cents": tier_limit}
        pol = policy_engine.evaluate(self.active_policies(), facts, ctx)
        reasons: list[str] = []
        rules: list[str] = []
        need = limits.required_role(amount, flags)
        route_amount = amount
        for tp in pol["tighten"]:
            if tp.get("route_on"):
                route_amount = max(route_amount, int(facts.get(tp["route_on"]) or 0))
            need = limits.stricter(need, limits.matrix_role(route_amount))
            need = limits.stricter(need, tp.get("min_role"))
            reasons.append(f"{tp['id']}: {tp.get('text', 'policy check')}")
        hard = flags & limits.HARD_FLAGS
        hold = flags & limits.HOLD_FLAGS
        block = flags & limits.BLOCK_FLAGS
        agent_flags = set(p.get("flags") or [])
        if block or p.get("action") == "reject":
            action, state = "block", State.BLOCKED
            if block:
                rules.append("duplicate")
                reasons.append("Already billed: same vendor and invoice number as a record on file.")
            if p.get("action") == "reject":
                reasons.append("Pip recommends rejecting.")
        elif hold or p.get("action") == "hold" or any(t["then"] == "hold" for t in pol["tighten"]):
            action, state = "hold", State.HELD
            if flags & {"bank_change", "bank_unverified", "lookalike_domain"}:
                rules.append("G-04")
                reasons.append("Bank details or sender do not match the file. Held until a phone check on the number on file.")
            if "injection" in flags:
                rules.append("G-05")
                reasons.append("The document contains instructions aimed at the AP system. Treated as data and reported.")
            if "vendor_frozen" in flags:
                rules.append("G-04")
                reasons.append("The vendor is frozen.")
        else:
            can_auto = (p.get("action") == "pay" and not hard and not pol["tighten"] and pol["auto_approve"]
                        and amount <= tier_limit and not (agent_flags - {"none"}))
            if can_auto:
                action, state = "auto_approve", State.AUTO_APPROVED
                reasons.append(f"{pol['auto_approve'][0]} matched, {usd(amount)} within Level {st['effective_tier']} limit of {usd(tier_limit)}.")
            else:
                action, state = "route_to_approver", State.NEEDS_APPROVER
                if amount > tier_limit:
                    rules.append("G-03")
                    reasons.append(f"{usd(amount)} is over Pip's limit of {usd(tier_limit)}.")
                if not pol["auto_approve"] and not pol["tighten"]:
                    reasons.append("No active policy covers this invoice.")
                if p.get("action") == "ask":
                    reasons.append("Pip asked for a human decision.")
                soft = flags - limits.HARD_FLAGS
                if soft:
                    reasons.append("Checks to review: " + ", ".join(sorted(soft)) + ".")
                if hard:
                    reasons.append("Hard flags: " + ", ".join(sorted(hard)) + ".")
        return {"action": action, "state": str(state), "required_role": need if action != "auto_approve" else None,
                "reasons": reasons, "rules": sorted(set(rules)), "policies_matched": pol["matched"],
                "auto_policy": pol["auto_approve"][0] if action == "auto_approve" else None,
                "tier": st["effective_tier"], "tier_limit_cents": tier_limit, "route_amount_cents": route_amount,
                "flags": sorted(flags), "agent_flags": sorted(agent_flags), "facts": facts}

    def route(self, wid: str) -> dict:
        it = self.item(wid)
        r = self.decide_route(it)
        facts = r.pop("facts")
        self.transition("work_item", wid, r["action"], GATEWAY,
                        patch={"routing": r, "required_role": r["required_role"], "facts": facts},
                        reason=" ".join(r["reasons"])[:600] or r["action"], evidence=[e for e in facts["evidence"] if not e.startswith("WI-")])
        if "injection" in r["flags"] and it["supplier"]:
            self.tighten_for_injection(it["supplier"], wid)
        if r["action"] == "auto_approve":
            self.post(wid, approver=f"policy:{r['auto_policy']}")
        return self.item(wid)

    # ======================================================================
    # Human decisions
    # ======================================================================
    def decide(self, wid: str, decision: str, actor: str, reason: str = "", new_bank: dict | None = None) -> dict:
        it = self.item(wid)
        if decision == "approve":
            self.transition("work_item", wid, "approve", actor, patch={"approved_by": actor}, reason=reason or "Approved")
            self._precedent(it, "approve", actor, reason)
            self.post(wid, approver=actor)
        elif decision == "reject":
            self.transition("work_item", wid, "reject", actor, patch={"approved_by": actor}, reason=reason or "Rejected")
            self._precedent(it, "reject", actor, reason)
        elif decision == "verify":
            # phone check on the number on file confirmed the new account: a human records it (G-02), then it needs approval
            def effect():
                if it["supplier"] and new_bank:
                    sba = self.record.next_name("supplier_bank_account", "SBA-", 5)
                    self.record.add_bank_account(BankAccountRecord(
                        name=sba, supplier=it["supplier"], bank_name=new_bank.get("bank_name", ""),
                        routing_last4=new_bank.get("routing_last4", ""), account_last4=new_bank["account_last4"],
                        valid_from=self.clock.today(), verified_by=limits.PEOPLE[actor].name, verified_at=self.clock.today(),
                        source="phone_verified"))
                    return {"new_bank_account": sba}
                return {}
            self.transition("vendor", it["supplier"], "change_bank", actor, reason=f"Phone check on file number confirmed new account for {wid}",
                            effect=effect) if (it["supplier"] and new_bank) else None
            if it["supplier"] in self.agent_state()["frozen_vendors"]:
                self.unfreeze(it["supplier"], actor, f"Verified by phone ({wid})")
            self.resync()
            r = self.decide_route_after_verify(it)
            self.transition("work_item", wid, "verify", actor, patch={"required_role": r}, reason=reason or "Verified by phone")
        elif decision == "reopen":
            self.transition("work_item", wid, "reopen", actor, patch={"required_role": "controller"}, reason=reason or "Reopened")
        else:
            raise Denied(f"unknown decision {decision}", rule="state_machine")
        self._score_pip(wid, decision, actor)
        return self.item(wid)

    def decide_route_after_verify(self, it: dict) -> str:
        return limits.stricter(limits.required_role(it["amount_cents"], set()), "controller")

    def _precedent(self, it: dict, decision: str, actor: str, reason: str) -> None:
        with tx(self.work):
            pid = next_id(self.work, "precedent", "prec-", 3)
        p = it["proposal"] or {}
        tags = sorted(set((it["facts"] or {}).get("flags", [])) | set(p.get("flags") or []))
        self.transition("precedent", pid, "record", GATEWAY,
                        patch={"work_item": it["id"], "vendor_id": it["supplier"], "tags": tags,
                               "agent_proposal": p.get("action"), "human_decision": decision, "reason": reason,
                               "outcome": "paid" if decision == "approve" else "not_paid"},
                        reason=f"{limits.PEOPLE.get(actor, limits.Actor(actor, actor, '')).name} {decision}d {it['id']} (Pip proposed {p.get('action')})")

    def _score_pip(self, wid: str, decision: str, actor: str) -> None:
        it = self.item(wid)
        pa = (it["proposal"] or {}).get("action")
        if pa is None or decision in ("verify", "reopen"):
            return
        correct = (decision == "approve" and pa in ("pay", "ask")) or (decision == "reject" and pa in ("ask", "hold", "reject"))
        self.score(correct, serious=False, reason=f"{wid}: Pip proposed {pa}, human chose {decision}")

    # ======================================================================
    # Posting: the only path that writes to the record and the bank
    # ======================================================================
    def post(self, wid: str, approver: str) -> dict:
        it = self.item(wid)
        ex = ExtractedInvoice(**it["extracted"])
        facts = it["facts"]
        if approver in (it["requested_by"], it["proposed_by"], GATEWAY):
            raise Denied("requester, approver and poster must differ", rule="separation_of_duties")
        if not it["supplier"] or not facts.get("bank_on_file_id"):
            raise Denied("no verified vendor bank account on file", rule="G-04")

        def effect() -> dict:
            # re-check at the last moment: bank on the invoice must be the verified one on file (G-04)
            fresh = compute_features(self.doc(it["doc_id"]), ex, self.golden, self.work, wid,
                                     set(self.agent_state(expire=False)["frozen_vendors"]))
            if fresh["bank_changed"]:
                raise Denied("bank details differ from the verified account on file", rule="G-04")
            if fresh["duplicate_exact"]:
                raise Denied("an invoice with this number is already on file", rule="duplicate")
            if approver.startswith("policy:") and it["amount_cents"] > self.agent_state(expire=False)["limit_cents"]:
                raise Denied("over Pip's limit; a human must approve", rule="G-03")
            pinv = self.record.next_name("purchase_invoice", "PINV-", 6)
            lines = [InvoiceLine(item_code=(fresh["three_way"][i].get("item_code") if i < len(fresh["three_way"]) else None),
                                 description=l.description, qty=l.qty, rate_cents=l.rate_cents, amount_cents=l.amount_cents)
                     for i, l in enumerate(ex.lines)]
            net = sum(l.amount_cents for l in lines)
            pinv = self.record.post_purchase_invoice(PurchaseInvoice(
                name=pinv, supplier=it["supplier"], company=it["entity_id"] or "HPI", bill_no=ex.bill_no,
                bill_date=ex.bill_date or self.clock.today(), posting_date=self.clock.today(), purchase_order=fresh.get("po_id"),
                net_total_cents=net, tax_cents=ex.grand_total_cents - net, grand_total_cents=ex.grand_total_cents,
                source_doc=it["doc_id"], items=lines))
            sba = fresh["bank_on_file_id"]
            acct = self.golden.execute("SELECT account_last4 FROM vendor_bank_account WHERE id=?", (sba,)).fetchone()[0]
            company = it["entity_id"] or "HPI"
            transfer = self.bank.send_ach(company, f"{sba}:{acct}", ex.grand_total_cents, f"{ex.bill_no} {pinv}")
            pay = self.record.next_name("payment_entry", "PAY-", 6)
            pay = self.record.post_payment_entry(PaymentEntry(name=pay, company=company, party=it["supplier"],
                                                        paid_amount_cents=ex.grand_total_cents, posting_date=self.clock.today(),
                                                        reference_invoice=pinv, bank_account=sba, transfer_id=transfer))
            return {"record_invoice": pinv, "record_payment": pay, "posted_by": GATEWAY, "approved_by": approver,
                    "transfer_id": transfer}

        def guarded() -> dict:
            try:
                return effect()
            except Denied:
                raise
            except Exception as e:  # the record refused the posting: a denied attempt, logged like any other
                raise Denied(f"system of record rejected the posting: {str(e)[:300]}", rule="record") from e

        self.transition("work_item", wid, "post", GATEWAY, patch={}, effect=guarded,
                        reason=f"Paid {usd(it['amount_cents'])} to the account on file · approved by {approver}")
        self.resync()
        return self.item(wid)

    # ======================================================================
    # Trust: tiers, tightening, freezes, incidents
    # ======================================================================
    def score(self, correct: bool, serious: bool, reason: str) -> None:
        st = self.agent_state()
        patch = {"correct": st["correct"] + (1 if correct else 0), "score": st["score"] + (1 if correct else 0)}
        if not correct:
            patch["last_wrong_at"] = self.clock.now()
        if serious:
            patch["serious_mistakes"] = st["serious_mistakes"] + 1
        self.transition("agent", PIP, "score", GATEWAY, patch=patch, reason=("correct: " if correct else "wrong: ") + reason)
        self.maybe_promote()

    def maybe_promote(self) -> None:
        from probation.gateway.trust import promotion_check
        st = self.agent_state()
        ok, why = promotion_check(st, self.clock.now())
        if ok:
            to = st["tier"] + 1
            self.transition("agent", PIP, "promote", GATEWAY,
                            patch={"tier": to, "tier_event": {"from": st["tier"], "to": to, "kind": "promote", "reason": why}},
                            reason=f"Level {st['tier']} to {to}: {why}")
            events.publish("tier.changed", {"from": st["tier"], "to": to, "reason": why})

    def demote(self, to: int, reason: str) -> None:
        st = self.agent_state()
        if st["tier"] <= to:
            return
        self.transition("agent", PIP, "demote", GATEWAY,
                        patch={"tier": to, "score": 0, "correct": 0, "tightened_until": None, "tightened_from_tier": None,
                               "tier_event": {"from": st["tier"], "to": to, "kind": "demote", "reason": reason}},
                        reason=f"Level {st['tier']} to {to}: {reason}")
        events.publish("tier.changed", {"from": st["tier"], "to": to, "reason": reason})

    def tighten_for_injection(self, vendor_id: str, wid: str) -> None:
        st = self.agent_state()
        until = SimClock.add_days(self.clock.now(), limits.TIGHTEN_DAYS)
        if st["tier"] > limits.TIGHTEN_TO_TIER:
            self.transition("agent", PIP, "tighten", GATEWAY,
                            patch={"tightened_until": until, "tightened_from_tier": st["tier"],
                                   "tier_event": {"from": st["effective_tier"], "to": limits.TIGHTEN_TO_TIER, "kind": "tighten",
                                                  "reason": f"untrusted instructions in {wid}"}},
                            reason=f"Limit cut to Level {limits.TIGHTEN_TO_TIER} until {until[:10]}: untrusted instructions in {wid}. A precaution, not a penalty.")
            events.publish("tier.changed", {"from": st["effective_tier"], "to": limits.TIGHTEN_TO_TIER, "reason": "injection"})
        self.freeze(vendor_id, GATEWAY, f"untrusted instructions in {wid}")

    def freeze(self, vendor_id: str, actor: str, reason: str) -> None:
        st = self.agent_state()
        if vendor_id in st["frozen_vendors"]:
            return
        frozen = st["frozen_vendors"] + [vendor_id]
        self.transition("vendor", vendor_id, "freeze", actor, reason=f"Frozen: {reason}")
        self.transition("agent", PIP, "score", GATEWAY, patch={"frozen_vendors": frozen}, reason=f"{vendor_id} added to frozen list")

    def unfreeze(self, vendor_id: str, actor: str, reason: str) -> None:
        self.transition("vendor", vendor_id, "unfreeze", actor, reason=reason)
        frozen = [v for v in self.agent_state()["frozen_vendors"] if v != vendor_id]
        self.transition("agent", PIP, "score", GATEWAY, patch={"frozen_vendors": frozen}, reason=f"{vendor_id} removed from frozen list by {actor}")

    def open_incident(self, wid: str | None, kind: str, detected_by: str, severity: str, details: dict, actor: str) -> str:
        with tx(self.work):
            iid = next_id(self.work, "incident", "inc-", 3)
        self.transition("incident", iid, "open", actor,
                        patch={"work_item": wid, "kind": kind, "detected_by": detected_by, "severity": severity, "details": details},
                        reason=f"{severity} {kind}: {details.get('summary', '')}"[:400], evidence=[wid] if wid else [])
        self.score(False, serious=severity == "critical", reason=f"incident {iid}: {details.get('summary', kind)}")
        if severity == "critical":
            self.demote(0, f"critical incident {iid}: a payment Pip should have stopped")
        return iid
