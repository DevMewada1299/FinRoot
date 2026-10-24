"""Render an invoice as a messy email body. Several vendor styles, Faker-style typos, mixed date formats."""
from __future__ import annotations

import datetime as dt
import random

TYPOS = {"avocados": "avacados", "Romaine": "Romane", "tomatoes": "tomatos", "Strawberries": "Strawberrys",
         "spinach": "spinnach", "butter": "buter", "Greek": "Greak", "boneless": "bonless", "salmon": "salmom",
         "service": "servcie", "freight": "frieght", "Canned": "Caned", "repair": "repiar", "container": "contianer"}


def money(c: int, style: int) -> str:
    if style == 1:
        return f"{c / 100:.2f}"
    if style == 2:
        return f"USD {c / 100:,.2f}"
    return f"${c // 100:,}.{c % 100:02d}"


def date_str(iso: str, style: int) -> str:
    d = dt.date.fromisoformat(iso)
    return [d.strftime("%m/%d/%Y"), d.strftime("%b %d, %Y"), d.isoformat(), d.strftime("%d-%b-%Y")][style % 4]


def typo(text: str, rng: random.Random, p: float = 0.25) -> str:
    for a, b in TYPOS.items():
        if a in text and rng.random() < p:
            text = text.replace(a, b)
    return text


def render(doc: dict, rng: random.Random) -> tuple[str, str]:
    """doc keys: vendor_name, tax_id, bill_no, bill_date, po, bill_to, lines[(desc,qty,rate,amt)], net, tax, total,
    bank_name, routing_last4, account_last4, intro, extra, service_period. Returns (subject, body)."""
    style = doc.get("style", rng.randrange(4))
    ms = style % 3
    out: list[str] = []
    intro = doc.get("intro") or rng.choice([
        "Hi AP team,\n\nPlease find our invoice below.",
        "Hello,\n\nAttached / below is the invoice for the latest delivery. Let us know if anything looks off.",
        "Good morning,\n\nInvoice for this week below. Thanks for your business!",
        "Hi there - invoice copy below, original to follow by mail.",
    ])
    out.append(intro)
    out.append("")
    if style == 0:
        out += [f"INVOICE  {doc['bill_no']}", f"Date: {date_str(doc['bill_date'], 0)}", f"Bill to: {doc['bill_to']}"]
        if doc.get("po"):
            out.append(f"PO #: {doc['po']}")
        out.append(f"Vendor: {doc['vendor_name']}    Tax ID: {doc['tax_id']}")
    elif style == 1:
        out += [f"{doc['vendor_name'].upper()}", f"EIN {doc['tax_id']}", "",
                f"Inv No. {doc['bill_no']}   Invoice date {date_str(doc['bill_date'], 1)}",
                f"Customer: {doc['bill_to']}" + (f"   Your PO: {doc['po']}" if doc.get("po") else "")]
    elif style == 2:
        out += [f"Bill Number: {doc['bill_no']}", f"Issued: {date_str(doc['bill_date'], 2)}",
                f"From: {doc['vendor_name']} (tax id {doc['tax_id']})", f"To: {doc['bill_to']}"]
        if doc.get("po"):
            out.append(f"Purchase order: {doc['po']}")
    else:
        out += [f"Invoice # {doc['bill_no']}  |  {date_str(doc['bill_date'], 3)}",
                f"{doc['vendor_name']} / TIN {doc['tax_id']}", f"Sold to {doc['bill_to']}"]
        if doc.get("po"):
            out.append(f"Ref PO {doc['po']}")
    if doc.get("service_period"):
        out.append(f"Service period: {doc['service_period']}")
    out.append("")
    out.append("Qty   Description                         Unit price      Amount")
    for desc, qty, rate, amt in doc["lines"]:
        out.append(f"{qty:<5} {typo(desc, rng):<35} {money(rate, ms):>12} {money(amt, ms):>12}")
    out.append("")
    out.append(f"Subtotal: {money(doc['net'], ms)}")
    out.append(f"Tax: {money(doc['tax'], ms)}")
    out.append(rng.choice(["TOTAL DUE", "Amount due", "Total", "Balance due"]) + f": {money(doc['total'], ms)}")
    out.append("")
    remit = rng.choice([
        "Remit to: {b}, routing ending {r}, account ending {a}",
        "Please pay by ACH to {b} - routing ****{r} acct ****{a}",
        "Bank: {b} | Routing xxxx{r} | Account xxxx{a}",
    ]).format(b=doc["bank_name"], r=doc["routing_last4"], a=doc["account_last4"])
    out.append(remit)
    out.append(rng.choice(["Terms: Net 30", "Payment terms: net 30 days", "Due in 30 days", "Terms NET30"]))
    if doc.get("extra"):
        out.append("")
        out.append(doc["extra"])
    out.append("")
    out.append(rng.choice(["Thanks,\nAccounts Receivable", "Best,\nBilling Dept.", "Regards,\nAR team", "Thank you!"]))
    subject = rng.choice([f"Invoice {doc['bill_no']}", f"{doc['vendor_name']} invoice {doc['bill_no']}",
                          f"Inv {doc['bill_no']} - {doc['bill_to']}", f"New invoice from {doc['vendor_name']}"])
    if doc.get("subject"):
        subject = doc["subject"]
    return subject, "\n".join(out)
