"""One NATS request/reply question. It is not a board post and it is not stored."""

from __future__ import annotations

import asyncio
import math
import re
import threading
from typing import Any

import nats
from nats import errors as nats_errors

from .queue import MAX_QUESTION_BYTES, QueueRefused, _safe_text

SUBJECT_PREFIX = "ask"
QUEUE_GROUP = "agent-bus"
_WORK = re.compile(r"[A-Za-z0-9-]+")


def ask_subject(work: object) -> str:
    """Return the one ask subject. work is one token of letters, digits, and hyphens."""
    if type(work) is not str or _WORK.fullmatch(work) is None:
        raise QueueRefused()
    return f"{SUBJECT_PREFIX}.{work}"


def ask(url: str, *, work: object, text: object, timeout_s: object) -> dict:
    """Ask one question and wait for one answer from the one queue group.

    Returns {"status": "answered", "answer": str} when a listener answers,
    {"status": "no_responders", "answer": None} at once when nobody is
    listening, and {"status": "timeout", "answer": None} when a listening
    queue member stays silent and the deadline passes. The ask touches no
    queue state: it is not a board post, a task, or a consultation.
    """
    subject = ask_subject(work)
    if not _safe_text(text, MAX_QUESTION_BYTES) or not text.strip():
        raise QueueRefused()
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise QueueRefused()
    if type(url) is not str or not url:
        raise QueueRefused()
    return _run(_ask_once(url, subject, text, float(timeout_s)))


async def _ask_once(url: str, subject: str, text: str, timeout_s: float) -> dict:
    # One ask, one fresh connection. A missing server must refuse quickly
    # (max_reconnect_attempts=0 would mean unlimited retries here), so the
    # ask never parks the caller behind a reconnect backoff.
    connection = await nats.connect(
        servers=[url],
        allow_reconnect=False,
        max_reconnect_attempts=1,
        reconnect_time_wait=0.1,
    )
    try:
        message = await connection.request(subject, text.encode("utf-8"), timeout=timeout_s)
    except nats_errors.NoRespondersError:
        return {"status": "no_responders", "answer": None}
    except nats_errors.TimeoutError:
        return {"status": "timeout", "answer": None}
    finally:
        try:
            await connection.close()
        except nats_errors.Error:
            pass
    return {"status": "answered", "answer": message.data.decode("utf-8")}


def _run(coro: Any) -> Any:
    """Run one ask on a private loop and thread so a sync caller cannot nest loops."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = asyncio.run(coro)
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=target, name="agent-bus-ask", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
