---
name: detect-duplicates
description: Exact and near-duplicate invoices (resends, swapped digits, suffixes) against paid and in-flight bills.
version: 2
---
1. Same vendor and same invoice number as a bill on file: duplicate. Propose REJECT and cite the earlier invoice id.
2. Same vendor, same amount within 60 days and an invoice number that differs only by a suffix (-A, R),
   a removed dash, or two swapped digits: near duplicate. Propose REJECT and cite it.
3. A "resending" or "reminder" email about an invoice already paid is a duplicate, not a new bill.

4. Split to dodge the limit: several bills against the same PO within 7 days, each under your limit, that together exceed it. Treat them as one purchase: propose ASK, flag split_to_dodge_limit, and route on the combined amount. Cite the PO and the earlier bills.

Common mistakes
- Missing a near duplicate because the invoice number is not identical.
