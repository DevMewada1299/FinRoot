"""ERPNext spike: setup wizard, API key, then Supplier -> PO -> Purchase Receipt -> Purchase Invoice -> Payment Entry
over REST, and read them back. Standard library only. Local Docker instance; nothing here is a real credential.

Run: uv run --project backend python docker/erpnext_spike.py
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8080"
REPO = Path(__file__).resolve().parents[1]
COMPANY = "Harborline Provisions Inc."
ABBR = "HPI"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
AUTH: dict = {}


def call(method: str, path: str, data: dict | None = None, form: bool = False) -> dict:
    url = BASE + path
    headers = {"Accept": "application/json"}
    body = None
    if AUTH:
        headers["Authorization"] = f"token {AUTH['key']}:{AUTH['secret']}"
    if data is not None:
        if form:
            body = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            j = json.loads(txt)
            msg = j.get("exception") or j.get("_server_messages") or j.get("message") or txt[:500]
        except ValueError:
            msg = txt[:500]
        raise RuntimeError(f"{method} {path} -> {e.code}: {str(msg)[:800]}") from None


def wait_up(timeout: int = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            call("GET", "/api/method/ping")
            print(f"ERPNext up after {time.time() - t0:.0f}s")
            return
        except Exception:
            time.sleep(5)
    sys.exit("ERPNext did not come up")


def login() -> None:
    call("POST", "/api/method/login", {"usr": "Administrator", "pwd": "admin"})


def setup_wizard() -> None:
    done = call("GET", "/api/method/frappe.client.get_value?doctype=System+Settings&fieldname=setup_complete&filters=%7B%7D")
    if (done.get("message") or {}).get("setup_complete") in (1, "1"):
        print("setup wizard already complete")
        return
    args = {"language": "English", "country": "United States", "timezone": "America/Los_Angeles", "currency": "USD",
            "full_name": "Marisol Vega", "email": "marisol@harborline.example", "password": "admin",
            "company_name": COMPANY, "company_abbr": ABBR, "chart_of_accounts": "Standard",
            "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31", "setup_demo": 0}
    t = time.time()
    call("POST", "/api/method/frappe.desk.page.setup_wizard.setup_wizard.setup_complete", {"args": args}, form=True)
    print(f"setup wizard completed in {time.time() - t:.0f}s")


def api_keys() -> None:
    login()  # the setup wizard ends the session
    r = call("POST", "/api/method/frappe.core.doctype.user.user.generate_keys", {"user": "Administrator"}, form=True)
    secret = r["message"]["api_secret"]
    key = r["message"].get("api_key") or call("GET", "/api/resource/User/Administrator")["data"]["api_key"]
    AUTH.update(key=key, secret=secret)
    env = REPO / ".env"
    lines = [l for l in env.read_text().splitlines() if not l.startswith(("ERPNEXT_API_KEY=", "ERPNEXT_API_SECRET="))]
    lines += [f"ERPNEXT_API_KEY={key}", f"ERPNEXT_API_SECRET={secret}"]
    env.write_text("\n".join(lines) + "\n")
    print("API key generated and written to .env (not printed)")


def ensure(doctype: str, name: str, doc: dict) -> dict:
    try:
        return call("GET", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")["data"]
    except RuntimeError:
        return call("POST", f"/api/resource/{urllib.parse.quote(doctype)}", doc)["data"]


def insert_submit(doctype: str, doc: dict) -> dict:
    d = call("POST", f"/api/resource/{urllib.parse.quote(doctype)}", doc)["data"]
    d = call("PUT", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(d['name'])}", {"docstatus": 1})["data"]
    return d


def main() -> None:
    timings = {}
    wait_up()
    login()
    setup_wizard()
    api_keys()
    t = time.time()
    wh = f"Stores - {ABBR}"
    ensure("Supplier Group", "Dairy", {"supplier_group_name": "Dairy", "parent_supplier_group": "All Supplier Groups"})
    sup = ensure("Supplier", "Sutro Dairy Coop", {"supplier_name": "Sutro Dairy Coop", "supplier_group": "Dairy", "tax_id": "00-2736064"})
    item = ensure("Item", "DRY-002", {"item_code": "DRY-002", "item_name": "Unsalted butter 36x1lb", "item_group": "All Item Groups",
                                       "stock_uom": "Nos", "is_stock_item": 1})
    po = insert_submit("Purchase Order", {"supplier": sup["name"], "company": COMPANY, "transaction_date": "2026-10-20",
                                          "schedule_date": "2026-10-22",
                                          "items": [{"item_code": item["name"], "qty": 30, "rate": 98.5, "schedule_date": "2026-10-22",
                                                     "warehouse": wh}]})
    pr = insert_submit("Purchase Receipt", {"supplier": sup["name"], "company": COMPANY, "posting_date": "2026-10-22",
                                            "items": [{"item_code": item["name"], "qty": 30, "rate": 98.5, "warehouse": wh,
                                                       "purchase_order": po["name"], "purchase_order_item": po["items"][0]["name"]}]})
    pi = insert_submit("Purchase Invoice", {"supplier": sup["name"], "company": COMPANY, "bill_no": "29399", "bill_date": "2026-10-22",
                                            "posting_date": "2026-10-23", "set_posting_time": 1,
                                            "items": [{"item_code": item["name"], "qty": 30, "rate": 98.5,
                                                       "purchase_order": po["name"], "po_detail": po["items"][0]["name"],
                                                       "purchase_receipt": pr["name"], "pr_detail": pr["items"][0]["name"]}]})
    co = call("GET", f"/api/resource/Company/{urllib.parse.quote(COMPANY)}")["data"]
    pe = insert_submit("Payment Entry", {"payment_type": "Pay", "company": COMPANY, "posting_date": "2026-10-24", "party_type": "Supplier",
                                         "party": sup["name"], "paid_from": co.get("default_bank_account") or co.get("default_cash_account") or f"Cash - {ABBR}",
                                         "paid_to": co["default_payable_account"], "paid_amount": pi["grand_total"],
                                         "received_amount": pi["grand_total"], "reference_no": "ach_spike_1", "reference_date": "2026-10-24",
                                         "references": [{"reference_doctype": "Purchase Invoice", "reference_name": pi["name"],
                                                         "allocated_amount": pi["grand_total"]}]})
    timings["five_doctypes_s"] = round(time.time() - t, 1)
    # read back
    back = {dt: call("GET", f"/api/resource/{urllib.parse.quote(dt)}/{urllib.parse.quote(n)}")["data"]
            for dt, n in [("Purchase Order", po["name"]), ("Purchase Receipt", pr["name"]), ("Purchase Invoice", pi["name"]),
                          ("Payment Entry", pe["name"])]}
    gl = call("GET", "/api/resource/GL%20Entry?" + urllib.parse.urlencode({
        "filters": json.dumps([["voucher_no", "in", [pi["name"], pe["name"]]]]), "fields": json.dumps(["account", "debit", "credit", "voucher_no"]),
        "limit_page_length": 50}))["data"]
    print(json.dumps({
        "supplier": sup["name"], "po": [po["name"], back["Purchase Order"]["status"]], "pr": [pr["name"], back["Purchase Receipt"]["status"]],
        "pi": [pi["name"], back["Purchase Invoice"]["status"], back["Purchase Invoice"]["outstanding_amount"]],
        "pe": [pe["name"], back["Payment Entry"]["docstatus"]],
        "gl_rows": len(gl), "gl_balanced": round(sum(g["debit"] for g in gl), 2) == round(sum(g["credit"] for g in gl), 2),
        "timings": timings}, indent=1))


if __name__ == "__main__":
    main()
