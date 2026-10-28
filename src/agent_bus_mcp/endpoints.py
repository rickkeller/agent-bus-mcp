"""Configured endpoints. The bus carries a chat or a pointer. It does not call a model."""

from __future__ import annotations

import json
from pathlib import Path

from .queue import DurableQueue, QueueRefused, RoutePolicy

CHAT_TTL_SECONDS = 86400
_KINDS = {"chat": "consultation", "handoff": "task"}


def policy_from_config(document: object) -> RoutePolicy:
    """Build the allowlist. An edge names a sender, a receiver, and chat or handoff."""
    if type(document) is not dict:
        raise QueueRefused()
    endpoints = document.get("endpoints")
    allow = document.get("allow")
    if type(endpoints) is not list or type(allow) is not list or not endpoints or not allow:
        raise QueueRefused()
    routes: dict[tuple[str, str], list[str]] = {}
    for edge in allow:
        if type(edge) is not dict or set(edge) != {"from", "to", "kinds"}:
            raise QueueRefused()
        source, destination, kinds = edge["from"], edge["to"], edge["kinds"]
        if source == destination or type(kinds) is not list or not kinds:
            raise QueueRefused()
        modes: list[str] = []
        for kind in kinds:
            mode = _KINDS.get(kind)
            if mode is None or mode in modes:
                raise QueueRefused()
            modes.append(mode)
        if (source, destination) in routes:
            raise QueueRefused()
        routes[(source, destination)] = modes
    return RoutePolicy(agents=endpoints, routes=routes)


def load_config(path: Path) -> RoutePolicy:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueRefused() from exc
    return policy_from_config(document)


def open_endpoint(state_dir: Path, config_path: Path, endpoint_id: str) -> DurableQueue:
    """Open one endpoint. The id is this process. A message cannot choose another sender."""
    return DurableQueue(
        Path(state_dir),
        policy=load_config(config_path),
        agent_id=endpoint_id,
    )


def post_chat(queue: DurableQueue, *, to: str, text: str, key: str) -> dict:
    """Send one addressed chat message. It expires. It does not become a task."""
    return queue.ask_question(
        question=text,
        origin_ref="chat",
        idempotency_key=key,
        expires_in_seconds=CHAT_TTL_SECONDS,
        destination_id=to,
    )


def post_handoff(queue: DurableQueue, *, to: str, pointer: str, key: str) -> str:
    """Send a pointer. The bus does not carry file bytes and does not fetch the pointer."""
    if type(pointer) is not str or not pointer.startswith("https://"):
        raise QueueRefused()
    return queue.enqueue(
        goal="read the pointer",
        references=[pointer],
        idempotency_key=key,
        destination_id=to,
    )
