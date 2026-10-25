"""Demo controller: reset to a known morning, fast-forward history, play scenarios.

Everything goes through the real gateway. What is simulated: the calendar (SimClock), the people (SimPeople
decisions are made through the same API as a human's), and the shadow-mode review of historical bills.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from probation.config import REPO_ROOT, load_settings
from probation.gateway.core import GATEWAY, PIP, Gateway
from probation.models import InboxDoc
from probation.pipeline import run_batch
from probation.sim.setup import hire, onboard

HIRE_AT = "2026-04-06T16:00:00Z"
MORNING_AT = "2026-10-24T16:00:00Z"
YESTERDAY_AT = "2026-10-23T18:30:00Z"


def demo_docs(scenario: str) -> list[InboxDoc]:
    out = []
    for line in open(REPO_ROOT / "data" / "inbox" / "demo.jsonl"):
        d = json.loads(line)
        if d.get("scenario") == scenario:
            out.append(InboxDoc(**{k: d[k] for k in ("id", "received_at", "channel", "from_addr", "subject", "body", "attachments")}))
    return out


def snapshot(settings=None) -> None:
    s = settings or load_settings()
    snap = s.var_dir / "pristine"
    snap.mkdir(exist_ok=True)
    for name in ("record.db", "golden.db"):
        import sqlite3
        db = sqlite3.connect(s.var_dir / name)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        shutil.copy(s.var_dir / name, snap / name)


def shadow_history(gw: Gateway, start: str, end: str, every_days: float = 1.6) -> int:
    """Pip reviews historical bills in shadow mode. Ground truth for history is what happened: the bill was
    valid and paid. Pip's call is scored by the gateway's trust engine, which promotes it by the rules."""
    from probation.sim.backtest import history_cases
    from probation.sim.clock import SimClock
    cases = sorted(history_cases(gw, days=400, limit=20000), key=lambda c: c["ref"])
    n = 0
    t = start
    i = 0
    while t < end and i < len(cases):
        gw.clock.set(t)
        st = gw.agent_state()
        c = cases[i]
        i += 1
        f = c["features"]
        ok = f.get("po_match") and not f.get("bank_changed")
        gw.score(bool(ok), False, f"shadow review of {c['ref']} ({'agreed' if ok else 'disagreed'} with the paid outcome)")
        n += 1
        t = SimClock.add_days(t, every_days)
        _ = st
    return n


ERP_DB = ["docker", "exec", "-i", "erpnext-db-1"]


def _erp_db_name() -> str:
    import subprocess
    out = subprocess.run(["docker", "exec", "erpnext-backend-1", "cat", "sites/frontend/site_config.json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["db_name"]


def erpnext_snapshot(settings=None) -> None:
    """After docker/erpnext_load.py and a sync: dump ERPNext's database and golden.db as the demo's starting point."""
    import sqlite3
    import subprocess
    s = settings or load_settings()
    snap = s.var_dir / "pristine_erpnext"
    snap.mkdir(exist_ok=True)
    with open(snap / "erpnext.sql", "wb") as f:
        subprocess.run(ERP_DB + ["mariadb-dump", "-uroot", "-padmin", "--single-transaction", _erp_db_name()], stdout=f, check=True)
    db = sqlite3.connect(s.golden_db)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    shutil.copy(s.golden_db, snap / "golden.db")


def erpnext_restore(s) -> None:
    import subprocess
    snap = s.var_dir / "pristine_erpnext"
    with open(snap / "erpnext.sql", "rb") as f:
        subprocess.run(ERP_DB + ["mariadb", "-uroot", "-padmin", _erp_db_name()], stdin=f, check=True)
    subprocess.run(["docker", "exec", "erpnext-backend-1", "bench", "--site", "frontend", "clear-cache"], check=True, capture_output=True)
    for suf in ("-wal", "-shm"):
        Path(str(s.golden_db) + suf).unlink(missing_ok=True)
    shutil.copy(snap / "golden.db", s.golden_db)


def reset(verbose: bool = True) -> dict:
    s = load_settings()
    if s.record_backend == "erpnext":
        erpnext_restore(s)
    snap = s.var_dir / "pristine"
    if not (snap / "record.db").exists():
        snapshot(s)
    for name in (("record.db", "golden.db") if s.record_backend != "erpnext" else ("record.db",)):
        for suf in ("-wal", "-shm"):
            Path(str(s.var_dir / name) + suf).unlink(missing_ok=True)
        shutil.copy(snap / name, s.var_dir / name)
    for f in (s.work_db, s.bank_db):
        for suf in ("", "-wal", "-shm"):
            Path(str(f) + suf).unlink(missing_ok=True)
    # fresh runtime copy of the skills (approved lessons patch this copy, never the repo) and no regression cases
    shutil.rmtree(s.var_dir / "skills", ignore_errors=True)
    shutil.copytree(REPO_ROOT / "agent_skills", s.var_dir / "skills")
    shutil.rmtree(s.var_dir / "regression", ignore_errors=True)
    s = load_settings()
    gw = Gateway(s)
    gw.clock.set(HIRE_AT)
    hire(gw)
    onboard(gw)
    n = shadow_history(gw, SimClockAdd(HIRE_AT, 1), YESTERDAY_AT)
    # yesterday afternoon: one delivery billed in three parts, each under the limit. Pip pays them (the first mistake).
    gw.clock.set(YESTERDAY_AT)
    first = run_batch(gw, [(d, "demo", "split_first") for d in demo_docs("split_first")], mode="rules")
    # this morning's inbox
    gw.clock.set(MORNING_AT)
    morning = []
    for sc in ("clean_morning", "price_mismatch", "near_duplicate", "new_vendor"):
        morning += [(d, "demo", sc) for d in demo_docs(sc)]
    res = run_batch(gw, morning, mode="rules")
    st = gw.agent_state()
    gw.close()
    out = {"shadow_reviews": n, "tier": st["tier"], "split_first": [it["state"] for it in first],
           "morning": {it["id"]: it["state"] for it in res}}
    if verbose:
        print(json.dumps(out, indent=1))
    return out


def SimClockAdd(iso: str, days: float) -> str:
    from probation.sim.clock import SimClock
    return SimClock.add_days(iso, days)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "reset"
    if cmd == "reset":
        reset()
    elif cmd == "snapshot":
        snapshot()
    elif cmd == "erpnext-snapshot":
        erpnext_snapshot()
