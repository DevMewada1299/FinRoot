"""Runtime skills in agent_skills/<name>/SKILL.md. The prompt carries only the index; load_skill reads a body."""
from __future__ import annotations

import re
from pathlib import Path


def _front(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def index(skills_dir: Path) -> list[dict]:
    out = []
    for p in sorted(Path(skills_dir).glob("*/SKILL.md")):
        fm = _front(p.read_text())
        out.append({"name": fm.get("name", p.parent.name), "description": fm.get("description", ""),
                    "version": int(fm.get("version", "1") or 1)})
    return out


def load(skills_dir: Path, name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        return "unknown skill"
    p = Path(skills_dir) / name / "SKILL.md"
    return p.read_text() if p.exists() else "unknown skill"
