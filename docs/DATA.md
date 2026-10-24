# Data plan: real where it is public and cheap, synthetic where we need labels

Accuracy claims need an answer key. No real dataset tells us which invoice was fraudulent, so everything transactional is synthetic and labeled. Real sources are used for sizing, document realism and screening. State this plainly in the README.

## What is real and what is simulated
| Layer | Source | Status |
|---|---|---|
| Company scale and cost mix | SEC EDGAR (one public food distributor or restaurant operator) | Real numbers, used only to size the fake company |
| Invoice documents (messy layouts) | A public invoice dataset, if its licence and format work (see below) | Real documents, with our own context built around them |
| Vendor screening | OFAC Specially Designated Nationals list (US Treasury, Sanctions List Service) | Real public list |
| Accounting engine | ERPNext, if the spike passes | Real software, our data |
| Company, vendors, POs, receipts, payments, bank, people | Generated | Simulated |
| Fraud and error scenarios, labels | Generated, modelled on common business-email-compromise patterns | Simulated, labeled |

## SEC EDGAR (sizing)
- Free JSON on `data.sec.gov` (Company Facts, Company Concept, Frames). No API key. Send a descriptive User-Agent that identifies the app and a contact email; cache responses; honour `Retry-After`; do not hammer it.
- Pick one public company, record its CIK in this file, and pull cost of revenue, revenue and accounts payable. **Do not assume XBRL tag names.** Inspect the Company Facts output and confirm the tags before using them.
- Use it to set Harborline's annual purchasing spend, payables balance, invoices per month and average invoice size (scaled to the roughly 1,200 invoices a month in `CLAUDE.md`). Document the arithmetic.

## Real invoice documents (messy-text realism)
- Candidate: DocILE, a public invoice test set. Licence, registration and download format are **unverified**; check before relying on it. Other public invoice datasets are fine if the licence allows use in a hackathon project.
- Time-box to 30 minutes. If it is not usable, fall back to Claude-written documents.
- **Reverse-seeding:** take a sample (at least 50), extract fields with Haiku, then create the vendor, PO and goods receipt that would match each document in our record. Label the original clean. Plant corruptions programmatically (swap invoice digits, change a total, change the bank account) and write a label row for each.
- Keep raw dataset files out of git if the licence is unclear. Credit the source in the README.

## OFAC SDN screening (real public list)
- Download the list from Treasury's Sanctions List Service and build a local name index. Verify the file format before coding.
- On new-vendor onboarding, fuzzy-match the vendor name. A match is "possible match, needs a human", never an accusation.
- Never present a real listed entity as a demo vendor. Test with synthetic names that fuzzy-match an entry, which also gives us a false-positive case for the "asks at the right moment" criterion.

## Synthetic generation (see `CLAUDE.md` for the label list)
- Bulk history from code and templates. Claude writes about 300 messy documents for the eval set and the demo.
- Every document gets a label row in `data/labels/*.jsonl`. The runtime agent can never read it.
- About 65% clean, with the planted problems listed in `CLAUDE.md`.

## README wording (adapt)
"Real: ERPNext software (if used), SEC EDGAR figures used to size the company, the OFAC list for vendor screening, public invoice documents (source and licence credited). Simulated: the company Harborline Provisions, its vendors, POs, receipts, payments, bank and people, and every fraud and error scenario, with an answer key we used to measure accuracy."
