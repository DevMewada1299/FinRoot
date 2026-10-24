import dataclasses
import gc
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from probation.config import REPO_ROOT, load_settings
from probation.gateway.core import GATEWAY, PIP, Gateway
from probation.models import InboxDoc

sys.path.insert(0, str(REPO_ROOT / "data" / "generate"))


def _checkpoint(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    import seed
    from probation.ports.sqlite import SqliteRecord
    from probation.sync.job import run_sync
    d = tmp_path_factory.mktemp("seed")
    s = load_settings(d)
    seed.main(s, write_files=False)
    gc.collect()
    run_sync(SqliteRecord(s.record_db), s.golden_db)
    gc.collect()
    for f in (s.record_db, s.golden_db):
        _checkpoint(f)
    return d


@pytest.fixture
def settings(seeded, tmp_path):
    for name in ("record.db", "golden.db"):
        shutil.copy(seeded / name, tmp_path / name)
    shutil.copytree(REPO_ROOT / "agent_skills", tmp_path / "skills")
    s = load_settings(tmp_path)
    return dataclasses.replace(s, skills_dir=tmp_path / "skills", regression_dir=tmp_path / "regression",
                               anthropic_api_key="", max_llm_calls=0)


@pytest.fixture
def gw(settings):
    from probation.sim.setup import onboard
    g = Gateway(settings)
    onboard(g)
    return g


def set_tier(gw: Gateway, tier: int) -> None:
    st = gw.agent_state()
    gw.transition("agent", PIP, "promote", GATEWAY,
                  patch={"tier": tier, "tier_event": {"from": st["tier"], "to": tier, "kind": "promote", "reason": "test setup"}},
                  reason="test setup")


def load_docs(set_name: str) -> list[dict]:
    return [json.loads(l) for l in open(REPO_ROOT / "data" / "inbox" / f"{set_name}.jsonl")]


def to_doc(d: dict) -> InboxDoc:
    return InboxDoc(**{k: d[k] for k in ("id", "received_at", "channel", "from_addr", "subject", "body", "attachments")})


def docs_for(set_name: str, scenario: str) -> list[InboxDoc]:
    return [to_doc(d) for d in load_docs(set_name) if d.get("scenario") == scenario]
