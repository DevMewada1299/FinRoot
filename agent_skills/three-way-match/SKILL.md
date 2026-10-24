---
name: three-way-match
description: Match the invoice to its purchase order and goods receipt; tolerances, partial receipts, partial billing.
version: 1
---
1. Look up the PO cited on the invoice. It must belong to the same vendor.
2. Match each invoice line to a PO line (by item code, else by description; vendors misspell items).
3. Price: invoice unit price vs PO unit price. Up to 3% over is within tolerance for known vendors; more needs a human.
4. Quantity: units billed now + units billed before on this PO must not exceed units ordered.
5. Receipt: units billed (including earlier bills) must not exceed units the warehouse received (goods receipt).
6. Partial billing against a PO is normal. Note how many bills this PO has had.

Common mistakes
- Comparing the invoice total to the PO total when the vendor is billing in parts.
- Ignoring a short receipt because the PO quantity matches.
