"""In-memory waiters for one host process.

Nothing in this module is durable, and it does not dial out. A waiter holds
the hub lock only while registering or sleeping. Sleep releases that lock.
Callers that already hold the queue flock may bump a generation; they must
not take the queue flock while holding the hub lock.
"""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_GEN: dict[str, int] = {}
_CONDS: dict[str, threading.Condition] = {}
_WAITING: set[str] = set()
_TOTAL = 0
MAX_WAITERS = 32


def waiter_cap() -> int:
    """Process cap for parked waits. Unset means 32. A value cannot raise it."""
    raw = os.environ.get("AGENT_BUS_WAIT_CAP", "")
    if raw.isdigit():
        value = int(raw)
        if 1 <= value <= MAX_WAITERS:
            return value
    return MAX_WAITERS


def generation(endpoint: str) -> int:
    with _LOCK:
        return _GEN.get(endpoint, 0)


def bump(endpoint: str) -> None:
    """Record that this endpoint should look again. Safe to call under the queue flock."""
    if not isinstance(endpoint, str):
        return
    with _LOCK:
        _GEN[endpoint] = _GEN.get(endpoint, 0) + 1
        condition = _CONDS.get(endpoint)
        if condition is not None:
            condition.notify_all()


def try_reserve(endpoint: str) -> bool:
    """Take this endpoint's single slot. False when it, or the process cap, is full."""
    global _TOTAL
    with _LOCK:
        if endpoint in _WAITING or _TOTAL >= waiter_cap():
            return False
        _WAITING.add(endpoint)
        _TOTAL += 1
        if endpoint not in _CONDS:
            _CONDS[endpoint] = threading.Condition(_LOCK)
        return True


def release(endpoint: str) -> None:
    global _TOTAL
    with _LOCK:
        if endpoint in _WAITING:
            _WAITING.discard(endpoint)
            _TOTAL -= 1


def wait_for_change(endpoint: str, ticket: int, timeout: float) -> None:
    """Sleep until the generation moves or the slice elapses. Does not extend a deadline."""
    if timeout <= 0:
        return
    with _LOCK:
        condition = _CONDS.get(endpoint)
        if condition is None:
            condition = threading.Condition(_LOCK)
            _CONDS[endpoint] = condition
        if _GEN.get(endpoint, 0) != ticket:
            return
        condition.wait(timeout)
