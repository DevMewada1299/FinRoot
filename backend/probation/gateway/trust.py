"""Tier promotion rules. Pure function over agent_state; the gateway applies the result through transition()."""
from __future__ import annotations

import datetime as dt

# tier -> (correct decisions needed, extra condition description)
PROMOTE = {
    0: (20, "20 correct suggestions, no serious mistakes"),
    1: (60, "60 correct decisions, 2 approved lessons or policies"),
    2: (150, "150 correct, none wrong in 60 days, 98% on the replay exam"),
}


def promotion_check(st: dict, now: str) -> tuple[bool, str]:
    tier = st["tier"]
    if tier >= 3 or (st["tightened_until"] and st["tightened_until"] > now):
        return False, "top tier or tightened"
    need, why = PROMOTE[tier]
    if st["correct"] < need:
        return False, f"{st['correct']} of {need} correct"
    if tier == 0 and st["serious_mistakes"]:
        return False, "serious mistake on record"
    if tier == 1 and (st["learned_count"] or 0) < 2:
        return False, "needs 2 approved lessons or policies"
    if tier == 2:
        if st["last_wrong_at"]:
            last = dt.datetime.fromisoformat(st["last_wrong_at"].replace("Z", "+00:00"))
            if dt.datetime.fromisoformat(now.replace("Z", "+00:00")) - last < dt.timedelta(days=60):
                return False, "a wrong decision in the last 60 days"
        if (st["exam_score"] or 0) < 0.98:
            return False, "replay exam below 98%"
    return True, why
