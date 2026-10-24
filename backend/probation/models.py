"""Contracts shared by every module. Money is integer cents. Timestamps are UTC ISO strings."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Cents = int


def usd(cents: Cents) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


# ---------- system of record (shapes modelled on ERPNext doctypes) ----------

class Company(BaseModel):
    name: str                  # e.g. "HPI"
    company_name: str
    bank_account: str          # our operating account id at SimBank
    ap_account: str            # GL account names
    bank_gl_account: str


class Supplier(BaseModel):
    name: str                  # SUP-0001
    supplier_name: str
    supplier_group: str
    tax_id: str
    email_domain: str
    phone: str
    status: Literal["active", "frozen", "inactive"] = "active"
    created_at: str
    represents_company: str | None = None   # set for intercompany suppliers


class BankAccountRecord(BaseModel):
    name: str                  # SBA-0001
    supplier: str
    bank_name: str
    routing_last4: str
    account_last4: str
    valid_from: str
    valid_to: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None
    source: str                # onboarding | phone_verified | email_request


class POLine(BaseModel):
    item_code: str
    description: str
    qty: int
    rate_cents: Cents
    amount_cents: Cents


class PurchaseOrder(BaseModel):
    name: str                  # PO-10001
    supplier: str
    company: str
    transaction_date: str
    department: str
    requested_by: str
    grand_total_cents: Cents
    status: str = "open"
    items: list[POLine] = []


class ReceiptLine(BaseModel):
    item_code: str
    received_qty: int


class PurchaseReceipt(BaseModel):
    name: str                  # PR-10001
    supplier: str
    company: str
    posting_date: str
    purchase_order: str
    items: list[ReceiptLine] = []


class InvoiceLine(BaseModel):
    item_code: str | None = None
    description: str
    qty: int
    rate_cents: Cents
    amount_cents: Cents


class PurchaseInvoice(BaseModel):
    name: str                  # PINV-100001
    supplier: str
    company: str
    bill_no: str               # the vendor's own invoice number
    bill_date: str
    posting_date: str
    purchase_order: str | None = None
    net_total_cents: Cents
    tax_cents: Cents
    grand_total_cents: Cents
    status: str = "unpaid"
    source_doc: str | None = None
    items: list[InvoiceLine] = []


class PaymentEntry(BaseModel):
    name: str                  # PAY-100001
    company: str
    party: str
    paid_amount_cents: Cents
    posting_date: str
    reference_invoice: str
    bank_account: str          # SBA id the money went to
    transfer_id: str


class GLEntry(BaseModel):
    name: str
    posting_date: str
    company: str
    account: str
    debit_cents: Cents
    credit_cents: Cents
    voucher_type: str
    voucher_no: str
    party: str | None = None


class RawRow(BaseModel):
    seq: int
    doctype: str
    name: str
    data: dict


# ---------- system of work ----------

class State(StrEnum):
    NEW = "NEW"
    EXTRACTED = "EXTRACTED"
    PROPOSED = "PROPOSED"
    AUTO_APPROVED = "AUTO_APPROVED"
    NEEDS_APPROVER = "NEEDS_APPROVER"
    HELD = "HELD"
    BLOCKED = "BLOCKED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"


class InboxDoc(BaseModel):
    """A raw email or pasted invoice. Everything in here is untrusted data."""
    id: str                    # DOC-000001
    received_at: str
    channel: Literal["email", "paste"]
    from_addr: str = ""
    subject: str = ""
    body: str
    attachments: list[dict] = []


class ExtractedLine(BaseModel):
    description: str
    item_code: str | None = None
    qty: int = 1
    rate_cents: Cents = 0
    amount_cents: Cents = 0


class ExtractedInvoice(BaseModel):
    vendor_name: str = ""
    vendor_tax_id: str = ""
    bill_no: str = ""
    bill_date: str = ""
    po_number: str = ""
    bill_to_company: str = ""
    net_total_cents: Cents = 0
    tax_cents: Cents = 0
    grand_total_cents: Cents = 0
    remit_bank_name: str = ""
    remit_routing_last4: str = ""
    remit_account_last4: str = ""
    service_period: str = ""
    lines: list[ExtractedLine] = []
    extractor: str = "rules"   # rules | llm


class WorkItem(BaseModel):
    id: str                    # WI-000001
    kind: str = "purchase_invoice"
    state: State
    doc_id: str
    supplier: str | None = None
    entity_id: str | None = None
    amount_cents: Cents = 0
    extracted: ExtractedInvoice | None = None
    facts: dict = {}
    proposal: dict | None = None
    routing: dict | None = None
    required_role: str | None = None
    requested_by: str = "inbox"
    proposed_by: str | None = None
    approved_by: str | None = None
    posted_by: str | None = None
    record_invoice: str | None = None
    record_payment: str | None = None
    created_at: str
    updated_at: str


ProposalAction = Literal["pay", "ask", "hold", "reject"]


class Proposal(BaseModel):
    """What the agent hands to the gateway. Structured only; the gateway ignores prose."""
    work_item: str
    action: ProposalAction
    summary: str = Field(max_length=600)
    reasons: list[str] = Field(default_factory=list, max_length=12)
    flags: list[str] = Field(default_factory=list)   # e.g. ["bank_change", "injection"]
    evidence: list[str] = Field(default_factory=list)  # record / memory ids the agent relied on
    question_for_human: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    mode: Literal["llm", "rules"] = "rules"


class AuditRow(BaseModel):
    seq: int
    at: str
    actor: str
    action: str
    subject: str
    allowed: bool
    reason: str
    diff: dict
    evidence: list[str]
    prev_hash: str
    hash: str


class Denied(Exception):
    def __init__(self, reason: str, rule: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.rule = rule
