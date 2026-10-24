"""A realistic future inbox for fast-forward, generated at runtime from the vendor file (no labels involved).

Simulated buyers raise POs and the warehouse posts goods receipts directly in the record, as ERP users would.
All AP decisions and payments still go through the gateway.
"""
from __future__ import annotations

import random
import re
import sys

from probation.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "data" / "generate"))

# per-document probability of each problem in a normal week (the rest are clean)
MIX = [("clean", 0.86), ("po_price_mismatch", 0.04), ("duplicate_near", 0.02), ("receipt_shortfall", 0.02),
       ("po_qty_mismatch", 0.015), ("bank_change", 0.01), ("new_vendor_legit", 0.015), ("tax_id_mismatch", 0.01),
       ("amount_tamper", 0.01)]


def _specs(gw):
    from company import VendorSpec
    out = []
    for v in gw.golden.execute("SELECT * FROM vendor WHERE is_intercompany=0 AND status='active'"):
        ent = gw.golden.execute("SELECT entity_id, COUNT(*) c FROM invoice WHERE vendor_id=? GROUP BY entity_id ORDER BY c DESC LIMIT 1",
                                (v["id"],)).fetchone()
        n = gw.golden.execute("SELECT COUNT(*) FROM invoice WHERE vendor_id=?", (v["id"],)).fetchone()[0]
        if not ent or n < 3:
            continue
        banks = [dict(bank_name=b["bank_name"], routing_last4=b["routing_last4"], account_last4=b["account_last4"],
                      valid_from=b["valid_from"], valid_to=b["valid_to"], source=b["source"], sba=b["id"])
                 for b in gw.golden.execute("SELECT * FROM vendor_bank_account WHERE vendor_id=? ORDER BY valid_from", (v["id"],))]
        out.append(VendorSpec(sid=v["id"], name=v["name"], group=v["vendor_group"], entity=ent[0], domain=v["email_domain"],
                              tax_id=v["tax_id"], phone=v["phone"], created_at=v["created_at"], weight=float(n), bank=banks))
    return out


def _formatter(gw, sid: str, rng: random.Random):
    last = gw.golden.execute("SELECT vendor_invoice_no FROM invoice WHERE vendor_id=? ORDER BY invoice_date DESC, id DESC LIMIT 1",
                             (sid,)).fetchone()
    base = last[0] if last else "INV-1000"
    m = re.search(r"(\d+)(?!.*\d)", base)
    state = {"n": int(m.group(1)) + 50 if m else 5000, "pre": base[:m.start()] if m else "INV-", "w": len(m.group(1)) if m else 4}

    def nxt() -> str:
        state["n"] += rng.randint(1, 3)
        return f"{state['pre']}{state['n']:0{state['w']}d}"
    return nxt


def week_docs(gw, n: int, seed: int) -> list:
    import history
    from history import Counters
    from invoices import Planter
    from probation.models import InboxDoc
    rng = random.Random(seed)
    specs = _specs(gw)
    history.TODAY = __import__("datetime").date.fromisoformat(gw.clock.today())
    import invoices
    invoices.TODAY = history.TODAY
    ctr = Counters()
    ctr.po = int(gw.record.db.execute("SELECT MAX(CAST(SUBSTR(name, 4) AS INTEGER)) FROM purchase_order").fetchone()[0])
    ctr.pr = int(gw.record.db.execute("SELECT MAX(CAST(SUBSTR(name, 4) AS INTEGER)) FROM purchase_receipt").fetchone()[0])
    fmt = {}
    p = Planter(gw.record, specs, rng, ctr, fmt)
    weights = [s.weight for s in specs]
    kinds, probs = zip(*MIX)
    for _ in range(n):
        v = rng.choices(specs, weights)[0]
        if v.sid not in fmt:
            fmt[v.sid] = _formatter(gw, v.sid, rng)
        kind = rng.choices(kinds, probs)[0]
        try:
            if kind == "clean" or v.group in ("Utilities", "Professional Services", "IT & Software", "Fuel & Fleet"):
                p.clean(v, "sim")
            else:
                getattr(p, kind)(v, "sim")
        except Exception:
            p.clean(v, "sim")
    gw.record.commit()
    gw.resync()
    docs = []
    for d in p.docs:
        d = dict(d, id=gw.new_doc_id("DOC-S"), received_at=gw.clock.now())
        docs.append(InboxDoc(**{k: d[k] for k in ("id", "received_at", "channel", "from_addr", "subject", "body", "attachments")}))
    return docs
