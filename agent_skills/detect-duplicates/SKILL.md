---
name: detect-duplicates
description: Exact and near-duplicate invoices (resends, swapped digits, suffixes) against paid and in-flight bills.
version: 1
---
1. Same vendor and same invoice number as a bill on file: duplicate. Propose REJECT and cite the earlier invoice id.
2. Same vendor, same amount within 60 days and an invoice number that differs only by a suffix (-A, R),
   a removed dash, or two swapped digits: near duplicate. Propose REJECT and cite it.
3. A "resending" or "reminder" email about an invoice already paid is a duplicate, not a new bill.

Common mistakes
- Missing a near duplicate because the invoice number is not identical.
