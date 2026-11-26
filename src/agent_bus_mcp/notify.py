"""Offer a board post on the local NATS server before it is written.

An answer means a subscriber has the message, so the board is not written.
Nobody listening, a silent subscriber, or a missing server is the fallback.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any, Callable

_TOKEN = re.compile(r"[A-Za-z0-9-]+")


def board_subject(board: object) -> str | None:
    """Return board.<name>, or None when the name is not one token."""
    if type(board) is not str or _TOKEN.fullmatch(board) is None:
        return None
    return f"board.{board}"


def task_subject(destination: object) -> str | None:
    """Return task.<endpoint>, or None when the name is not one token."""
    if type(destination) is not str or _TOKEN.fullmatch(destination) is None:
        return None
    return f"task.{destination}"


def wake_subject_for_board(board: object) -> str | None:
    """Return wake.board.<name>, the non-consuming post-wake subject.

    The live offer stays on board.<board>. A separate namespace keeps a
    parked observer from being counted as an offer subscriber, and keeps it
    off boards it is not a member of.
    """
    if type(board) is not str or _TOKEN.fullmatch(board) is None:
        return None
    return f"wake.board.{board}"


def offer(url: str | None, subject: str | None, payload: dict, timeout_s: float = 2.0) -> str:
    """Deliver the text of an already-written post to one live listener.

    Return answered, no_responders, timeout, or down. The post is written
    before this runs, so an answer only marks the result delivered "live";
    it does not remove or replace the post.
    """
    if not url or not subject or type(payload) is not dict or timeout_s <= 0:
        return "down"
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError):
        return "down"
    if len(encoded) > 4096:
        return "down"
    try:
        return _run(_offer_once(url, subject, encoded, timeout_s))
    except Exception:
        return "down"


async def _offer_once(url: str, subject: str, encoded: bytes, timeout_s: float) -> str:
    import nats
    from nats import errors as nats_errors

    connection = await nats.connect(
        servers=[url],
        allow_reconnect=False,
        max_reconnect_attempts=1,
        reconnect_time_wait=0.1,
        connect_timeout=0.4,
    )
    try:
        await connection.request(subject, encoded, timeout=timeout_s)
    except nats_errors.NoRespondersError:
        return "no_responders"
    except nats_errors.TimeoutError:
        return "timeout"
    finally:
        try:
            await connection.close()
        except nats_errors.Error:
            pass
    return "answered"


def publish_wake(url: str | None, subject: str | None, payload: dict) -> bool:
    """Publish one non-consuming wake. Return False when there is no server, no subject, or bad payload.

    The payload must already omit the post text. This function does not read
    the queue and does not raise to the writer.
    """
    if not url or not subject or type(payload) is not dict:
        return False
    if "text" in payload:
        return False
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError):
        return False
    if len(encoded) > 512:
        return False
    try:
        _run(_publish_once(url, subject, encoded))
    except Exception:
        return False
    return True


async def _publish_once(url: str, subject: str, encoded: bytes) -> None:
    import nats
    from nats import errors as nats_errors

    connection = await nats.connect(
        servers=[url],
        allow_reconnect=False,
        max_reconnect_attempts=1,
        reconnect_time_wait=0.1,
        connect_timeout=0.4,
    )
    try:
        await connection.publish(subject, encoded)
        await connection.flush(timeout=0.4)
    finally:
        try:
            await connection.close()
        except nats_errors.Error:
            pass


def listen(url: str, handler: Callable[[str, bytes], None]) -> None:
    """Subscribe to board.> and task.> until the process is stopped."""
    _run(_listen(url, handler))


async def _listen(url: str, handler: Callable[[str, bytes], None]) -> None:
    import nats

    connection = await nats.connect(
        servers=[url],
        allow_reconnect=True,
        max_reconnect_attempts=-1,
        reconnect_time_wait=1,
    )

    async def receive(message: Any) -> None:
        handler(message.subject, message.data)

    await connection.subscribe("board.>", cb=receive)
    await connection.subscribe("task.>", cb=receive)
    await connection.flush()
    while True:
        await asyncio.sleep(3600)


def _run(coro: Any) -> Any:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = asyncio.run(coro)
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=target, name="agent-bus-notify", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


class WakeWatch:
    """Subscribe to this member's wake subjects and task.<endpoint>.

    Any message bumps the endpoint's parked wait and sets one event, so the
    caller's recheck returns at once. A missing server, a refused connect,
    or any subscriber error leaves the event unset: the caller's five-second
    recheck stays correct. Nothing here subscribes to the live offer on
    board.<board>, so a parked wait cannot make an offer stall, and nothing
    ever answers, so no message can consume a post.
    """

    def __init__(self, url: str, endpoint: str, boards: list[str], on_wake=None) -> None:
        self._event = threading.Event()
        self._stopping = threading.Event()
        self._url = url
        self._task_subject = task_subject(endpoint)
        self._board_subjects = [wake_subject_for_board(board) for board in boards if wake_subject_for_board(board)]
        self._on_wake = on_wake
        self._thread = threading.Thread(
            target=self._run, name="agent-bus-wake-watch", daemon=True
        )
        self._thread.start()

    def wait_is_set(self) -> bool:
        fired = self._event.is_set()
        self._event.clear()
        return fired

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            asyncio.run(self._subscribe())
        except Exception:
            return

    async def _subscribe(self) -> None:
        import nats
        from nats import errors as nats_errors

        async def on_error(error: Exception) -> None:
            return

        try:
            connection = await asyncio.wait_for(
                nats.connect(
                    servers=[self._url],
                    connect_timeout=0.8,
                    allow_reconnect=False,
                    error_cb=on_error,
                ),
                timeout=1.5,
            )
        except Exception:
            return

        async def receive(message: Any) -> None:
            self._event.set()
            if self._on_wake is not None:
                try:
                    self._on_wake()
                except Exception:
                    pass

        try:
            for subject in self._board_subjects + ([self._task_subject] if self._task_subject else []):
                await connection.subscribe(subject, cb=receive)
            await connection.flush()
        except Exception:
            try:
                await connection.close()
            except nats_errors.Error:
                pass
            return
        while not self._stopping.is_set():
            await asyncio.sleep(0.1)
        try:
            await connection.close()
        except nats_errors.Error:
            pass
