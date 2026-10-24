"""In-process event bus for the SSE feed. Best effort: a slow listener drops events, never blocks the gateway."""
from __future__ import annotations

import queue
import threading

_lock = threading.Lock()
_subs: list[queue.Queue] = []


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subs:
            _subs.remove(q)


def publish(kind: str, data: dict) -> None:
    with _lock:
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait({"event": kind, "data": data})
        except queue.Full:
            pass
