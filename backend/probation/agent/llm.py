"""Thin wrapper over the Anthropic SDK: disk cache keyed by input hash, and a MAX_LLM_CALLS cost guard."""
from __future__ import annotations

import hashlib
import json
import threading

from probation.config import Settings

_lock = threading.Lock()
_calls = {"n": 0}


class LLMUnavailable(Exception):
    pass


def calls_made() -> int:
    return _calls["n"]


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def create(settings: Settings, **params) -> dict:
    """Returns the response as a plain dict (cached on disk). Raises LLMUnavailable if no key or over budget."""
    key = _key(params)
    path = settings.llm_cache_dir / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not settings.llm_enabled:
        raise LLMUnavailable("no ANTHROPIC_API_KEY or MAX_LLM_CALLS=0")
    with _lock:
        if _calls["n"] >= settings.max_llm_calls:
            raise LLMUnavailable(f"MAX_LLM_CALLS={settings.max_llm_calls} reached")
        _calls["n"] += 1
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model = params.get("model", "")
    if model.startswith(("claude-sonnet-5-5", "claude-opus-5-5")):
        # refusal fallback routed server-side by category
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        resp = client.messages.create(**params)
    out = resp.to_dict()
    settings.llm_cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out
