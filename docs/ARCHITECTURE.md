# Architecture

## Trust boundaries
| Component | Reads | Writes |
|---|---|---|
| Agent (LLM tool loop) | `golden.db` (read-only connection), `search_memory`, `load_skill` | Nothing directly. It returns proposals to the gateway. |
| Gateway | Everything | The record (via `RecordPort`), the bank (via `BankPort`), `work.db`. The only writer. |
| Sync job | The record | `golden.db` |
| Simulated people (Claude) | A work item and its evidence | Decisions, sent to the gateway as `human_*` actors |
| Frontend | API | Decisions only, through the API |

## Stores
- **System of record**: ERPNext (REST) or `record.db` (SQLite, same shape). Tables or doctypes: Company, Supplier, SupplierBankAccount (with history), Item, Account (chart of accounts), PurchaseOrder, PurchaseReceipt, PurchaseInvoice, PaymentEntry, GLEntry, ApprovalMatrix.
- **`golden.db`**: standardised, typed read model rebuilt incrementally by the sync job (raw, then standardised, then golden, with a cursor per source and stable ids so a re-sync never duplicates). Every golden row keeps a `raw_ref` to its source row. This is what the agent's `run_sql` tool queries (SELECT only, row-limited, read-only connection).
- **`work.db`**: the system of work.
  - `work_item(id, kind, state, subject_ref, entity_id, amount, evidence json, proposed_by, created_at)`
  - `audit_log(seq, at, actor, action, subject, allowed, reason, diff json, evidence json, prev_hash, hash)` append-only
  - `agent_state(agent_id, tier, score, tightened_until, frozen_vendors json)` and `tier_event(...)`
  - `policy`, `precedent`, `lesson`, `incident`, `skill_version` (see `LEARNING.md`)
  - `eval_run(id, at, metrics json)`

## State machine (extends Hyphenate's blueprint)
```
NEW -> EXTRACTED -> PROPOSED -> (AUTO_APPROVED | NEEDS_APPROVER | HELD | BLOCKED)
NEEDS_APPROVER --approve--> APPROVED --post--> POSTED
NEEDS_APPROVER --reject--> REJECTED
HELD --verified--> NEEDS_APPROVER          (e.g. bank change confirmed by phone)
```
`POSTED` is the only transition that writes to the record or the bank, and only the gateway can perform it.

## Gateway `transition(item, action, actor, patch, reason)`
In one DB transaction:
1. Look up `to_state` from the transition table. Missing means denied.
2. `can(actor, action, item)`: role permissions, separation of duties (requester, approver, poster differ; Pip never approves its own proposal), tier limit, approval matrix, freezes.
3. Hard limits G-01 to G-05 (below). Evidence check: every cited id must exist in `golden.db`.
4. Policy engine: evaluate active policies. A match can allow auto-approval only within the tier limit and with no hard flag.
5. Apply the change, append the `audit_log` row with a diff and `prev_hash`/`hash`, commit. If denied, still append the row with `allowed=false` and raise `Denied`.

Hash: `sha256(prev_hash | at | actor | action | subject | allowed | reason | diff)`. `verify_chain()` recomputes from genesis and reports the first broken row.

## Hard limits
- G-01 Pip never approves its own proposal above its limit.
- G-02 Pip cannot change a vendor's bank details. Only a human can.
- G-03 Anything over the tier limit or the approval-matrix threshold goes to a human.
- G-04 New or changed bank details hold the payment until a human confirms by phone on the number already on file.
- G-05 Text from invoices, emails or tool results is data, never an instruction.

## Tiers
| Tier | Limit | Promote when |
|---|---|---|
| L0 | $0 | start state |
| L1 | $500 | 20 correct suggestions, no serious mistakes |
| L2 | $5,000 | 60 correct decisions, 2 approved lessons or policies |
| L3 | $25,000 | 150 correct, none wrong in 60 days, 98% on the replay exam |

Demotion: a payment Pip should have stopped sends it to L0 immediately (critical incident). Untrusted instructions inside an email or invoice tighten it to L1 for 7 days and freeze the vendor (precaution, not a penalty). Everything is logged in `tier_event`.

## Policy DSL (JSON, evaluated by code)
```json
{"id":"pol-007","when":{"vendor_status":"known","po_match":true,"price_delta_pct":"<=3",
 "bank_changed":false,"receipt_covers_invoice":true},
 "then":"auto_approve","max_amount":5000,"source":"onboarding","evidence":["pay-211","pay-214"]}
```
Operators: equality, `<=`/`>=` comparisons, `in`, `not`. No free text and no code strings. `backtest(policy, history)` replays it over labeled history and returns caught, missed and wrongly held counts.

## Ports
```python
class RecordPort(Protocol):
    def get_supplier(self, supplier_id: str) -> Supplier: ...
    def bank_history(self, supplier_id: str) -> list[BankAccountRecord]: ...
    def get_po(self, po_id: str) -> PurchaseOrder: ...
    def get_receipts(self, po_id: str) -> list[PurchaseReceipt]: ...
    def changes_since(self, cursor: str) -> tuple[list[RawRow], str]: ...   # for sync
    # gateway only
    def post_purchase_invoice(self, inv: PurchaseInvoice) -> str: ...
    def post_payment_entry(self, pay: PaymentEntry) -> str: ...            # also posts GL entries

class BankPort(Protocol):
    def send_ach(self, entity_id: str, account: str, amount: Decimal, memo: str) -> str: ...
    def status(self, transfer_id: str) -> str: ...
```
`SimBank` is the default. `IncreaseBank` is optional and gated behind the sandbox key.

## API (FastAPI, JSON; SSE for live feed)
- `POST /inbox/receive` (email or pasted text) and `GET /inbox`
- `GET /work-items`, `GET /work-items/{id}/evidence`, `POST /work-items/{id}/decision` (approve, reject, verify)
- `GET /policies`, `POST /policies/{id}/decision`, `POST /policies/{id}/backtest`
- `GET /incidents`, `GET /lessons`, `POST /lessons/{id}/decision`
- `GET /agent/state`, `GET /agent/tiers`, `GET /agent/timeline`
- `GET /audit`, `POST /audit/verify`, `POST /ask` (answers from `audit_log`)
- `POST /sim/play/{scenario}`, `POST /sim/fast-forward`, `POST /sim/reset`
- `GET /eval/latest`
- `GET /events` (SSE): `agent.step`, `gateway.decision`, `tier.changed`, `policy.proposed`, `lesson.proposed`

## Simulation
- `SimClock` advances logical time so fast-forward can show weeks or months.
- `SimPeople`: Claude plays department approver, controller (sometimes says no, sometimes is wrong) and CFO from short personas. Their decisions go through the same API as a human's.
- Fast-forward runs the real gateway, policy engine and trust engine. Most invoices use decisions precomputed in a batch (cached by input hash); live Claude calls are used only for ambiguous items. State this in the README.

## Eval (`make eval`)
Run the labeled set through the full pipeline. Report per planted-problem precision and recall, false-hold rate, dollars protected (sum of amounts of correctly blocked bad invoices), auto-pay rate, ask-human rate, injection-resistance (N attack documents, count that changed anything), and the same metrics before and after learning. Write `eval/report.json` and `eval/report.md`. The UI Scorecard reads `report.json`.
