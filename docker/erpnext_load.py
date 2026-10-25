"""Load Harborline into the local ERPNext: one-time setup, then a 3-month slice of the generated record.

  uv run --project backend python docker/erpnext_load.py [--fresh]

--fresh drops and recreates the ERPNext site first (the Docker volumes), so the record holds only our data.
The generated SQLite record (var/record.db from `make seed`) is the source: same ids, same amounts. History before
SLICE_FROM stays out of ERPNext to keep seeding practical; open POs and receipts used by the eval and demo
documents are always loaded.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "docker"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=True)

from probation.ports.erpnext import COMPANIES, ErpnextClient, ErpnextError, _bank_doc, _ensure_bank, d  # noqa: E402
from probation.models import BankAccountRecord  # noqa: E402

SLICE_FROM = "2026-07-24"
COMPOSE = ["docker", "compose", "-p", "erpnext", "-f", str(REPO / "docker" / "erpnext-pwd.yml")]
PROMPT_DOCTYPES = ["Supplier", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry", "Bank Account"]
CUSTOM_FIELDS = {
    "Supplier": [("custom_email_domain", "Email domain", "Data"), ("custom_phone", "Phone on file", "Data"),
                 ("custom_created_at", "Vendor since", "Data")],
    "Bank Account": [("custom_routing_last4", "Routing last 4", "Data"), ("custom_account_last4", "Account last 4", "Data"),
                     ("custom_valid_from", "Valid from", "Date"), ("custom_valid_to", "Valid to", "Date"),
                     ("custom_verified_by", "Verified by", "Data"), ("custom_verified_at", "Verified at", "Date"),
                     ("custom_source", "Source", "Data")],
    "Purchase Order": [("custom_department", "Department", "Data"), ("custom_requested_by", "Requested by", "Data")],
    "Purchase Invoice": [("custom_source_doc", "Source document", "Data")],
    "Payment Entry": [("custom_bank_account_id", "Paid to bank account", "Data")],
    "Item": [("custom_group", "Purchasing group", "Data")],
}
NON_STOCK_GROUPS = {"Utilities", "Professional Services", "IT & Software", "Fuel & Fleet"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fresh_site() -> None:
    log("dropping and recreating the ERPNext site")
    subprocess.run(COMPOSE + ["down", "-v"], check=True, capture_output=True)
    subprocess.run(COMPOSE + ["up", "-d"], check=True, capture_output=True)
    while True:
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", "erpnext-create-site-1"],
                            capture_output=True, text=True).stdout.strip()
        if st.startswith("exited"):
            if not st.endswith(" 0"):
                sys.exit(f"create-site failed: {st}")
            break
        time.sleep(5)
    import erpnext_spike as S
    S.wait_up()
    S.login()
    S.setup_wizard()
    S.api_keys()
    load_dotenv(REPO / ".env", override=True)
    log("site ready, API key rotated into .env")


def setup(api: ErpnextClient) -> None:
    for dt_ in PROMPT_DOCTYPES:
        if not api.exists("Property Setter", f"{dt_}-main-autoname"):
            api.insert("Property Setter", {"doc_type": dt_, "doctype_or_field": "DocType", "property": "autoname",
                                           "value": "Prompt", "property_type": "Data"})
    for dt_, fields in CUSTOM_FIELDS.items():
        for fn, label, ft in fields:
            if not api.exists("Custom Field", f"{dt_}-{fn}"):
                api.insert("Custom Field", {"dt": dt_, "fieldname": fn, "label": label, "fieldtype": ft})
    # price and quantity differences are decided by our approval workflow, so ERPNext must accept an approved rate change
    for field, value in [("po_required", "No"), ("maintain_same_rate", 0), ("over_billing_allowance", 100)]:
        api.call("POST", "/api/method/frappe.client.set_value", {"doctype": "Buying Settings", "name": "Buying Settings",
                                                                 "fieldname": field, "value": value}, form=True)
    api.call("POST", "/api/method/frappe.client.set_value", {"doctype": "Accounts Settings", "name": "Accounts Settings",
                                                             "fieldname": "over_billing_allowance", "value": 100}, form=True)
    for abbr, name in COMPANIES.items():
        if not api.exists("Company", name):
            api.insert("Company", {"company_name": name, "abbr": abbr, "default_currency": "USD", "country": "United States",
                                   "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard"})
            log(f"created company {name}")
        for acct, parent, typ in [(f"Operating Bank - {abbr}", f"Bank Accounts - {abbr}", "Bank"),
                                  (f"Recoverable Tax - {abbr}", f"Duties and Taxes - {abbr}", "Tax")]:
            if not api.exists("Account", acct):
                api.insert("Account", {"account_name": acct.rsplit(" - ", 1)[0], "parent_account": parent, "company": name,
                                       "account_type": typ, "is_group": 0})
    log("setup done: naming, custom fields, 3 companies, bank and tax accounts")


_local = __import__("threading").local()
RECORD_DB: Path | None = None


def rows(_db, sql: str, *args) -> list[dict]:
    """One read-only SQLite connection per thread (a shared connection mixes up interleaved queries)."""
    db = getattr(_local, "db", None)
    if db is None:
        db = _local.db = sqlite3.connect(f"file:{RECORD_DB}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(sql, args)]


def parallel(fn, items, workers: int = 4, label: str = "") -> int:
    t = time.time()
    errs = []

    def safe(x):
        try:
            fn(x)
        except Exception as e:  # report and continue; a failed document is listed, not fatal
            errs.append(f"{type(e).__name__}: {e}")
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(safe, items))
    log(f"{label}: {len(items) - len(errs)} ok, {len(errs)} failed in {time.time() - t:.0f}s" + (f" · first error: {errs[0][:300]}" if errs else ""))
    return len(errs)


def load(api: ErpnextClient, record_db: Path) -> None:
    global RECORD_DB
    RECORD_DB = record_db
    db = None
    groups = sorted({r["supplier_group"] for r in rows(db, "SELECT DISTINCT supplier_group FROM supplier")})
    for g in groups:
        if not api.exists("Supplier Group", g):
            api.insert("Supplier Group", {"supplier_group_name": g, "parent_supplier_group": "All Supplier Groups"})
    for b in {r["bank_name"] for r in rows(db, "SELECT DISTINCT bank_name FROM supplier_bank_account")}:
        _ensure_bank(api, b)
    items = rows(db, "SELECT * FROM item")
    parallel(lambda i: api.exists("Item", i["item_code"]) or api.insert("Item", {
        "item_code": i["item_code"], "item_name": i["item_name"], "item_group": "All Item Groups", "stock_uom": "Nos",
        "is_stock_item": 0 if i["item_group"] in NON_STOCK_GROUPS else 1, "standard_rate": d(i["std_rate_cents"]),
        "custom_group": i["item_group"]}), items, label="items")
    sups = rows(db, "SELECT * FROM supplier ORDER BY name")

    def sup(s):
        if api.exists("Supplier", s["name"]):
            return
        doc = {"__newname": s["name"], "supplier_name": s["supplier_name"], "supplier_group": s["supplier_group"],
               "tax_id": s["tax_id"], "custom_email_domain": s["email_domain"], "custom_phone": s["phone"],
               "custom_created_at": s["created_at"]}
        if s["represents_company"]:
            doc.update(is_internal_supplier=1, represents_company=COMPANIES[s["represents_company"]],
                       companies=[{"company": COMPANIES["HPI"]}])
        api.insert("Supplier", doc)
    parallel(sup, sups, label="suppliers")
    banks = rows(db, "SELECT * FROM supplier_bank_account ORDER BY name")
    parallel(lambda b: api.exists("Bank Account", b["name"]) or api.insert("Bank Account", _bank_doc(BankAccountRecord(**b))),
             banks, label="supplier bank accounts")

    invs = rows(db, "SELECT * FROM purchase_invoice WHERE bill_date>=? ORDER BY name", SLICE_FROM)
    po_names = {i["purchase_order"] for i in invs if i["purchase_order"]} | {r["name"] for r in rows(db, "SELECT name FROM purchase_order WHERE status='to_bill'")}
    pos = [p for p in rows(db, "SELECT * FROM purchase_order ORDER BY name") if p["name"] in po_names]
    po_rows: dict[str, dict[str, str]] = {}

    def po(p):
        lines = rows(db, "SELECT * FROM purchase_order_item WHERE parent=? ORDER BY idx", p["name"])
        sched = (dt.date.fromisoformat(p["transaction_date"]) + dt.timedelta(days=2)).isoformat()
        wh = f"Stores - {p['company']}"
        out = api.insert("Purchase Order", {
            "__newname": p["name"], "supplier": p["supplier"], "company": COMPANIES[p["company"]], "transaction_date": p["transaction_date"],
            "schedule_date": sched, "custom_department": p["department"], "custom_requested_by": p["requested_by"],
            "items": [{"item_code": l["item_code"], "qty": l["qty"], "rate": d(l["rate_cents"]), "schedule_date": sched, "warehouse": wh,
                       "description": l["description"]} for l in lines],
            "taxes": _taxes(p["company"], p["grand_total_cents"] - sum(l["amount_cents"] for l in lines))}, submit=True)
        po_rows[p["name"]] = {i["item_code"]: i["name"] for i in out["items"]}
    parallel(po, pos, label="purchase orders")

    prs = [r for r in rows(db, "SELECT * FROM purchase_receipt ORDER BY name") if r["purchase_order"] in po_rows]
    pr_rows: dict[str, tuple[str, dict[str, str]]] = {}

    def pr(r):
        lines = rows(db, "SELECT * FROM purchase_receipt_item WHERE parent=? ORDER BY idx", r["name"])
        pol = rows(db, "SELECT * FROM purchase_order_item WHERE parent=?", r["purchase_order"])
        rate = {l["item_code"]: l["rate_cents"] for l in pol}
        wh = f"Stores - {r['company']}"
        out = api.insert("Purchase Receipt", {
            "__newname": r["name"], "supplier": r["supplier"], "company": COMPANIES[r["company"]], "posting_date": r["posting_date"],
            "set_posting_time": 1,
            "items": [{"item_code": l["item_code"], "qty": l["received_qty"], "rate": d(rate.get(l["item_code"], 0)), "warehouse": wh,
                       "purchase_order": r["purchase_order"], "purchase_order_item": po_rows[r["purchase_order"]].get(l["item_code"])}
                      for l in lines]}, submit=True)
        pr_rows[r["purchase_order"]] = (out["name"], {i["item_code"]: i["name"] for i in out["items"]})
    parallel(pr, prs, label="purchase receipts")

    def pinv(v):
        lines = rows(db, "SELECT * FROM purchase_invoice_item WHERE parent=? ORDER BY idx", v["name"])
        po_name = v["purchase_order"]
        items = []
        for l in lines:
            row = {"item_code": l["item_code"], "qty": l["qty"], "rate": d(l["rate_cents"]), "description": l["description"]}
            if po_name and po_name in po_rows:
                row.update(purchase_order=po_name, po_detail=po_rows[po_name].get(l["item_code"]))
                if po_name in pr_rows:
                    row.update(purchase_receipt=pr_rows[po_name][0], pr_detail=pr_rows[po_name][1].get(l["item_code"]))
            items.append(row)
        api.insert("Purchase Invoice", {
            "__newname": v["name"], "supplier": v["supplier"], "company": COMPANIES[v["company"]], "bill_no": v["bill_no"],
            "bill_date": v["bill_date"], "posting_date": v["posting_date"], "set_posting_time": 1, "due_date": v["posting_date"],
            "custom_source_doc": v["source_doc"], "update_stock": 0, "items": items, "taxes": _taxes(v["company"], v["tax_cents"])},
            submit=True)
    parallel(pinv, invs, label="purchase invoices")

    inv_names = {v["name"] for v in invs}
    pays = [p for p in rows(db, "SELECT * FROM payment_entry ORDER BY name") if p["reference_invoice"] in inv_names]
    payable = {abbr: api.get("Company", name)["default_payable_account"] for abbr, name in COMPANIES.items()}
    parallel(lambda p: api.insert("Payment Entry", {
        "__newname": p["name"], "payment_type": "Pay", "company": COMPANIES[p["company"]], "posting_date": p["posting_date"],
        "party_type": "Supplier", "party": p["party"], "paid_from": f"Operating Bank - {p['company']}", "paid_to": payable[p["company"]],
        "paid_amount": d(p["paid_amount_cents"]), "received_amount": d(p["paid_amount_cents"]), "reference_no": p["transfer_id"],
        "reference_date": p["posting_date"], "custom_bank_account_id": p["bank_account"],
        "references": [{"reference_doctype": "Purchase Invoice", "reference_name": p["reference_invoice"],
                        "allocated_amount": d(p["paid_amount_cents"])}]}, submit=True), pays, label="payment entries")


def _taxes(abbr: str, tax_cents: int) -> list[dict]:
    return [{"charge_type": "Actual", "account_head": f"Recoverable Tax - {abbr}", "description": "Sales tax",
             "tax_amount": d(tax_cents)}] if tax_cents else []


def main() -> None:
    t0 = time.time()
    if "--fresh" in sys.argv:
        fresh_site()
    import os
    api = ErpnextClient(key=os.environ["ERPNEXT_API_KEY"], secret=os.environ["ERPNEXT_API_SECRET"])
    setup(api)
    load(api, REPO / "var" / "pristine" / "record.db")
    log(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
