"""Record -> golden.db. Raw rows are kept as-is, standardised, then upserted into typed golden tables.

Ids are the record's own names, so a re-sync never duplicates. Every golden row keeps raw_ref.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from probation.models import RawRow

GOLDEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_cursor(source TEXT PRIMARY KEY, cursor TEXT, rows_total INTEGER);
CREATE TABLE IF NOT EXISTS raw_row(seq INTEGER PRIMARY KEY, doctype TEXT, name TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS entity(id TEXT PRIMARY KEY, name TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS item(id TEXT PRIMARY KEY, name TEXT, item_group TEXT, uom TEXT, std_rate_cents INTEGER, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS vendor(id TEXT PRIMARY KEY, name TEXT, name_norm TEXT, vendor_group TEXT, tax_id TEXT,
  email_domain TEXT, phone TEXT, status TEXT, created_at TEXT, is_intercompany INTEGER, represents_entity TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS vendor_bank_account(id TEXT PRIMARY KEY, vendor_id TEXT, bank_name TEXT, routing_last4 TEXT,
  account_last4 TEXT, valid_from TEXT, valid_to TEXT, verified_by TEXT, verified_at TEXT, source TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS purchase_order(id TEXT PRIMARY KEY, vendor_id TEXT, entity_id TEXT, order_date TEXT,
  department TEXT, requested_by TEXT, total_cents INTEGER, status TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS po_line(po_id TEXT, line_no INTEGER, item_code TEXT, description TEXT, qty INTEGER,
  rate_cents INTEGER, amount_cents INTEGER, PRIMARY KEY(po_id, line_no));
CREATE TABLE IF NOT EXISTS goods_receipt(id TEXT PRIMARY KEY, vendor_id TEXT, entity_id TEXT, received_date TEXT,
  po_id TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS receipt_line(receipt_id TEXT, line_no INTEGER, item_code TEXT, received_qty INTEGER,
  PRIMARY KEY(receipt_id, line_no));
CREATE TABLE IF NOT EXISTS invoice(id TEXT PRIMARY KEY, vendor_id TEXT, entity_id TEXT, vendor_invoice_no TEXT,
  invoice_no_norm TEXT, invoice_date TEXT, posted_date TEXT, po_id TEXT, net_cents INTEGER, tax_cents INTEGER,
  total_cents INTEGER, status TEXT, source_doc TEXT, raw_ref TEXT);
CREATE TABLE IF NOT EXISTS invoice_line(invoice_id TEXT, line_no INTEGER, item_code TEXT, description TEXT, qty INTEGER,
  rate_cents INTEGER, amount_cents INTEGER, PRIMARY KEY(invoice_id, line_no));
CREATE TABLE IF NOT EXISTS payment(id TEXT PRIMARY KEY, entity_id TEXT, vendor_id TEXT, amount_cents INTEGER,
  paid_date TEXT, invoice_id TEXT, bank_account_id TEXT, transfer_id TEXT, raw_ref TEXT);
CREATE INDEX IF NOT EXISTS g_inv_vendor ON invoice(vendor_id, invoice_date);
CREATE INDEX IF NOT EXISTS g_inv_norm ON invoice(invoice_no_norm);
CREATE INDEX IF NOT EXISTS g_inv_po ON invoice(po_id);
CREATE INDEX IF NOT EXISTS g_pay_vendor ON payment(vendor_id);
CREATE INDEX IF NOT EXISTS g_pay_inv ON payment(invoice_id);
CREATE INDEX IF NOT EXISTS g_gr_po ON goods_receipt(po_id);
CREATE INDEX IF NOT EXISTS g_vba_vendor ON vendor_bank_account(vendor_id);
"""


def norm_invoice_no(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(inc|llc|co|corp|ltd|the|and|group|sons)\b", " ", s)
    return " ".join(s.split())


def _ref(r: RawRow) -> str:
    return f"record:{r.doctype}:{r.name}"


def standardise(r: RawRow) -> list[tuple[str, dict]]:
    """Map one raw row to (golden_table, row) pairs. Child rows come back as their own tables."""
    d = r.data
    ref = _ref(r)
    t = r.doctype
    if t == "company":
        return [("entity", dict(id=d["name"], name=d["company_name"], raw_ref=ref))]
    if t == "item":
        return [("item", dict(id=d["item_code"], name=d["item_name"], item_group=d["item_group"], uom=d["uom"],
                              std_rate_cents=d["std_rate_cents"], raw_ref=ref))]
    if t == "supplier":
        return [("vendor", dict(id=d["name"], name=d["supplier_name"], name_norm=norm_name(d["supplier_name"]),
                                vendor_group=d["supplier_group"], tax_id=(d["tax_id"] or "").strip(),
                                email_domain=(d["email_domain"] or "").lower().strip(), phone=d["phone"], status=d["status"],
                                created_at=d["created_at"], is_intercompany=int(bool(d.get("represents_company"))),
                                represents_entity=d.get("represents_company"), raw_ref=ref))]
    if t == "supplier_bank_account":
        return [("vendor_bank_account", dict(id=d["name"], vendor_id=d["supplier"], bank_name=d["bank_name"],
                                             routing_last4=d["routing_last4"], account_last4=d["account_last4"],
                                             valid_from=d["valid_from"], valid_to=d["valid_to"], verified_by=d["verified_by"],
                                             verified_at=d["verified_at"], source=d["source"], raw_ref=ref))]
    if t == "purchase_order":
        out = [("purchase_order", dict(id=d["name"], vendor_id=d["supplier"], entity_id=d["company"],
                                       order_date=d["transaction_date"], department=d["department"],
                                       requested_by=d["requested_by"], total_cents=d["grand_total_cents"],
                                       status=d["status"], raw_ref=ref))]
        out += [("po_line", dict(po_id=d["name"], line_no=i["idx"], item_code=i["item_code"], description=i["description"],
                                 qty=i["qty"], rate_cents=i["rate_cents"], amount_cents=i["amount_cents"])) for i in d["items"]]
        return out
    if t == "purchase_receipt":
        out = [("goods_receipt", dict(id=d["name"], vendor_id=d["supplier"], entity_id=d["company"],
                                      received_date=d["posting_date"], po_id=d["purchase_order"], raw_ref=ref))]
        out += [("receipt_line", dict(receipt_id=d["name"], line_no=i["idx"], item_code=i["item_code"],
                                      received_qty=i["received_qty"])) for i in d["items"]]
        return out
    if t == "purchase_invoice":
        out = [("invoice", dict(id=d["name"], vendor_id=d["supplier"], entity_id=d["company"], vendor_invoice_no=d["bill_no"],
                                invoice_no_norm=norm_invoice_no(d["bill_no"]), invoice_date=d["bill_date"],
                                posted_date=d["posting_date"], po_id=d["purchase_order"], net_cents=d["net_total_cents"],
                                tax_cents=d["tax_cents"], total_cents=d["grand_total_cents"], status=d["status"],
                                source_doc=d["source_doc"], raw_ref=ref))]
        out += [("invoice_line", dict(invoice_id=d["name"], line_no=i["idx"], item_code=i["item_code"],
                                      description=i["description"], qty=i["qty"], rate_cents=i["rate_cents"],
                                      amount_cents=i["amount_cents"])) for i in d["items"]]
        return out
    if t == "payment_entry":
        return [("payment", dict(id=d["name"], entity_id=d["company"], vendor_id=d["party"], amount_cents=d["paid_amount_cents"],
                                 paid_date=d["posting_date"], invoice_id=d["reference_invoice"],
                                 bank_account_id=d["bank_account"], transfer_id=d["transfer_id"], raw_ref=ref))]
    return []


CHILD_PARENT_KEY = {"po_line": "po_id", "receipt_line": "receipt_id", "invoice_line": "invoice_id"}


def open_golden(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(GOLDEN_SCHEMA)
    return db


def run_sync(record, golden_path: Path, source: str = "record") -> dict:
    """Incremental: reads changes since the stored cursor. Safe to run repeatedly."""
    db = open_golden(golden_path)
    row = db.execute("SELECT cursor FROM sync_cursor WHERE source=?", (source,)).fetchone()
    cursor = row[0] if row else "0"
    total = 0
    while True:
        rows, new_cursor = record.changes_since(cursor)
        if not rows:
            break
        with db:
            for r in rows:
                db.execute("INSERT OR REPLACE INTO raw_row VALUES(?,?,?,?)", (r.seq, r.doctype, r.name, json.dumps(r.data)))
                pairs = standardise(r)
                cleared: set[str] = set()
                for table, g in pairs:
                    if table in CHILD_PARENT_KEY and table not in cleared:
                        db.execute(f"DELETE FROM {table} WHERE {CHILD_PARENT_KEY[table]}=?", (g[CHILD_PARENT_KEY[table]],))
                        cleared.add(table)
                    cols = ",".join(g)
                    db.execute(f"INSERT OR REPLACE INTO {table}({cols}) VALUES({','.join('?' for _ in g)})", list(g.values()))
            total += len(rows)
            cursor = new_cursor
            db.execute("INSERT OR REPLACE INTO sync_cursor VALUES(?,?,COALESCE((SELECT rows_total FROM sync_cursor WHERE source=?),0)+?)",
                       (source, cursor, source, len(rows)))
    db.close()
    return {"synced": total, "cursor": cursor}


if __name__ == "__main__":
    from probation.config import load_settings
    from probation.ports.sqlite import SqliteRecord
    s = load_settings()
    print(run_sync(SqliteRecord(s.record_db), s.golden_db))
