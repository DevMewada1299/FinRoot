"""SQLite system of record. Table and field names follow ERPNext doctypes (snake_cased).

Every insert or update appends to change_log so the sync job can read changes with a cursor.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from probation.models import (
    BankAccountRecord, Company, GLEntry, PaymentEntry, POLine, PurchaseInvoice,
    PurchaseOrder, PurchaseReceipt, RawRow, ReceiptLine, Supplier, InvoiceLine,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS company(name TEXT PRIMARY KEY, company_name TEXT, bank_account TEXT,
  ap_account TEXT, bank_gl_account TEXT);
CREATE TABLE IF NOT EXISTS account(name TEXT PRIMARY KEY, company TEXT, root_type TEXT, account_type TEXT);
CREATE TABLE IF NOT EXISTS item(item_code TEXT PRIMARY KEY, item_name TEXT, item_group TEXT, uom TEXT,
  std_rate_cents INTEGER);
CREATE TABLE IF NOT EXISTS supplier(name TEXT PRIMARY KEY, supplier_name TEXT, supplier_group TEXT,
  tax_id TEXT, email_domain TEXT, phone TEXT, status TEXT, created_at TEXT, represents_company TEXT);
CREATE TABLE IF NOT EXISTS supplier_bank_account(name TEXT PRIMARY KEY, supplier TEXT, bank_name TEXT,
  routing_last4 TEXT, account_last4 TEXT, valid_from TEXT, valid_to TEXT, verified_by TEXT,
  verified_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS purchase_order(name TEXT PRIMARY KEY, supplier TEXT, company TEXT,
  transaction_date TEXT, department TEXT, requested_by TEXT, grand_total_cents INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS purchase_order_item(parent TEXT, idx INTEGER, item_code TEXT, description TEXT,
  qty INTEGER, rate_cents INTEGER, amount_cents INTEGER, PRIMARY KEY(parent, idx));
CREATE TABLE IF NOT EXISTS purchase_receipt(name TEXT PRIMARY KEY, supplier TEXT, company TEXT,
  posting_date TEXT, purchase_order TEXT);
CREATE TABLE IF NOT EXISTS purchase_receipt_item(parent TEXT, idx INTEGER, item_code TEXT,
  received_qty INTEGER, PRIMARY KEY(parent, idx));
CREATE TABLE IF NOT EXISTS purchase_invoice(name TEXT PRIMARY KEY, supplier TEXT, company TEXT,
  bill_no TEXT, bill_date TEXT, posting_date TEXT, purchase_order TEXT, net_total_cents INTEGER,
  tax_cents INTEGER, grand_total_cents INTEGER, status TEXT, source_doc TEXT);
CREATE TABLE IF NOT EXISTS purchase_invoice_item(parent TEXT, idx INTEGER, item_code TEXT,
  description TEXT, qty INTEGER, rate_cents INTEGER, amount_cents INTEGER, PRIMARY KEY(parent, idx));
CREATE TABLE IF NOT EXISTS payment_entry(name TEXT PRIMARY KEY, company TEXT, party TEXT,
  paid_amount_cents INTEGER, posting_date TEXT, reference_invoice TEXT, bank_account TEXT, transfer_id TEXT);
CREATE TABLE IF NOT EXISTS gl_entry(name TEXT PRIMARY KEY, posting_date TEXT, company TEXT, account TEXT,
  debit_cents INTEGER, credit_cents INTEGER, voucher_type TEXT, voucher_no TEXT, party TEXT);
CREATE TABLE IF NOT EXISTS change_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, doctype TEXT, name TEXT);
CREATE INDEX IF NOT EXISTS ix_pi_supplier ON purchase_invoice(supplier);
CREATE INDEX IF NOT EXISTS ix_pr_po ON purchase_receipt(purchase_order);
CREATE INDEX IF NOT EXISTS ix_sba_supplier ON supplier_bank_account(supplier);
CREATE INDEX IF NOT EXISTS ix_gl_voucher ON gl_entry(voucher_no);
"""

CHILDREN = {
    "purchase_order": "purchase_order_item",
    "purchase_receipt": "purchase_receipt_item",
    "purchase_invoice": "purchase_invoice_item",
}
PK = {"item": "item_code"}

EXPENSE_BY_GROUP = {
    "Produce": "5100 Cost of Goods - Produce",
    "Dairy": "5110 Cost of Goods - Dairy",
    "Meat & Seafood": "5120 Cost of Goods - Protein",
    "Dry Goods": "5130 Cost of Goods - Dry Goods",
    "Beverage": "5140 Cost of Goods - Beverage",
    "Packaging": "5200 Packaging",
    "Fuel & Fleet": "6100 Fleet and Fuel",
    "Facilities": "6200 Facilities and Repairs",
    "Logistics": "6300 Freight Out",
    "Professional Services": "6400 Professional Services",
    "Utilities": "6500 Utilities",
    "IT & Software": "6600 Software",
    "Intercompany": "2300 Intercompany Payable",
}


class LedgerImbalance(Exception):
    pass


class SqliteRecord:
    """Writable record. Construct it only in the gateway and the seed script."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ---------- helpers ----------
    def _insert(self, table: str, row: dict, log: bool = True) -> None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        self.db.execute(f"INSERT INTO {table}({cols}) VALUES({qs})", list(row.values()))
        if log and table not in CHILDREN.values():
            self.db.execute("INSERT INTO change_log(doctype,name) VALUES(?,?)",
                            (table, row.get(PK.get(table, "name"))))

    def _touch(self, table: str, name: str) -> None:
        self.db.execute("INSERT INTO change_log(doctype,name) VALUES(?,?)", (table, name))

    def _one(self, sql: str, *args) -> sqlite3.Row:
        r = self.db.execute(sql, args).fetchone()
        if r is None:
            raise KeyError(f"not found: {args}")
        return r

    # ---------- reads ----------
    def get_company(self, name: str) -> Company:
        return Company(**dict(self._one("SELECT * FROM company WHERE name=?", name)))

    def get_supplier(self, supplier_id: str) -> Supplier:
        return Supplier(**dict(self._one("SELECT * FROM supplier WHERE name=?", supplier_id)))

    def bank_history(self, supplier_id: str) -> list[BankAccountRecord]:
        rows = self.db.execute(
            "SELECT * FROM supplier_bank_account WHERE supplier=? ORDER BY valid_from", (supplier_id,))
        return [BankAccountRecord(**dict(r)) for r in rows]

    def get_po(self, po_id: str) -> PurchaseOrder:
        po = dict(self._one("SELECT * FROM purchase_order WHERE name=?", po_id))
        items = self.db.execute("SELECT * FROM purchase_order_item WHERE parent=? ORDER BY idx", (po_id,))
        po["items"] = [POLine(**{k: r[k] for k in POLine.model_fields}) for r in items]
        return PurchaseOrder(**po)

    def get_receipts(self, po_id: str) -> list[PurchaseReceipt]:
        out = []
        for r in self.db.execute("SELECT * FROM purchase_receipt WHERE purchase_order=?", (po_id,)).fetchall():
            d = dict(r)
            items = self.db.execute("SELECT * FROM purchase_receipt_item WHERE parent=? ORDER BY idx", (d["name"],))
            d["items"] = [ReceiptLine(item_code=i["item_code"], received_qty=i["received_qty"]) for i in items]
            out.append(PurchaseReceipt(**d))
        return out

    def changes_since(self, cursor: str, limit: int = 20000) -> tuple[list[RawRow], str]:
        start = int(cursor or 0)
        log = self.db.execute(
            "SELECT seq, doctype, name FROM change_log WHERE seq>? ORDER BY seq LIMIT ?", (start, limit)).fetchall()
        out: list[RawRow] = []
        for seq, doctype, name in log:
            pk = PK.get(doctype, "name")
            row = self.db.execute(f"SELECT * FROM {doctype} WHERE {pk}=?", (name,)).fetchone()
            if row is None:
                continue
            data = dict(row)
            child = CHILDREN.get(doctype)
            if child:
                data["items"] = [dict(c) for c in self.db.execute(
                    f"SELECT * FROM {child} WHERE parent=? ORDER BY idx", (name,))]
            out.append(RawRow(seq=seq, doctype=doctype, name=name, data=data))
        new_cursor = str(log[-1][0]) if log else str(start)
        return out, new_cursor

    def gl_totals(self) -> tuple[int, int]:
        d, c = self.db.execute("SELECT COALESCE(SUM(debit_cents),0), COALESCE(SUM(credit_cents),0) FROM gl_entry").fetchone()
        return int(d), int(c)

    def next_name(self, table: str, prefix: str, width: int = 6) -> str:
        n = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        while True:
            n += 1
            name = f"{prefix}{n:0{width}d}"
            if not self.db.execute(f"SELECT 1 FROM {table} WHERE name=?", (name,)).fetchone():
                return name

    # ---------- seed-only bulk writes ----------
    def seed_company(self, c: Company) -> None:
        self._insert("company", c.model_dump())

    def seed_account(self, name: str, company: str, root_type: str, account_type: str) -> None:
        self._insert("account", {"name": name, "company": company, "root_type": root_type,
                                 "account_type": account_type}, log=False)

    def seed_item(self, item_code: str, item_name: str, item_group: str, uom: str, std_rate_cents: int) -> None:
        self._insert("item", {"item_code": item_code, "item_name": item_name, "item_group": item_group,
                              "uom": uom, "std_rate_cents": std_rate_cents})

    def seed_supplier(self, s: Supplier) -> None:
        self._insert("supplier", s.model_dump())

    def seed_po(self, po: PurchaseOrder) -> None:
        d = po.model_dump(exclude={"items"})
        self._insert("purchase_order", d)
        for i, line in enumerate(po.items, 1):
            self._insert("purchase_order_item", {"parent": po.name, "idx": i, **line.model_dump()})

    def seed_receipt(self, pr: PurchaseReceipt) -> None:
        self._insert("purchase_receipt", pr.model_dump(exclude={"items"}))
        for i, line in enumerate(pr.items, 1):
            self._insert("purchase_receipt_item", {"parent": pr.name, "idx": i, **line.model_dump()})

    def commit(self) -> None:
        self.db.commit()

    # ---------- gateway-only writes ----------
    def _post_gl(self, entries: list[GLEntry]) -> None:
        if sum(e.debit_cents for e in entries) != sum(e.credit_cents for e in entries):
            raise LedgerImbalance(f"voucher {entries[0].voucher_no} does not balance")
        for e in entries:
            self._insert("gl_entry", e.model_dump(), log=False)

    def _gl_name(self) -> str:
        if not hasattr(self, "_gl_seq"):
            self._gl_seq = self.db.execute("SELECT COUNT(*) FROM gl_entry").fetchone()[0]
        self._gl_seq += 1
        return f"GLE-{self._gl_seq:08d}"

    def post_purchase_invoice(self, inv: PurchaseInvoice, commit: bool = True) -> str:
        sup = self.get_supplier(inv.supplier)
        co = self.get_company(inv.company)
        expense = f"{EXPENSE_BY_GROUP.get(sup.supplier_group, '6900 Other Expense')} - {co.name}"
        tax_acct = f"1400 Recoverable Tax - {co.name}"
        try:
            self._insert("purchase_invoice", inv.model_dump(exclude={"items"}))
            for i, line in enumerate(inv.items, 1):
                self._insert("purchase_invoice_item", {"parent": inv.name, "idx": i, **line.model_dump()})
            entries = [GLEntry(name=self._gl_name(), posting_date=inv.posting_date, company=inv.company,
                               account=expense, debit_cents=inv.net_total_cents, credit_cents=0,
                               voucher_type="Purchase Invoice", voucher_no=inv.name, party=inv.supplier)]
            if inv.tax_cents:
                entries.append(GLEntry(name=self._gl_name(), posting_date=inv.posting_date, company=inv.company,
                                       account=tax_acct, debit_cents=inv.tax_cents, credit_cents=0,
                                       voucher_type="Purchase Invoice", voucher_no=inv.name))
            entries.append(GLEntry(name=self._gl_name(), posting_date=inv.posting_date, company=inv.company,
                                   account=co.ap_account, debit_cents=0, credit_cents=inv.grand_total_cents,
                                   voucher_type="Purchase Invoice", voucher_no=inv.name, party=inv.supplier))
            self._post_gl(entries)
            if commit:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return inv.name

    def post_payment_entry(self, pay: PaymentEntry, commit: bool = True) -> str:
        co = self.get_company(pay.company)
        try:
            self._insert("payment_entry", pay.model_dump())
            self._post_gl([
                GLEntry(name=self._gl_name(), posting_date=pay.posting_date, company=pay.company,
                        account=co.ap_account, debit_cents=pay.paid_amount_cents, credit_cents=0,
                        voucher_type="Payment Entry", voucher_no=pay.name, party=pay.party),
                GLEntry(name=self._gl_name(), posting_date=pay.posting_date, company=pay.company,
                        account=co.bank_gl_account, debit_cents=0, credit_cents=pay.paid_amount_cents,
                        voucher_type="Payment Entry", voucher_no=pay.name),
            ])
            self.db.execute("UPDATE purchase_invoice SET status='paid' WHERE name=?", (pay.reference_invoice,))
            self._touch("purchase_invoice", pay.reference_invoice)
            if commit:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return pay.name

    def add_bank_account(self, acct: BankAccountRecord, commit: bool = True) -> str:
        self.db.execute("UPDATE supplier_bank_account SET valid_to=? WHERE supplier=? AND valid_to IS NULL",
                        (acct.valid_from, acct.supplier))
        for (n,) in self.db.execute("SELECT name FROM supplier_bank_account WHERE supplier=?", (acct.supplier,)).fetchall():
            self._touch("supplier_bank_account", n)
        self._insert("supplier_bank_account", acct.model_dump())
        if commit:
            self.db.commit()
        return acct.name

    def set_supplier_status(self, supplier_id: str, status: str, commit: bool = True) -> None:
        self.db.execute("UPDATE supplier SET status=? WHERE name=?", (status, supplier_id))
        self._touch("supplier", supplier_id)
        if commit:
            self.db.commit()
