"""Replay cases for policy backtests, built only from runtime-visible outcomes (never from data/labels):
recent paid history (good), and work items a human approved (good) or rejected / reported in an incident (bad)."""
from __future__ import annotations

import json

BASE = {"vendor_status": "known", "po_match": True, "price_delta_pct": 0.0, "qty_over_po": False, "receipt_covers_invoice": True,
        "bank_changed": False, "sender_domain_on_file": True, "sender_lookalike": False, "tax_id_match": True,
        "arithmetic_ok": True, "duplicate_exact": False, "duplicate_near": False, "injection_suspected": False}


def history_cases(gw, days: int = 45, limit: int = 3000) -> list[dict]:
    g = gw.golden
    since = g.execute("SELECT date(MAX(invoice_date), ?) FROM invoice WHERE source_doc='history'", (f"-{days} days",)).fetchone()[0]
    rows = g.execute("""
        SELECT i.id, i.vendor_id, i.po_id, i.total_cents, i.invoice_date, v.vendor_group, v.is_intercompany,
          (SELECT COUNT(*) FROM invoice j WHERE j.vendor_id=i.vendor_id AND j.invoice_date BETWEEN date(i.invoice_date,'-7 days') AND i.invoice_date) AS v7n,
          (SELECT SUM(total_cents) FROM invoice j WHERE j.vendor_id=i.vendor_id AND j.invoice_date BETWEEN date(i.invoice_date,'-7 days') AND i.invoice_date) AS v7t,
          (SELECT COUNT(*) FROM invoice j WHERE i.po_id IS NOT NULL AND j.po_id=i.po_id AND j.invoice_date BETWEEN date(i.invoice_date,'-7 days') AND i.invoice_date) AS p7n,
          (SELECT SUM(total_cents) FROM invoice j WHERE i.po_id IS NOT NULL AND j.po_id=i.po_id AND j.invoice_date BETWEEN date(i.invoice_date,'-7 days') AND i.invoice_date) AS p7t
        FROM invoice i JOIN vendor v ON v.id=i.vendor_id
        WHERE i.source_doc='history' AND i.invoice_date>=? ORDER BY i.invoice_date DESC LIMIT ?""", (since, limit)).fetchall()
    cases = []
    for r in rows:
        f = dict(BASE, amount_cents=r["total_cents"], po_found=r["po_id"] is not None, vendor_group=r["vendor_group"],
                 is_intercompany=bool(r["is_intercompany"]), vendor_7d_count=r["v7n"], vendor_7d_total_cents=r["v7t"] or 0,
                 same_po_bills_7d=max(1, r["p7n"]), same_po_7d_total_cents=r["p7t"] or r["total_cents"])
        if r["is_intercompany"]:
            f["vendor_status"] = "intercompany"
        cases.append({"features": f, "bad": False, "ref": r["id"]})
    cases += decided_cases(gw)
    return cases


def decided_cases(gw) -> list[dict]:
    bad_items = {r[0] for r in gw.work.execute("SELECT work_item FROM incident WHERE work_item IS NOT NULL")}
    out = []
    for r in gw.work.execute("SELECT id, state, routing FROM work_item WHERE state IN ('POSTED','REJECTED') AND routing IS NOT NULL"):
        routing = json.loads(r["routing"])
        f = (gw.item(r["id"])["facts"]) or {}
        human_rejected = r["state"] == "REJECTED"
        out.append({"features": f, "bad": human_rejected or r["id"] in bad_items, "ref": r["id"]})
        _ = routing
    return out
