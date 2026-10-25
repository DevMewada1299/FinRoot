**Pip is the AP specialist at Harborline Provisions. It reads vendor bills, matches them to purchase orders and goods receipts, pays what is safe, and asks the right human for the rest.**

Probation is the risk layer that lets a finance team hire it: tiered trust, limits enforced in code, a tamper-evident work record, and an agent that learns from corrections and from its own mistakes. Built at Cal Hacks 13.0.

## What you will see
1. **The planted email.** A "we changed banks" email from a look-alike domain, with an amount that matches a real PO and a line telling the AP system to skip the callback. Pip flags it, the gateway holds the payment (G-04) and treats the text as data (G-05), Pip's limit is tightened for 7 days and the vendor is frozen.
2. **Pip's first mistake.** Yesterday one delivery was billed in three parts, each just under Pip's $5,000 limit. Pip paid all three. The controller reports it; Pip writes a post-mortem; the Policy Lab shows the incident, the lesson, the exact change and a replay test.
3. **Not repeating it.** After the controller approves the lesson, the same pattern from another vendor is flagged and routed to the Controller on the combined amount, before anything is paid. The Scorecard shows split-bill recall before and after.
4. **Defending a decision.** Every bill has an evidence view (invoice vs records vs bank history, three-way match, clickable record ids) and Ask Pip answers "why?" from the hash-chained work record. "Edit an old row" then "Verify the record" shows tampering being detected.

## How it is built
```
AP inbox / paste box -> Extractor (rules, Haiku fallback) -> work item
                                 |
 Pip (Sonnet tool loop, or the same procedure as code in rules mode)
   read-only: golden.db, search_memory, load_skill -> proposes only, cites evidence ids
                 |
                 v
 GATEWAY (the only writer): state machine, can(), tiers, approval matrix, hard limits G-01..G-05,
   evidence check, JSON policy engine, hash-chained audit log
      |                  |                 |
 record.db (ERPNext   work.db            SimBank
 doctype shapes)      (system of work)
      -> sync -> golden.db (read model Pip queries)
```
- **The model proposes, code decides.** Pip's code has no write handle (a test parses its imports and checks its connections are read-only). The gateway recomputes every fact itself; the proposal can only make routing stricter.
- **Limits live in code.** Tiers L0 $0, L1 $500, L2 $5,000, L3 $25,000; approval matrix to $10k department manager, $50k controller, above that CFO; requester, approver and poster are always different actors.
- **Every state change goes through `transition()`**, which appends a SHA-256-chained audit row in the same transaction. Denied attempts are logged too.
- **Learning can only tighten.** Lessons are validated (no touching tiers, limits, freezes or thresholds; no instruction-like text), need a human, and add a regression case. Pip's learnable checks stay inert until an approved lesson turns them on.
- Details: `docs/ARCHITECTURE.md`, `docs/LEARNING.md`.

## Real vs simulated
**Real:** ERPNext v16 is the system of record in the live demo (local Docker, `make erpnext-seed`): 405 suppliers, 416 supplier bank accounts and a 3-month slice of history (3,784 purchase orders and goods receipts, 3,750 purchase invoices, 2,525 payment entries) loaded over its REST API with our own document ids. Every payment Pip or a human approves is posted by the gateway as a submitted Purchase Invoice and Payment Entry with ERPNext's own GL entries; open http://localhost:8080 to see them. The sync job reads ERPNext back into golden.db. `make test` and `make eval` run on the SQLite record (same generator, same ids, same shapes) so they are fast and isolated. Also real: the schema (modelled on ERPNext doctypes: Supplier, Purchase Order, Purchase Receipt, Purchase Invoice, Payment Entry, GL Entry), the fraud patterns (common business-email-compromise attacks: look-alike domains, bank-change requests before a payment, copied amounts, instructions aimed at automated AP), and the code paths: the gateway, policy engine, audit chain and trust rules that run in the demo are the ones the tests check.

**Simulated:** the company Harborline Provisions, its 405 vendors, 8 months of history, POs, receipts, payments, the bank (SimBank, no real money), the people (department manager, controller, CFO), every fraud and error scenario, and the answer key used to measure accuracy. Tax ids use the invalid `00-` prefix; bank details are last-4 digits only. Pip's promotions before Oct 24 come from a shadow-mode review of historical bills; fast-forward generates new weeks of bills, and simulated people decide from the evidence, never from the labels. See `docs/DATA.md` for what is and is not done yet (SEC EDGAR sizing).

## Run it
Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+.
```
make setup     # deps, copies .env.example to .env
make seed      # generate data, sync to golden.db, snapshot
make test      # invariant tests (must pass before any demo)
make eval      # labeled evaluation -> eval/report.md, eval/report.json
make demo      # reset, then API on :8000 and UI on :5173
```
ERPNext (optional, recommended): Docker, then `make erpnext-seed` once (about 15 minutes; the image is about 1 GB) and `RECORD_BACKEND=erpnext` in `.env`. `make demo` then restores an ERPNext database snapshot on every reset (about 10 seconds). Without it, the demo runs on the SQLite record.
Without `ANTHROPIC_API_KEY`, Pip runs in **rules mode**: the same procedure written as code, labelled in the UI. With a key, Pip is `claude-sonnet-5-5` in a tool loop and extraction falls back to `claude-haiku-4-5`. LLM responses are cached on disk by input hash and `MAX_LLM_CALLS` caps live calls.

## Eval
Numbers from `make eval` only (see `eval/report.md`). The latest run, in rules mode, on 300 labeled documents (193 clean, 107 planted), Pip fixed at Level 2:

| | A. before learning | B. onboarding policies | C. after lessons |
|---|---|---|---|
| Auto-pay rate (clean bills) | 0.0% | 74.1% | 74.1% |
| False-hold rate (clean bills) | 0.0% | 0.0% | 0.0% |
| Bad bills auto-paid | 0 | 16 | 0 |
| Split-to-dodge-limit recall | 0% | 0% | 100% |

8 of 8 injection documents, run with full side effects, changed nothing. Caveat: the eval documents come from the same templates as the rules extractor, so rules-mode numbers for the other problem types are high partly by construction. The meaningful learning result is the split-bill row: same documents, same code, one human-approved lesson.

## Tests (`make test`)
No write handle in the agent; denied actions are logged and raise; the audit chain verifies and detects an edited row; the ledger balances after every posting; changed bank details block payment within the limit; injected instructions change nothing; non-existent evidence is rejected; requester and Pip cannot approve; promotion, demotion and the 7-day tightening expiry; a lesson cannot relax a hard limit; an unapproved lesson changes nothing; every incident with a lesson has a passing regression case.

## Secrets and what is in git
Keys live only in `.env` (gitignored; copy `.env.example`). `make setup` enables a pre-commit hook (`.githooks/pre-commit`)
that refuses commits containing key-shaped strings, `.env` files, databases, dumps, key files or anything over 5 MB, and
`make test` fails if a tracked file contains a secret. Not in git: `var/` (databases, LLM cache, ERPNext snapshot),
`video/*.mp4`, `node_modules/`, raw SEC downloads.
