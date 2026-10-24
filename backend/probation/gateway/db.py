"""work.db: the system of work. Only the gateway opens it writable."""
from __future__ import annotations

import sqlite3
from pathlib import Path

WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS counter(name TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS sim_clock(id INTEGER PRIMARY KEY CHECK(id=1), now TEXT);
CREATE TABLE IF NOT EXISTS inbox_doc(id TEXT PRIMARY KEY, received_at TEXT, channel TEXT, from_addr TEXT, subject TEXT,
  body TEXT, attachments TEXT, set_name TEXT, scenario TEXT);
CREATE TABLE IF NOT EXISTS work_item(id TEXT PRIMARY KEY, kind TEXT, state TEXT, doc_id TEXT, supplier TEXT, entity_id TEXT,
  amount_cents INTEGER, extracted TEXT, facts TEXT, proposal TEXT, routing TEXT, required_role TEXT, requested_by TEXT,
  proposed_by TEXT, approved_by TEXT, posted_by TEXT, record_invoice TEXT, record_payment TEXT, transfer_id TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS w_wi_state ON work_item(state);
CREATE INDEX IF NOT EXISTS w_wi_supplier ON work_item(supplier);
CREATE TABLE IF NOT EXISTS audit_log(seq INTEGER PRIMARY KEY, at TEXT, actor TEXT, action TEXT, subject TEXT, allowed INTEGER,
  reason TEXT, diff TEXT, evidence TEXT, prev_hash TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS w_audit_subject ON audit_log(subject);
CREATE TABLE IF NOT EXISTS agent_state(agent_id TEXT PRIMARY KEY, tier INTEGER, score INTEGER, correct INTEGER,
  serious_mistakes INTEGER, last_wrong_at TEXT, tightened_until TEXT, tightened_from_tier INTEGER, frozen_vendors TEXT,
  learned_count INTEGER, exam_score REAL);
CREATE TABLE IF NOT EXISTS tier_event(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, agent_id TEXT, from_tier INTEGER,
  to_tier INTEGER, kind TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS policy(id TEXT PRIMARY KEY, status TEXT, text TEXT, body TEXT, source TEXT, evidence TEXT,
  backtest TEXT, created_at TEXT, decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS precedent(id TEXT PRIMARY KEY, at TEXT, work_item TEXT, vendor_id TEXT, tags TEXT,
  agent_proposal TEXT, human_decision TEXT, reason TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS incident(id TEXT PRIMARY KEY, at TEXT, work_item TEXT, kind TEXT, detected_by TEXT,
  severity TEXT, details TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS lesson(id TEXT PRIMARY KEY, at TEXT, incident_id TEXT, tags TEXT, what_happened TEXT,
  root_cause TEXT, rule_text TEXT, proposed_action TEXT, status TEXT, approved_by TEXT, evidence TEXT, hits INTEGER,
  last_hit TEXT, backtest TEXT);
CREATE TABLE IF NOT EXISTS skill_version(skill TEXT, version INTEGER, body TEXT, source_lesson TEXT, approved_by TEXT,
  at TEXT, PRIMARY KEY(skill, version));
CREATE TABLE IF NOT EXISTS eval_run(id TEXT PRIMARY KEY, at TEXT, label TEXT, metrics TEXT);
CREATE TABLE IF NOT EXISTS regression_case(id TEXT PRIMARY KEY, incident_id TEXT, lesson_id TEXT, doc_ids TEXT,
  expect TEXT, created_at TEXT);
"""


def open_work(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path), check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(WORK_SCHEMA)
    return db


def next_id(db: sqlite3.Connection, name: str, prefix: str, width: int = 6) -> str:
    """Call inside an open transaction."""
    db.execute("INSERT INTO counter(name, n) VALUES(?, 0) ON CONFLICT(name) DO NOTHING", (name,))
    db.execute("UPDATE counter SET n = n + 1 WHERE name=?", (name,))
    n = db.execute("SELECT n FROM counter WHERE name=?", (name,)).fetchone()[0]
    return f"{prefix}{n:0{width}d}"
