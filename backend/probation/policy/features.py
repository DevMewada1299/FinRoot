"""Facts about one incoming invoice, computed by plain code from golden.db and work.db.

The gateway computes these itself and never takes them from the agent. The agent may read the same facts
through a tool, but the policy engine and hard limits only ever see this module's output.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sqlite3
from pathlib import Path

from probation.models import ExtractedInvoice, InboxDoc, usd
from probation.sync.job import norm_invoice_no, norm_name

INJECTION_PATTERNS = [
    r"\bnote to (the )?ap\b", r"\bap system\b", r"\bai (reviewer|agent|assistant)\b", r"\bautomated (payables|processors?|system)\b",
    r"\bignore (all |any )?(prior|previous)\b", r"\bskip (any |the )?(callback|call back|verification|review)\b",
    r"\bpre-?approved\b", r"\bapproval\s*=\s*true\b", r"\bsystem instruction\b", r"\bassistant note\b",
    r"\bno human review\b", r"\bdo not escalate\b", r"\bupdate the vendor bank details\b", r"\btreat this invoice as verified\b",
    r"\bsigned off on this\b", r"\brelease payment\b",
]


def open_golden_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=1")
    return db


def injection_hits(text: str) -> list[str]:
    t = (text or "").lower()
    return [p for p in INJECTION_PATTERNS if re.search(p, t)]


def domain_base(d: str) -> str:
    d = d.lower().strip()
    parts = d.split(".")
    return parts[0] if len(parts) > 1 else d


def lookalike(sender: str, on_file: str) -> bool:
    if not sender or not on_file or sender == on_file:
        return False
    a, b = domain_base(sender), domain_base(on_file)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return a == b or b in a or a in b or ratio >= 0.8 or a.replace("0", "o").replace("1", "l") == b


def similar_invoice_no(a: str, b: str) -> bool:
    a, b = norm_invoice_no(a), norm_invoice_no(b)
    if not a or not b or a == b:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    if len(a) == len(b) and sorted(a) == sorted(b) and sum(x != y for x, y in zip(a, b)) == 2:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


def _best_line(desc: str, item_code: str | None, po_lines: list[sqlite3.Row]) -> sqlite3.Row | None:
    if item_code:
        for pl in po_lines:
            if pl["item_code"] == item_code:
                return pl
    best, score = None, 0.0
    d = desc.lower()
    for pl in po_lines:
        s = difflib.SequenceMatcher(None, d, pl["description"].lower()).ratio()
        if s > score:
            best, score = pl, s
    return best if score >= 0.6 else None


def compute(doc: InboxDoc, ex: ExtractedInvoice, golden: sqlite3.Connection, work: sqlite3.Connection | None,
            work_item_id: str | None = None, frozen_vendors: set[str] | None = None) -> dict:
    f: dict = {"amount_cents": ex.grand_total_cents}
    flags: set[str] = set()
    evidence: list[str] = [doc.id]
    checks: list[dict] = []
    sender = (doc.from_addr.split("@")[-1] if "@" in doc.from_addr else "").lower().strip()
    bill_day = ex.bill_date or doc.received_at[:10]

    # ---- vendor: by name (typo tolerant), then tax id, then sender domain ----
    v = None
    how = None
    if ex.vendor_name:
        target = norm_name(ex.vendor_name)
        same = golden.execute("SELECT * FROM vendor WHERE name_norm=?", (target,)).fetchall()
        v = next((x for x in same if x["tax_id"] == ex.vendor_tax_id.strip()), same[0] if same else None)
        if v is None:
            names = golden.execute("SELECT id, name_norm FROM vendor").fetchall()
            best = max(names, key=lambda r: difflib.SequenceMatcher(None, target, r["name_norm"]).ratio(), default=None)
            if best and difflib.SequenceMatcher(None, target, best["name_norm"]).ratio() >= 0.9:
                v = golden.execute("SELECT * FROM vendor WHERE id=?", (best["id"],)).fetchone()
        how = "name" if v else None
    if v is None and ex.vendor_tax_id:
        v = golden.execute("SELECT * FROM vendor WHERE tax_id=?", (ex.vendor_tax_id.strip(),)).fetchone()
        how = "tax_id" if v else None
    if v is None and sender:
        v = golden.execute("SELECT * FROM vendor WHERE email_domain=?", (sender,)).fetchone()
        how = "domain" if v else None
    f["vendor_id"] = v["id"] if v else None
    f["vendor_name"] = v["name"] if v else ex.vendor_name
    f["vendor_matched_by"] = how
    if v is None:
        f["vendor_status"] = "new"
        flags.add("new_vendor")
        checks.append(dict(key="vendor", label="Vendor", inv=ex.vendor_name or "(none)", rec="Not in the vendor file",
                           bank="No payment history", st="warn", note="First bill from a vendor we have never paid."))
    else:
        evidence.append(v["id"])
        frozen = v["status"] == "frozen" or (frozen_vendors and v["id"] in frozen_vendors)
        f["vendor_status"] = "frozen" if frozen else ("intercompany" if v["is_intercompany"] else "known")
        f["vendor_group"] = v["vendor_group"]
        if frozen:
            flags.add("vendor_frozen")
        if v["is_intercompany"]:
            flags.add("intercompany")
        f["tax_id_match"] = (not ex.vendor_tax_id) or ex.vendor_tax_id.strip() == v["tax_id"]
        if not f["tax_id_match"]:
            flags.add("tax_id_mismatch")
        checks.append(dict(key="vendor", label="Vendor", inv=f"{ex.vendor_name} · tax id {ex.vendor_tax_id or 'n/a'}",
                           rec=f"{v['name']} ({v['id']}) · tax id {v['tax_id']}", bank=f"On file since {v['created_at']}",
                           st="ok" if f["tax_id_match"] else "bad",
                           note="Tax id matches the vendor file." if f["tax_id_match"] else "Tax id on the invoice is not the one on file.",
                           refs=[v["id"]]))

    # ---- sender ----
    f["sender_domain"] = sender
    if v is not None:
        f["sender_domain_on_file"] = sender == v["email_domain"]
        f["sender_lookalike"] = lookalike(sender, v["email_domain"])
        if f["sender_lookalike"]:
            flags.add("lookalike_domain")
        if sender:
            checks.append(dict(key="sender", label="Sender", inv=doc.from_addr, rec=f"@{v['email_domain']} on file",
                               bank="", st="ok" if f["sender_domain_on_file"] else ("bad" if f["sender_lookalike"] else "warn"),
                               note="Same domain as on file." if f["sender_domain_on_file"] else
                               ("Look-alike of the domain on file." if f["sender_lookalike"] else "Different domain from the one on file."),
                               refs=[v["id"]]))

    # ---- bank ----
    f["bank_changed"] = False
    if v is not None:
        hist = golden.execute("SELECT * FROM vendor_bank_account WHERE vendor_id=? ORDER BY valid_from", (v["id"],)).fetchall()
        cur = next((h for h in hist if h["valid_to"] is None), hist[-1] if hist else None)
        n_paid = 0
        if cur is not None:
            evidence.append(cur["id"])
            n_paid = golden.execute("SELECT COUNT(*) FROM payment WHERE bank_account_id=?", (cur["id"],)).fetchone()[0]
            f["bank_on_file_last4"] = cur["account_last4"]
            f["bank_on_file_id"] = cur["id"]
            f["bank_verified"] = bool(cur["verified_at"])
            if not cur["verified_at"]:
                flags.add("bank_unverified")
        f["payments_to_account_on_file"] = n_paid
        if ex.remit_account_last4 and cur is not None and ex.remit_account_last4 != cur["account_last4"]:
            f["bank_changed"] = True
            flags.add("bank_change")
        st = "bad" if f["bank_changed"] else "ok"
        checks.append(dict(key="bank", label="Pay-to account",
                           inv=f"••{ex.remit_account_last4} {ex.remit_bank_name}".strip() if ex.remit_account_last4 else "(not stated)",
                           rec=f"••{cur['account_last4']} {cur['bank_name']}, verified {cur['verified_at'] or 'never'}" if cur else "none",
                           bank=f"{n_paid} earlier payments to ••{cur['account_last4']}" if cur else "No history",
                           st=st, note="A different account from the one on file. Phone check on the number on file required (G-04)."
                           if f["bank_changed"] else "Same account as always.", refs=[cur["id"]] if cur else []))

    # ---- arithmetic ----
    lines_sum = sum(l.amount_cents for l in ex.lines)
    f["lines_sum_cents"] = lines_sum
    line_math_ok = all(abs(l.qty * l.rate_cents - l.amount_cents) <= max(1, l.qty // 2) for l in ex.lines)
    f["arithmetic_ok"] = bool(ex.lines) and line_math_ok and abs(lines_sum + ex.tax_cents - ex.grand_total_cents) <= 2
    if ex.lines and not f["arithmetic_ok"]:
        flags.add("amount_tamper")
    checks.append(dict(key="total", label="Total", inv=usd(ex.grand_total_cents),
                       rec=f"Lines {usd(lines_sum)} + tax {usd(ex.tax_cents)} = {usd(lines_sum + ex.tax_cents)}",
                       bank="", st="ok" if f["arithmetic_ok"] else "bad",
                       note="Lines and tax add up to the total." if f["arithmetic_ok"] else "The total does not equal the lines plus tax."))

    # ---- PO and three-way match ----
    f["po_found"] = False
    f["po_match"] = False
    f["price_delta_pct"] = 0.0
    f["qty_over_po"] = False
    f["receipt_covers_invoice"] = False
    three_way: list[dict] = []
    po = golden.execute("SELECT * FROM purchase_order WHERE id=?", (ex.po_number.strip().upper(),)).fetchone() if ex.po_number else None
    if po is not None:
        f["po_found"] = True
        evidence.append(po["id"])
        f["po_id"] = po["id"]
        f["po_total_cents"] = po["total_cents"]
        f["po_requested_by"] = po["requested_by"]
        f["po_department"] = po["department"]
        f["po_vendor_match"] = v is not None and po["vendor_id"] == v["id"]
        po_lines = golden.execute("SELECT * FROM po_line WHERE po_id=? ORDER BY line_no", (po["id"],)).fetchall()
        receipts = golden.execute("SELECT * FROM goods_receipt WHERE po_id=?", (po["id"],)).fetchall()
        evidence += [r["id"] for r in receipts]
        received: dict[str, int] = {}
        for r in receipts:
            for rl in golden.execute("SELECT * FROM receipt_line WHERE receipt_id=?", (r["id"],)):
                received[rl["item_code"]] = received.get(rl["item_code"], 0) + rl["received_qty"]
        prior_inv = golden.execute("SELECT id FROM invoice WHERE po_id=?", (po["id"],)).fetchall()
        billed_before: dict[str, int] = {}
        for pi in prior_inv:
            for il in golden.execute("SELECT * FROM invoice_line WHERE invoice_id=?", (pi["id"],)):
                billed_before[il["item_code"]] = billed_before.get(il["item_code"], 0) + il["qty"]
        # bills against the same PO that are in flight in work.db but not yet in the record
        if work is not None:
            for wi in work.execute("SELECT id, extracted FROM work_item WHERE record_invoice IS NULL AND state NOT IN ('REJECTED','BLOCKED') "
                                   "AND id<>? AND json_extract(extracted,'$.po_number')=?", (work_item_id or "", po["id"])):
                for l in json.loads(wi["extracted"]).get("lines", []):
                    pl = _best_line(l["description"], l.get("item_code"), po_lines)
                    if pl is not None:
                        billed_before[pl["item_code"]] = billed_before.get(pl["item_code"], 0) + l["qty"]
        all_matched = bool(ex.lines)
        max_delta = 0.0
        covers = True
        over = False
        for l in ex.lines:
            pl = _best_line(l.description, l.item_code, po_lines)
            if pl is None:
                all_matched = False
                three_way.append(dict(desc=l.description, inv_qty=l.qty, inv_rate=l.rate_cents, po_qty=None, po_rate=None,
                                      received=None, st="bad"))
                continue
            delta = (l.rate_cents - pl["rate_cents"]) * 100.0 / pl["rate_cents"] if pl["rate_cents"] else 0.0
            max_delta = max(max_delta, delta)
            prev = billed_before.get(pl["item_code"], 0)
            rec_q = received.get(pl["item_code"], 0)
            line_over = prev + l.qty > pl["qty"]
            line_short = prev + l.qty > rec_q
            over = over or line_over
            covers = covers and not line_short
            three_way.append(dict(desc=l.description, item_code=pl["item_code"], inv_qty=l.qty, inv_rate=l.rate_cents,
                                  po_qty=pl["qty"], po_rate=pl["rate_cents"], billed_before=prev, received=rec_q,
                                  st="bad" if (line_over or line_short) else ("warn" if delta > 0.5 else "ok")))
        f["price_delta_pct"] = round(max_delta, 2)
        f["qty_over_po"] = over
        f["receipt_covers_invoice"] = covers and bool(receipts)
        f["po_match"] = all_matched and not over and f["po_vendor_match"]
        f["po_invoices_before"] = len(prior_inv)
        if max_delta > 0.5:
            flags.add("price_over_po")
        if over:
            flags.add("qty_over_po")
        if not f["receipt_covers_invoice"]:
            flags.add("receipt_shortfall")
        if not f["po_vendor_match"]:
            flags.add("po_vendor_mismatch")
        checks.append(dict(key="po", label="Purchase order", inv=f"{ex.po_number}", rec=f"{po['id']}: {usd(po['total_cents'])}, {po['department']}",
                           bank="", st="ok" if f["po_match"] and max_delta <= 0.5 else ("warn" if f["po_match"] else "bad"),
                           note=(f"Highest unit price is {max_delta:.1f}% over the PO." if max_delta > 0.5 else "Prices match the PO.")
                           + (" Billed more units than ordered." if over else ""), refs=[po["id"]]))
        checks.append(dict(key="receipt", label="Goods received", inv=f"{sum(l.qty for l in ex.lines)} units billed",
                           rec=", ".join(r["id"] for r in receipts) or "No receipt", bank="",
                           st="ok" if f["receipt_covers_invoice"] else "bad",
                           note="Everything billed was received." if f["receipt_covers_invoice"] else "Billed for more than the warehouse received.",
                           refs=[r["id"] for r in receipts]))
    elif ex.po_number:
        flags.add("po_not_found")
        checks.append(dict(key="po", label="Purchase order", inv=ex.po_number, rec="No such PO", bank="", st="bad",
                           note="The PO number on the invoice does not exist."))
    f["three_way"] = three_way

    # ---- duplicates ----
    f["duplicate_exact"] = False
    f["duplicate_near"] = False
    dup_ids: list[str] = []
    near_ids: list[str] = []
    if v is not None and ex.bill_no:
        n = norm_invoice_no(ex.bill_no)
        for r in golden.execute("SELECT id, vendor_invoice_no, total_cents, invoice_date FROM invoice WHERE vendor_id=? AND invoice_no_norm=?",
                                (v["id"], n)):
            dup_ids.append(r["id"])
        since = (dt.date.fromisoformat(bill_day[:10]) - dt.timedelta(days=180)).isoformat() if bill_day else "0000"
        for r in golden.execute("SELECT id, vendor_invoice_no, total_cents FROM invoice WHERE vendor_id=? AND total_cents=? AND invoice_date>=?",
                                (v["id"], ex.grand_total_cents, since)):
            if r["id"] not in dup_ids and similar_invoice_no(r["vendor_invoice_no"], ex.bill_no):
                near_ids.append(r["id"])
        if work is not None:
            for r in work.execute("SELECT id, extracted FROM work_item WHERE supplier=? AND id<>? AND state NOT IN ('REJECTED')",
                                  (v["id"], work_item_id or "")):
                e2 = json.loads(r["extracted"] or "{}")
                if norm_invoice_no(e2.get("bill_no", "")) == n:
                    dup_ids.append(r["id"])
                elif e2.get("grand_total_cents") == ex.grand_total_cents and similar_invoice_no(e2.get("bill_no", ""), ex.bill_no):
                    near_ids.append(r["id"])
    if dup_ids:
        f["duplicate_exact"] = True
        flags.add("duplicate_exact")
        evidence += [d for d in dup_ids if not d.startswith("WI-")]
    if near_ids:
        f["duplicate_near"] = True
        flags.add("duplicate_near")
        evidence += [d for d in near_ids if not d.startswith("WI-")]
    f["duplicate_of"] = dup_ids + near_ids
    checks.append(dict(key="duplicate", label="Duplicate check", inv=ex.bill_no or "(no number)",
                       rec=", ".join(dup_ids + near_ids) or "No match", bank="",
                       st="bad" if dup_ids else ("warn" if near_ids else "ok"),
                       note="Same invoice number as one already on file." if dup_ids else
                       ("Same amount and a near-identical invoice number as a recent bill." if near_ids else "No duplicate found."),
                       refs=[d for d in dup_ids + near_ids if not d.startswith("WI-")]))

    # ---- aggregates (used by learned policies, e.g. split bills) ----
    f["vendor_7d_total_cents"] = ex.grand_total_cents
    f["vendor_7d_count"] = 1
    f["same_po_bills_7d"] = 1
    f["same_po_7d_total_cents"] = ex.grand_total_cents
    if v is not None and bill_day:
        since7 = (dt.date.fromisoformat(bill_day[:10]) - dt.timedelta(days=7)).isoformat()
        g = golden.execute("SELECT COALESCE(SUM(total_cents),0), COUNT(*) FROM invoice WHERE vendor_id=? AND invoice_date>=?",
                           (v["id"], since7)).fetchone()
        f["vendor_7d_total_cents"] += g[0]
        f["vendor_7d_count"] += g[1]
        if po is not None:
            g2 = golden.execute("SELECT COUNT(*), COALESCE(SUM(total_cents),0) FROM invoice WHERE po_id=? AND invoice_date>=?",
                                (po["id"], since7)).fetchone()
            f["same_po_bills_7d"] += g2[0]
            f["same_po_7d_total_cents"] += g2[1]
        if work is not None:
            for r in work.execute("SELECT amount_cents, extracted FROM work_item WHERE supplier=? AND id<>? AND record_invoice IS NULL "
                                  "AND state NOT IN ('REJECTED','BLOCKED')", (v["id"], work_item_id or "")):
                e2 = json.loads(r["extracted"] or "{}")
                if (e2.get("bill_date") or "9999") >= since7:
                    f["vendor_7d_total_cents"] += r["amount_cents"]
                    f["vendor_7d_count"] += 1
                    if po is not None and e2.get("po_number", "").upper() == po["id"]:
                        f["same_po_bills_7d"] += 1
                        f["same_po_7d_total_cents"] += r["amount_cents"]

    # ---- untrusted text ----
    hits = injection_hits(doc.subject + "\n" + doc.body)
    f["injection_suspected"] = bool(hits)
    if hits:
        flags.add("injection")
        checks.append(dict(key="text", label="Text inside the email", inv="Contains instructions aimed at an AP system",
                           rec="No such instruction exists in policy", bank="n/a", st="bad",
                           note="Treated as data and ignored (G-05). Reported to the controller."))

    f["bill_date"] = bill_day
    f["entity_id"] = None
    if po is not None:
        f["entity_id"] = po["entity_id"]
    elif v is not None:
        ent = golden.execute("SELECT id FROM entity WHERE lower(name)=lower(?)", (ex.bill_to_company.strip(),)).fetchone()
        f["entity_id"] = ent["id"] if ent else None
    f["is_intercompany"] = "intercompany" in flags
    f["flags"] = sorted(flags)
    f["evidence"] = list(dict.fromkeys(evidence))
    f["checks"] = checks
    return f
