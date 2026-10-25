"""Seed the system of record and the inbox. Run: make seed."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from company import make_vendors  # noqa: E402
from history import Counters, generate_history, seed_static  # noqa: E402
from invoices import Planter, build_demo_set, build_eval_set, demo_vendors  # noqa: E402
from probation.config import load_settings  # noqa: E402
from probation.ports.sqlite import SqliteRecord  # noqa: E402

REPO = HERE.parents[1]
SEED = 1310


def main(s=None, write_files: bool = True) -> None:
    t0 = time.time()
    import dataclasses
    s = s or dataclasses.replace(load_settings(), record_backend="sqlite")  # the generator always writes the SQLite record
    for f in (s.record_db, s.golden_db, s.work_db, s.bank_db):
        for suffix in ("", "-wal", "-shm"):
            Path(str(f) + suffix).unlink(missing_ok=True)
    rng = random.Random(SEED)
    vendors = make_vendors(rng)
    vendors += demo_vendors(len(vendors) + 1, rng)
    rec = SqliteRecord(s.record_db)
    ctr = Counters()
    seed_static(rec, vendors, rng, ctr)
    h = generate_history(rec, vendors, rng, ctr)
    rec.commit()
    p = Planter(rec, vendors, rng, ctr, h["formatters"])
    build_eval_set(p)
    build_demo_set(p)
    rec.commit()
    if not write_files:
        return
    inbox = REPO / "data" / "inbox"
    inbox.mkdir(exist_ok=True)
    labels = REPO / "data" / "labels"
    labels.mkdir(exist_ok=True)
    for set_name in ("eval", "demo"):
        with open(inbox / f"{set_name}.jsonl", "w") as f:
            for d in p.docs:
                if d["set"] == set_name:
                    f.write(json.dumps(d) + "\n")
        with open(labels / f"{set_name}.jsonl", "w") as f:
            for lab in p.labels:
                if lab["set"] == set_name:
                    f.write(json.dumps(lab) + "\n")
    d, c = rec.gl_totals()
    assert d == c, "ledger does not balance after seeding"
    kinds: dict[str, int] = {}
    for lab in p.labels:
        if lab["set"] == "eval":
            kinds[lab["label"]] = kinds.get(lab["label"], 0) + 1
    print(f"vendors={len(vendors)} history={h['stats']} eval_docs={sum(kinds.values())} "
          f"demo_docs={sum(1 for x in p.docs if x['set'] == 'demo')} gl_balanced={d == c} in {time.time() - t0:.1f}s")
    print("eval mix:", dict(sorted(kinds.items())))


if __name__ == "__main__":
    main()
