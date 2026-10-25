"""ERPNext system of record over its REST API (local Docker, see docker/).

Our ids are kept as ERPNext document names (autoname set to Prompt by docker/erpnext_load.py), so a Purchase Order is
PO-19093 in ERPNext, in golden.db and in the vendor's email alike. changes_since() translates ERPNext documents into the
same row shapes as the SQLite adapter, so the sync job and everything after it are unchanged.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from probation.models import BankAccountRecord, Cents, Company, PaymentEntry, PurchaseInvoice, PurchaseOrder, PurchaseReceipt, RawRow, Supplier

COMPANIES = {"HPI": "Harborline Provisions Inc.", "HCC": "Harborline Cold Chain LLC", "HFM": "Harborline Fresh Markets LLC"}
ABBR = {v: k for k, v in COMPANIES.items()}
# ERPNext doctype -> (our record table, child table fieldname or None)
DOCTYPES = [("Company", "company"), ("Item", "item"), ("Supplier", "supplier"), ("Bank Account", "supplier_bank_account"),
            ("Purchase Order", "purchase_order"), ("Purchase Receipt", "purchase_receipt"),
            ("Purchase Invoice", "purchase_invoice"), ("Payment Entry", "payment_entry")]


def c(x) -> int:
    return int(round(float(x or 0) * 100))


def d(x: int) -> float:
    return round(x / 100, 2)


class ErpnextError(RuntimeError):
    pass


class ErpnextClient:
    def __init__(self, base: str | None = None, key: str | None = None, secret: str | None = None):
        self.base = (base or os.environ.get("ERPNEXT_URL", "http://localhost:8080")).rstrip("/")
        self.key = key or os.environ.get("ERPNEXT_API_KEY", "")
        self.secret = secret or os.environ.get("ERPNEXT_API_SECRET", "")

    def call(self, method: str, path: str, data: dict | None = None, form: bool = False, timeout: int = 120) -> dict:
        headers = {"Accept": "application/json", "Authorization": f"token {self.key}:{self.secret}"}
        body = None
        if data is not None:
            if form:
                body = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data, default=str).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            try:
                j = json.loads(txt)
                msg = j.get("exception") or j.get("_server_messages") or j.get("message") or txt[:400]
            except ValueError:
                msg = txt[:400]
            raise ErpnextError(f"{method} {path.split('?')[0]} -> {e.code}: {str(msg)[:600]}") from None

    def get(self, doctype: str, name: str) -> dict:
        return self.call("GET", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")["data"]

    def exists(self, doctype: str, name: str) -> bool:
        try:
            self.get(doctype, name)
            return True
        except ErpnextError:
            return False

    def retry(self, fn, tries: int = 6):
        """ERPNext updates shared rows (e.g. an Item's last purchase rate) on submit; parallel writers can deadlock."""
        import random
        import time
        for i in range(tries):
            try:
                return fn()
            except ErpnextError as e:
                if ("Deadlock" not in str(e) and "Record has changed" not in str(e) and "TimestampMismatch" not in str(e)) or i == tries - 1:
                    raise
                time.sleep(0.3 * (i + 1) + random.random() * 0.5)

    def submit(self, doctype: str, name: str) -> dict:
        return self.retry(lambda: self.call("PUT", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}",
                                            {"docstatus": 1})["data"])

    def insert(self, doctype: str, doc: dict, submit: bool = False) -> dict:
        name = doc.get("__newname")
        if name and self.exists(doctype, name):
            out = self.get(doctype, name)          # left behind by an earlier, interrupted attempt
        else:
            out = self.retry(lambda: self.call("POST", f"/api/resource/{urllib.parse.quote(doctype)}", doc)["data"])
        if submit and out.get("docstatus") == 0:
            out = self.submit(doctype, out["name"])
        return out

    def update(self, doctype: str, name: str, patch: dict) -> dict:
        return self.call("PUT", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}", patch)["data"]

    def list(self, doctype: str, filters=None, fields=None, order_by: str | None = None, limit: int = 0, start: int = 0) -> list[dict]:
        q = {"limit_page_length": limit or 0, "limit_start": start}
        if filters:
            q["filters"] = json.dumps(filters)
        if fields:
            q["fields"] = json.dumps(fields)
        if order_by:
            q["order_by"] = order_by
        return self.call("GET", f"/api/resource/{urllib.parse.quote(doctype)}?" + urllib.parse.urlencode(q))["data"]


class ErpnextRecord:
    """RecordPort over ERPNext. Writable: construct only in the gateway and the loader."""

    def __init__(self, client: ErpnextClient | None = None):
        self.api = client or ErpnextClient()
        self.db = None  # no local handle; Gateway.close() tolerates this

    # ---------- reads ----------
    def get_company(self, name: str) -> Company:
        co = self.api.get("Company", COMPANIES.get(name, name))
        return Company(name=co["abbr"], company_name=co["name"], bank_account=f"SIM-OPS-{co['abbr']}-01",
                       ap_account=co["default_payable_account"], bank_gl_account=f"Operating Bank - {co['abbr']}")

    def get_supplier(self, supplier_id: str) -> Supplier:
        return Supplier(**_supplier(self.api.get("Supplier", supplier_id)))

    def bank_history(self, supplier_id: str) -> list[BankAccountRecord]:
        rows = self.api.list("Bank Account", [["party_type", "=", "Supplier"], ["party", "=", supplier_id]], ["*"], "custom_valid_from asc")
        return [BankAccountRecord(**_bank(r)) for r in rows]

    def get_po(self, po_id: str) -> PurchaseOrder:
        return PurchaseOrder(**_po(self.api.get("Purchase Order", po_id)))

    def get_receipts(self, po_id: str) -> list[PurchaseReceipt]:
        names = {r["name"] for r in self.api.list("Purchase Receipt", [["Purchase Receipt Item", "purchase_order", "=", po_id],
                                                                       ["docstatus", "=", 1]], ["name"])}
        return [PurchaseReceipt(**_pr(self.api.get("Purchase Receipt", n))) for n in sorted(names)]

    def changes_since(self, cursor: str, limit: int = 2000) -> tuple[list[RawRow], str]:
        """cursor is '<modified timestamp>' across all doctypes; returns full documents changed after it."""
        start = cursor if cursor and cursor != "0" else "1970-01-01 00:00:00"
        batch: list[tuple[str, str, str]] = []
        for dt, _table in DOCTYPES:
            filters = [["modified", ">", start]]
            if dt in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"):
                filters.append(["docstatus", "=", 1])
            if dt == "Bank Account":
                filters.append(["party_type", "=", "Supplier"])
            if dt == "Company":
                filters = [["modified", ">", start]]
            for r in self.api.list(dt, filters, ["name", "modified"], "modified asc", limit):
                batch.append((r["modified"], dt, r["name"]))
        batch.sort()
        batch = batch[:limit]
        if not batch:
            return [], cursor
        # move the cursor only past timestamps we have fully read (all docs with the last timestamp are included)
        last = batch[-1][0]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex:
            docs = list(ex.map(lambda b: self.api.get(b[1], b[2]), batch))
        out: list[RawRow] = []
        for seq, ((modified, dt, _name), doc) in enumerate(zip(batch, docs), 1):
            data = TRANSLATE[dt](doc)
            if data is None:
                continue
            out.append(RawRow(seq=_seq(modified, seq), doctype=dict(DOCTYPES)[dt], name=data.get("name") or data.get("item_code"), data=data))
        return out, last

    def gl_totals(self) -> tuple[int, int]:
        rows = self.api.list("GL Entry", [["is_cancelled", "=", 0]], ["sum(debit) as d", "sum(credit) as c"])
        return c(rows[0]["d"]), c(rows[0]["c"])

    def next_name(self, table: str, prefix: str, width: int = 6) -> str:
        dt = {"purchase_invoice": "Purchase Invoice", "payment_entry": "Payment Entry", "supplier_bank_account": "Bank Account",
              "purchase_order": "Purchase Order", "purchase_receipt": "Purchase Receipt"}[table]
        rows = self.api.list(dt, [["name", "like", f"{prefix}%"]], ["name"], "name desc", 1)
        n = int(rows[0]["name"][len(prefix):]) if rows else 0
        return f"{prefix}{n + 1:0{width}d}"

    def commit(self) -> None:
        pass

    # ---------- gateway-only writes ----------
    def post_purchase_invoice(self, inv: PurchaseInvoice, commit: bool = True) -> str:
        items = []
        po_items: dict[str, dict] = {}
        pr_items: dict[str, tuple[str, str]] = {}
        if inv.purchase_order:
            po = self.api.get("Purchase Order", inv.purchase_order)
            po_items = {i["item_code"]: i for i in po["items"]}
            for pr in self.get_receipts(inv.purchase_order):
                full = self.api.get("Purchase Receipt", pr.name)
                for i in full["items"]:
                    pr_items.setdefault(i["item_code"], (full["name"], i["name"]))
        for l in inv.items:
            row = {"item_code": l.item_code or _fallback_item(inv), "qty": l.qty, "rate": d(l.rate_cents), "description": l.description}
            if l.item_code in po_items:
                row.update(purchase_order=inv.purchase_order, po_detail=po_items[l.item_code]["name"])
            if l.item_code in pr_items:
                row.update(purchase_receipt=pr_items[l.item_code][0], pr_detail=pr_items[l.item_code][1])
            items.append(row)
        abbr = inv.company
        doc = {"__newname": inv.name, "supplier": inv.supplier, "company": COMPANIES[abbr], "bill_no": inv.bill_no,
               "bill_date": inv.bill_date, "posting_date": inv.posting_date, "set_posting_time": 1, "due_date": inv.posting_date,
               "custom_source_doc": inv.source_doc, "items": items, "update_stock": 0}
        if inv.tax_cents:
            doc["taxes"] = [{"charge_type": "Actual", "account_head": f"Recoverable Tax - {abbr}", "description": "Sales tax",
                             "tax_amount": d(inv.tax_cents)}]
        out = self.api.insert("Purchase Invoice", doc, submit=True)
        if c(out["grand_total"]) != inv.grand_total_cents:
            raise ErpnextError(f"ERPNext total {out['grand_total']} differs from {inv.grand_total_cents / 100}")
        return out["name"]

    def post_payment_entry(self, pay: PaymentEntry, commit: bool = True) -> str:
        abbr = pay.company
        co = self.api.get("Company", COMPANIES[abbr])
        out = self.api.insert("Payment Entry", {
            "__newname": pay.name, "payment_type": "Pay", "company": co["name"], "posting_date": pay.posting_date,
            "party_type": "Supplier", "party": pay.party, "paid_from": f"Operating Bank - {abbr}", "paid_to": co["default_payable_account"],
            "paid_amount": d(pay.paid_amount_cents), "received_amount": d(pay.paid_amount_cents), "reference_no": pay.transfer_id,
            "reference_date": pay.posting_date, "custom_bank_account_id": pay.bank_account,
            "references": [{"reference_doctype": "Purchase Invoice", "reference_name": pay.reference_invoice,
                            "allocated_amount": d(pay.paid_amount_cents)}]}, submit=True)
        return out["name"]

    def add_bank_account(self, acct: BankAccountRecord, commit: bool = True) -> str:
        for r in self.api.list("Bank Account", [["party", "=", acct.supplier], ["custom_valid_to", "is", "not set"]], ["name"]):
            self.api.update("Bank Account", r["name"], {"custom_valid_to": acct.valid_from})
        _ensure_bank(self.api, acct.bank_name)
        self.api.insert("Bank Account", _bank_doc(acct))
        return acct.name

    def set_supplier_status(self, supplier_id: str, status: str, commit: bool = True) -> None:
        self.api.update("Supplier", supplier_id, {"on_hold": 1 if status == "frozen" else 0, "hold_type": "All" if status == "frozen" else ""})


# ---------------------------------------------------------------- translation (ERPNext doc -> SQLite row shape)
def _seq(modified: str, i: int) -> int:
    digits = "".join(ch for ch in modified if ch.isdigit())[:14]
    return int(digits or 0) * 1000 + (i % 1000)


def _fallback_item(inv: PurchaseInvoice) -> str:
    return "LOG-003"


def _supplier(s: dict) -> dict:
    return {"name": s["name"], "supplier_name": s["supplier_name"], "supplier_group": s["supplier_group"], "tax_id": s.get("tax_id") or "",
            "email_domain": s.get("custom_email_domain") or "", "phone": s.get("custom_phone") or "",
            "status": "frozen" if s.get("on_hold") else ("inactive" if s.get("disabled") else "active"),
            "created_at": s.get("custom_created_at") or (s.get("creation") or "")[:10],
            "represents_company": ABBR.get(s.get("represents_company") or "") if s.get("is_internal_supplier") else None}


def _bank(b: dict) -> dict:
    return {"name": b.get("account_name") or b["name"], "supplier": b["party"], "bank_name": b["bank"],
            "routing_last4": b.get("custom_routing_last4") or "", "account_last4": b.get("custom_account_last4") or "",
            "valid_from": str(b.get("custom_valid_from") or ""), "valid_to": str(b["custom_valid_to"]) if b.get("custom_valid_to") else None,
            "verified_by": b.get("custom_verified_by"), "verified_at": str(b["custom_verified_at"]) if b.get("custom_verified_at") else None,
            "source": b.get("custom_source") or "onboarding"}


def _bank_doc(a: BankAccountRecord) -> dict:
    return {"__newname": a.name, "account_name": a.name, "bank": a.bank_name, "party_type": "Supplier", "party": a.supplier,
            "bank_account_no": f"XXXX{a.account_last4}", "branch_code": f"XXXX{a.routing_last4}",
            "custom_routing_last4": a.routing_last4, "custom_account_last4": a.account_last4, "custom_valid_from": a.valid_from,
            "custom_valid_to": a.valid_to, "custom_verified_by": a.verified_by, "custom_verified_at": a.verified_at,
            "custom_source": a.source}


def _ensure_bank(api: ErpnextClient, name: str) -> None:
    if not api.exists("Bank", name):
        try:
            api.insert("Bank", {"bank_name": name})
        except ErpnextError:
            pass


def _lines(items: list[dict]) -> list[dict]:
    return [{"idx": i["idx"], "item_code": i["item_code"], "description": i.get("item_name") or i.get("description") or "",
             "qty": int(round(float(i["qty"]))), "rate_cents": c(i["rate"]), "amount_cents": c(i["amount"])} for i in items]


def _po(p: dict) -> dict:
    status = {"Completed": "completed", "To Bill": "to_bill", "To Receive and Bill": "to_bill", "To Receive": "to_bill"}.get(p["status"], p["status"].lower())
    return {"name": p["name"], "supplier": p["supplier"], "company": ABBR.get(p["company"], p["company"]),
            "transaction_date": str(p["transaction_date"]), "department": p.get("custom_department") or "",
            "requested_by": p.get("custom_requested_by") or "", "grand_total_cents": c(p["grand_total"]), "status": status,
            "items": _lines(p["items"])}


def _pr(r: dict) -> dict:
    po = next((i.get("purchase_order") for i in r["items"] if i.get("purchase_order")), None)
    return {"name": r["name"], "supplier": r["supplier"], "company": ABBR.get(r["company"], r["company"]),
            "posting_date": str(r["posting_date"]), "purchase_order": po,
            "items": [{"idx": i["idx"], "item_code": i["item_code"], "received_qty": int(round(float(i["qty"])))} for i in r["items"]]}


def _pi(v: dict) -> dict:
    po = next((i.get("purchase_order") for i in v["items"] if i.get("purchase_order")), None)
    return {"name": v["name"], "supplier": v["supplier"], "company": ABBR.get(v["company"], v["company"]), "bill_no": v.get("bill_no") or "",
            "bill_date": str(v.get("bill_date") or v["posting_date"]), "posting_date": str(v["posting_date"]), "purchase_order": po,
            "net_total_cents": c(v["net_total"]), "tax_cents": c(v.get("total_taxes_and_charges")), "grand_total_cents": c(v["grand_total"]),
            "status": "paid" if v["status"] == "Paid" else "unpaid", "source_doc": v.get("custom_source_doc") or "",
            "items": _lines(v["items"])}


def _pe(e: dict) -> dict:
    ref = next((r["reference_name"] for r in e.get("references", []) if r["reference_doctype"] == "Purchase Invoice"), None)
    return {"name": e["name"], "company": ABBR.get(e["company"], e["company"]), "party": e["party"], "paid_amount_cents": c(e["paid_amount"]),
            "posting_date": str(e["posting_date"]), "reference_invoice": ref, "bank_account": e.get("custom_bank_account_id") or "",
            "transfer_id": e.get("reference_no") or ""}


def _company(co: dict) -> dict | None:
    if co["name"] not in ABBR:
        return None
    return {"name": co["abbr"], "company_name": co["name"], "bank_account": f"SIM-OPS-{co['abbr']}-01",
            "ap_account": co.get("default_payable_account") or "", "bank_gl_account": f"Operating Bank - {co['abbr']}"}


def _item(i: dict) -> dict:
    return {"item_code": i["item_code"], "item_name": i["item_name"], "item_group": i.get("custom_group") or i["item_group"],
            "uom": i.get("stock_uom") or "", "std_rate_cents": c(i.get("standard_rate") or 0)}


TRANSLATE = {"Company": _company, "Item": _item, "Supplier": _supplier, "Bank Account": _bank, "Purchase Order": _po,
             "Purchase Receipt": _pr, "Purchase Invoice": _pi, "Payment Entry": _pe}
