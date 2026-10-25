"""Paths and settings. Everything reads from here so tests can point at a temp dir."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    var_dir: Path
    skills_dir: Path
    labels_dir: Path
    regression_dir: Path
    anthropic_api_key: str
    agent_model: str
    extract_model: str
    max_llm_calls: int
    record_backend: str = "sqlite"

    @property
    def record_db(self) -> Path:
        return self.var_dir / "record.db"

    @property
    def golden_db(self) -> Path:
        # one read model per system of record, so the ERPNext demo and the SQLite eval never share a file
        return self.var_dir / ("golden_erpnext.db" if self.record_backend == "erpnext" else "golden.db")

    @property
    def work_db(self) -> Path:
        return self.var_dir / "work.db"

    @property
    def bank_db(self) -> Path:
        return self.var_dir / "simbank.db"

    @property
    def llm_cache_dir(self) -> Path:
        return self.var_dir / "llm_cache"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key) and self.max_llm_calls > 0


def load_settings(var_dir: Path | None = None) -> Settings:
    vd = var_dir or Path(os.environ.get("PROBATION_VAR", REPO_ROOT / "var"))
    vd.mkdir(parents=True, exist_ok=True)
    return Settings(
        var_dir=vd,
        # the running app uses a runtime copy under var/ (seeded from agent_skills/ at reset), so an approved skill
        # patch changes the live agent without editing the repo's v1 files
        skills_dir=Path(os.environ.get("PROBATION_SKILLS", vd / "skills" if (vd / "skills").exists() else REPO_ROOT / "agent_skills")),
        labels_dir=REPO_ROOT / "data" / "labels",
        regression_dir=Path(os.environ.get("PROBATION_REGRESSION", vd / "regression")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        agent_model=os.environ.get("AGENT_MODEL", "claude-sonnet-5-5"),
        extract_model=os.environ.get("EXTRACT_MODEL", "claude-haiku-4-5"),
        max_llm_calls=int(os.environ.get("MAX_LLM_CALLS", "200") or 0),
        record_backend=os.environ.get("RECORD_BACKEND", "sqlite").strip().lower() or "sqlite",
    )
