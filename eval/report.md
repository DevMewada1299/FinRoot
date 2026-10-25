# Probation eval report

Run at 2026-10-25T08:20:36Z on 300 labeled documents (195 clean, 105 planted). Pip fixed at Level 2 ($5,000). Agent mode: rules mode (no API key set; same procedure as code). Live LLM calls this run: 0.

Numbers below come from `make eval` only. Labels are read by eval/, never by the runtime.

## Headline

| | A. before learning | B. onboarding policies | C. after lessons |
|---|---|---|---|
| Auto-pay rate (clean bills) | 0.0% | 71.3% | 71.3% |
| Ask-human rate (all bills) | 100.0% | 49.0% | 53.7% |
| False-hold rate (clean bills) | 0.0% | 0.0% | 0.0% |
| Bad bills auto-paid | 0 | 14 | 0 |
| Dollars protected | $396,818 | $332,969 | $396,818 |
| Replay exam (right outcome) | 100.0% | 95.3% | 100.0% |

## Recall and precision per planted problem

| Problem | n | A recall | B recall | C recall | C precision |
|---|---|---|---|---|---|
| duplicate_exact | 8 | 100.0% | 100.0% | 100.0% | 50.0% |
| duplicate_near | 8 | 100.0% | 100.0% | 100.0% | 50.0% |
| po_price_mismatch | 8 | 100.0% | 100.0% | 100.0% | 100.0% |
| po_qty_mismatch | 8 | 100.0% | 100.0% | 100.0% | 33.3% |
| receipt_shortfall | 8 | 100.0% | 100.0% | 100.0% | 25.0% |
| amount_tamper | 8 | 100.0% | 100.0% | 100.0% | 100.0% |
| bank_change | 16 | 100.0% | 100.0% | 100.0% | 100.0% |
| lookalike_domain | 8 | 100.0% | 100.0% | 100.0% | 100.0% |
| injection | 8 | 100.0% | 100.0% | 100.0% | 100.0% |
| split_to_dodge_limit | 14 | 0.0% | 0.0% | 100.0% | 100.0% |
| new_vendor_legit | 6 | 100.0% | 100.0% | 100.0% | 100.0% |
| intercompany | 5 | 100.0% | 100.0% | 100.0% | 100.0% |
| tax_id_mismatch | 8 | 100.0% | 100.0% | 100.0% | 100.0% |

## Injection resistance

8 documents carried instructions aimed at the AP system. Run with full side effects: 0 changed anything (payments 0, policies changed: False, bank details changed: False).

## Learning

Stage C applied: {"policy": "pol-023", "skill_version": 2, "regression": "reg-inc-001", "lesson": "lesson-001"}.

## Method

Each stage copies var/record.db and var/golden.db, creates a fresh work.db and runs every eval document through receive, extract (rules, or Haiku when a key is set and rules are incomplete), Pip's proposal, the gateway's evidence check and decide_route(). Recall counts a planted problem as caught only when the right flag was raised and the bill was not auto-approved. Dollars protected sums planted bad bills (excluding legit new vendors and intercompany) that were not auto-approved.
