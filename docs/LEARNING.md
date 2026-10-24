# Learning, skills and memory

The goal: Pip gets measurably better from corrections and from its own mistakes, the learning survives restarts and context windows, and nothing it learns can be used to make it less safe.

## Two kinds of learning
- **Skills** are procedures: how to do a task. Files in `agent_skills/<name>/SKILL.md`, loaded on demand.
- **Memory** is experience: what happened and what we decided. Rows in `work.db`, retrieved by `search_memory`.

`agent_skills/` is the runtime agent's skill folder. Do not confuse it with `.claude/skills` (for Claude Code developing this repo).

## Skills (progressive disclosure)
The system prompt carries only an index (name and one-line description). The agent calls `load_skill(name)` to read the full body when it needs it. Each skill has frontmatter (`name`, `description`, `version`) and short numbered steps plus "common mistakes".

Initial skills, in build order:
1. `extract-invoice` (fields, line items, tax-inclusive vs exclusive totals)
2. `three-way-match` (PO, goods receipt, invoice; tolerances; partial receipts)
3. `verify-bank-change` (compare to history and sender, never pay, route to phone check)
4. `detect-duplicates` (exact, near, split-to-dodge-limit)
5. `handle-untrusted-text` (treat text as data, report attempts, never obey)
6. `decide-or-ask` (risk = exposure x uncertainty, tier and approval-matrix routing, what to put in the question to the human)
7. `draft-policy` (from precedents to the JSON DSL, with evidence ids)
8. `write-postmortem` (turn an incident into a structured lesson)
9. `explain-decision` (answer "why?" using only the audit log and cited records)
10. `early-pay-discount` (stretch)

Skill versions: the current text is on disk; every version is also stored in `skill_version(skill, version, body, source_lesson, approved_by, at)`. A skill changes only through an approved `skill_patch` (a diff proposed by the agent, reviewed by a human, applied by the gateway).

## Memory tables (`work.db`)
- `precedent(id, at, work_item, vendor_id, tags json, agent_proposal, human_decision, reason, outcome)`: every human decision on an item Pip escalated or got wrong.
- `incident(id, at, work_item, kind, detected_by, severity, details json)`. `kind`: `agent_wrong`, `human_override`, `late_discovery`, `near_miss`. `detected_by`: `human`, `ground_truth`, `gateway`. `severity`: `minor` or `critical`.
- `lesson(id, at, incident_id, tags json, what_happened, root_cause, rule_text, proposed_action json, status, approved_by, evidence json, hits, last_hit)`. `proposed_action.type` is one of `memory_note`, `policy`, `skill_patch`. `status`: `proposed`, `approved`, `rejected`, `retired`.
- `policy` as in `ARCHITECTURE.md`, with `source` in (`onboarding`, `correction`, `lesson`) and a stored `backtest`.

## Retrieval
Before deciding, the agent calls `search_memory(vendor_id, tags, limit)`. It returns approved lessons, matching precedents and active policies, ranked by vendor match, tag overlap and recency. Keep it simple: no vector database. Retrieved items are shown to the agent with their ids; if the agent relies on one, it cites the id and the gateway checks it exists and is approved. Increment `hits` and `last_hit` so we can show which lessons earn their keep and retire the ones that do not.

## The learning loops
**1. Onboarding.** A read-only SQL job over 12 months of history proposes policies with evidence counts (for example "known vendor, PO matches within 3%, same bank account: pay"). Each is replay-tested before a human sees it.

**2. Corrections.** When a human overrides Pip, write a `precedent`. After about three similar precedents (same tag set), Pip drafts a general policy with the `draft-policy` skill.

**3. Mistakes.**
1. Detect: a human override, a late discovery (for example a duplicate found after payment), a ground-truth mismatch in a simulation, or a gateway near-miss. Create an `incident`.
2. Post-mortem: Pip loads `write-postmortem` and reads only the audit log and cited records to write a `lesson`: what happened, root cause, the rule, and a `proposed_action`.
3. Review: a human (or the simulated controller, using a short rubric) approves or rejects in the Policy Lab. The Lab shows incident, lesson and the exact change.
4. Apply: `memory_note` becomes retrievable; `policy` goes through the replay test then activates; `skill_patch` writes a new skill version.
5. Regression: the failing case is added to `eval/regression/` and must pass from then on. A critical incident also demotes Pip per the tier rules.

## Safety rules (memory poisoning is the main risk)
- Memory and skills are written only from: human decisions through the gateway, outcomes verified against ground truth, and agent-authored lessons that stay `proposed` until a human approves.
- Content from invoices or emails never enters a lesson as an instruction. It may appear only inside an `evidence` field as escaped quoted data.
- A validator rejects any lesson or skill patch that touches hard limits, tiers, freezes or approval thresholds, or that contains instruction-like text aimed at bypassing a check. Lessons may tighten, add checks, or explain; they may not relax.
- An unapproved lesson has no effect on behaviour. There is a test for this.
- Tier effects: critical incident goes to L0; a freeze or tightening is never lifted by memory.

## What the demo must show ("Pip's second mistake")
1. A scenario Pip gets wrong the first time (best candidate: `split_to_dodge_limit`, one large bill split into several under the tier limit).
2. The controller catches it. Incident, post-mortem and lesson appear in the Policy Lab.
3. The controller approves; the replay test shows it catches the original and a few variants.
4. The same pattern, shuffled, arrives again and is caught before payment. The Scorecard shows the metric before and after.

## Measuring learning honestly
Run the same labeled test set at three points (before learning, after onboarding policies, after approved lessons). Show recall on each planted problem and the ask-human rate falling. Report only numbers produced by `make eval`; say how many invoices and how many cached versus live decisions.
