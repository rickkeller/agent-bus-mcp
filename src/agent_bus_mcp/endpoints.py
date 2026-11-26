"""Configured endpoints. The bus carries a chat or a pointer. It does not call a model."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from .queue import DurableQueue, QueueRefused, RoutePolicy, _identity, _instant, _parse_stamp
from . import webhook
from .notify import board_subject, offer, publish_wake, task_subject, wake_subject_for_board

CHAT_TTL_SECONDS = 86400
BOARD_TEXT_BYTES = 8192
BOARD_PAGE_LIMIT = 20
_KINDS = {"chat": "consultation", "handoff": "task"}


def policy_from_config(document: object) -> RoutePolicy:
    """Build the allowlist. An edge names a sender, a receiver, and chat or handoff."""
    if type(document) is not dict:
        raise QueueRefused("config is invalid")
    endpoints = document.get("endpoints")
    allow = document.get("allow")
    if type(endpoints) is not list or type(allow) is not list or not endpoints or not allow:
        raise QueueRefused("config is invalid")
    routes: dict[tuple[str, str], list[str]] = {}
    for edge in allow:
        if type(edge) is not dict or set(edge) != {"from", "to", "kinds"}:
            raise QueueRefused("config is invalid")
        source, destination, kinds = edge["from"], edge["to"], edge["kinds"]
        if source == destination or type(kinds) is not list or not kinds:
            raise QueueRefused("config is invalid")
        modes: list[str] = []
        for kind in kinds:
            mode = _KINDS.get(kind)
            if mode is None or mode in modes:
                raise QueueRefused("config is invalid")
            modes.append(mode)
        if (source, destination) in routes:
            raise QueueRefused("config is invalid")
        routes[(source, destination)] = modes
    return RoutePolicy(agents=endpoints, routes=routes)


def load_config(path: Path) -> RoutePolicy:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueRefused("config is unreadable") from exc
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


def file_accepted(queue: DurableQueue, *, to: str, text: str, caller_id: str, key: str) -> str:
    """File one accepted item. It is text plus the caller. It is work, not a chat."""
    if caller_id != queue.agent_id:
        raise QueueRefused("caller is not this endpoint")
    if not isinstance(text, str):
        raise QueueRefused("text must be a string")
    if not text.strip():
        raise QueueRefused("text is empty")
    return queue.enqueue(goal=text, references=[], idempotency_key=key, destination_id=to)


def read_accepted(queue: DurableQueue, *, to: str, key: str) -> dict:
    """Read the status and result of one filed item. It does not return the request."""
    task = queue.read_coding_request(key, destination_id=to)
    return {"task_id": task["task_id"], "status": task["status"], "result": task["result"]}


def post_handoff(queue: DurableQueue, *, to: str, pointer: str, key: str) -> str:
    """Send a pointer. The bus does not carry file bytes and does not fetch the pointer."""
    if type(pointer) is not str or not pointer.startswith("https://"):
        raise QueueRefused("pointer is invalid")
    return queue.enqueue(
        goal="read the pointer",
        references=[pointer],
        idempotency_key=key,
        destination_id=to,
    )


def boards_from_config(document: object) -> dict[str, tuple[str, ...]]:
    """A board is a member list. It does not own work and it does not add an edge."""
    if type(document) is not dict:
        raise QueueRefused("config is invalid")
    endpoints = document.get("endpoints")
    boards = document.get("boards", [])
    if type(endpoints) is not list or type(boards) is not list:
        raise QueueRefused("config is invalid")
    known = {name for name in endpoints if _identity(name)}
    if len(known) != len(endpoints):
        raise QueueRefused("config is invalid")
    found: dict[str, tuple[str, ...]] = {}
    for board in boards:
        if type(board) is not dict or set(board) != {"id", "members"}:
            raise QueueRefused("config is invalid")
        board_id, members = board["id"], board["members"]
        if (
            not _identity(board_id)
            or type(members) is not list
            or len(members) < 2
            or len(set(members)) != len(members)
            or not all(_identity(member) and member in known for member in members)
            or board_id in found
        ):
            raise QueueRefused("config is invalid")
        found[board_id] = tuple(members)
    return found


def _load_boards(config: Path) -> dict[str, tuple[str, ...]]:
    try:
        document = json.loads(Path(config).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueRefused("config is unreadable") from exc
    return boards_from_config(document)


def _require_member(queue: DurableQueue, config: Path, board: str) -> None:
    if not _identity(board):
        raise QueueRefused("board is invalid")
    members = _load_boards(config).get(board)
    if members is None or queue.agent_id not in members:
        raise QueueRefused("not a member of board %s" % board)


def _board_posts_locked(queue: DurableQueue, board: str) -> list[dict]:
    """Assign any missing sequence numbers. The caller holds the queue lock."""
    posts = queue.root / "boards" / board / "posts"
    records: list[dict] = []
    if posts.exists():
        for path in sorted(posts.glob("post_*.json")):
            records.append(queue._read(path))
    numbered = [record.get("seq") for record in records if _sequence(record.get("seq"))]
    if len(numbered) != len(set(numbered)):
        raise QueueRefused()
    highest = max(numbered, default=0)
    nxt = highest + 1
    changed = False
    for record in records:
        if _sequence(record.get("seq")):
            continue
        record["seq"] = nxt
        nxt += 1
        changed = True
        queue._write(posts / ("%s.json" % record["post_id"]), record)
    counter = queue.root / "boards" / board / "seq.json"
    if changed or (numbered and not counter.exists()):
        counter.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os_chmod(counter.parent)
        queue._write(counter, {"next": nxt})
    records.sort(key=lambda record: record["seq"])
    return records


def _sequence(value: object) -> bool:
    return type(value) is int and value >= 1


def post_board(
    queue: DurableQueue,
    *,
    config: Path,
    board: str,
    text: str,
    key: str,
    notify_url: str | None = None,
) -> dict:
    """Write the post first, then offer the text live and wake observers.

    A real post id and seq exist whatever the live path does. When
    notify_url is set, the written text is offered on board.<board>; an
    answer marks the result delivered "live" and never suppresses the
    write. Nobody listening, a timeout, or a missing server marks it
    "board". Either way one non-consuming wake is published. Answering
    does not acknowledge the answering member. The same key returns the
    saved result and does not offer again.
    """
    if not isinstance(text, str):
        raise QueueRefused("text must be a string")
    if not text.strip():
        raise QueueRefused("text is empty")
    size = len(text.encode())
    if size > BOARD_TEXT_BYTES:
        raise QueueRefused("text is %s bytes, limit is %s" % (size, BOARD_TEXT_BYTES))
    if not _safe_key(key):
        raise QueueRefused("key is invalid")
    _require_member(queue, config, board)
    members = _load_boards(config)[board]
    root = queue.root / "boards" / board
    key_path = root / "keys" / str(queue.agent_id) / key
    with queue._locked():
        if key_path.exists():
            saved = queue._read(key_path)
        else:
            (root / "posts").mkdir(parents=True, exist_ok=True, mode=0o700)
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os_chmod(root)
            existing = _board_posts_locked(queue, board)
            post_id = "post_" + secrets.token_hex(16)
            record = {
                "post_id": post_id,
                "board": board,
                "author": queue.agent_id,
                "text": text,
                "seq": (existing[-1]["seq"] + 1) if existing else 1,
                "handoff_id": None,
            }
            queue._write(root / "posts" / ("%s.json" % post_id), record)
            queue._write(root / "seq.json", {"next": record["seq"] + 1})
            key_record = {"post_id": post_id, "offering": bool(notify_url)}
            if notify_url:
                key_record["offering_since"] = time.time()
            queue._write(key_path, key_record)
            for member in members:
                queue.note_availability(member)
            saved = None
    if saved is None:
        # A fresh post. The live offer and the observer wake run outside the lock.
        # An opt-in webhook wake is queued first and sent on a background
        # thread, so it never waits on the offer and never fails the post.
        webhook.fire_board_post(config, record, members)
        delivered = "board"
        if notify_url:
            outcome = offer(
                notify_url,
                board_subject(board),
                {
                    "kind": "post",
                    "board": board,
                    "author": queue.agent_id,
                    "text": text,
                    "key": key,
                    "post_id": post_id,
                    "seq": record["seq"],
                },
            )
            if outcome == "answered":
                delivered = "live"
            result = {**record, "delivered": delivered}
            _publish_post(notify_url, record)
            with queue._locked():
                queue._write(
                    key_path,
                    {"post_id": post_id, "delivered": delivered, "result": result},
                )
            return result
        return record
    if "delivered" in saved:
        with queue._locked():
            for member in members:
                queue.note_availability(member)
        return saved["result"]
    # An existing bare key record: the post exists. While offering is set the
    # live offer may still be in flight in this process, so wait it out once,
    # bounded, without holding the queue lock while sleeping. The bound covers
    # the offer and the wake together; a mark older than that is the record a
    # crash left behind, resolved once here.
    post_id = saved["post_id"]
    marked_at = saved.get("offering_since") or 0.0
    stale = not isinstance(marked_at, (int, float)) or (time.time() - marked_at) > _OFFERING_WINDOW_SECONDS
    waited = 0.0
    while saved.get("offering") and not stale and waited < _OFFERING_WINDOW_SECONDS:
        time.sleep(0.05)
        waited += 0.05
        with queue._locked():
            saved = queue._read(key_path)
        if "delivered" in saved:
            break
        marked_at = saved.get("offering_since") or marked_at
        stale = not isinstance(marked_at, (int, float)) or (time.time() - marked_at) > _OFFERING_WINDOW_SECONDS
    if "delivered" in saved:
        with queue._locked():
            for member in members:
                queue.note_availability(member)
        return saved["result"]
    # A stale mark is what a crash left behind, resolved once here. The
    # writer may have stored its own result between the last loop read and
    # this lock, so the key is re-read first: a stored result wins, only an
    # offering-less key gets resolved.
    with queue._locked():
        saved = queue._read(key_path)
        if "delivered" in saved:
            for member in members:
                queue.note_availability(member)
            return saved["result"]
        record = queue._read(root / "posts" / ("%s.json" % post_id))
        _board_posts_locked(queue, board)
        for member in members:
            queue.note_availability(member)
        queue._write(
            key_path,
            {"post_id": post_id, "delivered": "board", "result": {**record, "delivered": "board"}},
        )
        return {**record, "delivered": "board"}


def read_board(queue: DurableQueue, *, config: Path, board: str) -> list[dict]:
    """Read one board. A non-member is refused. Reading does not claim or acknowledge."""
    return read_board_page(queue, config=config, board=board)["posts"]


def read_board_page(
    queue: DurableQueue,
    *,
    config: Path,
    board: str,
    after: str | None = None,
    limit: int | None = None,
) -> dict:
    """Read one bounded page. Omit limit to return the whole board. This does not acknowledge."""
    _require_member(queue, config, board)
    with queue._locked():
        records = _board_posts_locked(queue, board)
    page_limit = len(records) if limit is None else _page_limit(limit)
    if page_limit == 0:
        return {"posts": [], "next_after": None}
    return _page(records, after=after, limit=page_limit)


def poll_unread(
    queue: DurableQueue,
    *,
    config: Path,
    board: str,
    after: str | None = None,
    limit: int | None = None,
) -> dict:
    """Return unread posts for this member. Own posts are not unread. This does not acknowledge."""
    _require_member(queue, config, board)
    page_limit = _page_limit(limit)
    with queue._locked():
        records = _board_posts_locked(queue, board)
        acked = _acked_posts_locked(queue, board)
    unread = [
        record
        for record in records
        if record.get("author") != queue.agent_id and record.get("post_id") not in acked
    ]
    return _page(unread, after=after, limit=page_limit, positions=records)


def ack_board(queue: DurableQueue, *, config: Path, board: str, post_ids: list[str]) -> list[str]:
    """Acknowledge exact posts. Reading does not do this. Replay is the same set."""
    _require_member(queue, config, board)
    if (
        type(post_ids) is not list
        or not 1 <= len(post_ids) <= BOARD_PAGE_LIMIT
        or len(set(post_ids)) != len(post_ids)
        or not all(_post_id(item) for item in post_ids)
    ):
        raise QueueRefused("posts are invalid")
    with queue._locked():
        records = _board_posts_locked(queue, board)
        known = {record["post_id"] for record in records}
        if any(item not in known for item in post_ids):
            raise QueueRefused("a post is unknown")
        acked = _acked_posts_locked(queue, board)
        acked.update(post_ids)
        path = _ack_path(queue, board)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os_chmod(path.parent)
        queue._write(path, {"posts": sorted(acked)})
        queue.note_availability(str(queue.agent_id))
    return sorted(post_ids)


def availability_hint(queue: DurableQueue, *, config: Path, board: str | None) -> tuple[int, dict]:
    """Level-triggered hint for this member. This does not acknowledge or claim.

    ``board`` limits ``unread_boards`` to that board. ``claimable`` and
    ``holding_lease`` stay endpoint-wide: they are this member's own tasks,
    including a task that has no board. At most 20 board names are returned,
    oldest unread first. Names, flags, and counts are the whole hint.
    """
    if not isinstance(queue.agent_id, str):
        raise QueueRefused("endpoint is invalid")
    boards = _load_boards(config)
    if board is not None:
        if not _identity(board):
            raise QueueRefused("board is invalid")
        members = boards.get(board)
        if members is None or queue.agent_id not in members:
            raise QueueRefused("not a member of board %s" % board)
        selected = [board]
    else:
        selected = [name for name, members in boards.items() if queue.agent_id in members]
    from .availability import generation

    with queue._locked():
        ticket = generation(queue.agent_id)
        unread = _unread_names_locked(queue, selected)
        claimable, holding = _task_flags_locked(queue)
    return ticket, {"unread_boards": unread, "claimable": claimable, "holding_lease": holding}


def _unread_names_locked(queue: DurableQueue, boards: list[str]) -> list[str]:
    ranked: list[tuple[int, str]] = []
    for name in boards:
        records = _board_posts_locked(queue, name)
        acked = _acked_posts_locked(queue, name)
        sequences = [
            record["seq"]
            for record in records
            if record.get("author") != queue.agent_id
            and record.get("post_id") not in acked
            and type(record.get("seq")) is int
        ]
        if sequences:
            ranked.append((min(sequences), name))
    ranked.sort()
    return [name for _, name in ranked[:BOARD_PAGE_LIMIT]]


def _task_flags_locked(queue: DurableQueue) -> tuple[bool, bool]:
    claimable = False
    holding = False
    instant = _instant(None)
    worker = queue.agent_id
    for task in queue._tasks():
        if task.get("worker_id") != worker:
            continue
        status = task.get("status")
        if status == "pending":
            claimable = True
            continue
        if status != "leased":
            continue
        if _parse_stamp(task.get("lease_expires_at")) <= instant:
            claimable = True
        else:
            holding = True
    return claimable, holding


_WAIT_LIMIT_SECONDS = 25

# The live offer waits up to 2 seconds on a subscribed listener; the wake and
# the bookkeeping add headroom. An offering mark older than this is what a
# crash left behind, and a replay resolves it to delivered board.
_OFFERING_WINDOW_SECONDS = 4.0


def _unread_wait_limit(arguments: dict) -> tuple[str | None, int]:
    board = arguments.get("board")
    if board is not None and not isinstance(board, str):
        raise QueueRefused("board must be a string")
    timeout = arguments.get("timeout_seconds", 25)
    if type(timeout) is not int or not 1 <= timeout <= _WAIT_LIMIT_SECONDS:
        raise QueueRefused("timeout_seconds is invalid")
    return board, timeout


def wait_unread(
    queue: DurableQueue,
    *,
    config: Path,
    arguments: dict,
    notify_url: str | None = None,
) -> dict:
    """Hold until this endpoint has unread posts, a claimable task, or a lease.

    The result is a hint. It does not read, acknowledge, claim, or answer,
    and it does not count this endpoint's own posts. The deadline is fixed
    when the call starts. Rechecks are at most five seconds and never move
    the deadline; the parked sleep is woken early by an in-process
    note_availability bump or, with NATS, by a wake. The queue lock is not
    held while this sleeps.
    """
    if not isinstance(queue.agent_id, str):
        raise QueueRefused("endpoint is invalid")
    board, timeout = _unread_wait_limit(arguments)
    deadline = time.monotonic() + timeout
    endpoint = queue.agent_id
    from .availability import bump, release, try_reserve, wait_for_change
    from .notify import WakeWatch

    boards = _load_boards(config)
    if board is None:
        member_boards = [name for name, members in boards.items() if endpoint in members]
    else:
        if not _identity(board):
            raise QueueRefused("board is invalid")
        members = boards.get(board)
        if members is None or endpoint not in members:
            raise QueueRefused("not a member of board %s" % board)
        member_boards = [board]

    watch = None
    reserved = False
    try:
        while True:
            _ticket, snap = availability_hint(queue, config=config, board=board)
            if snap["unread_boards"] or snap["claimable"] or snap["holding_lease"]:
                return {"status": "ready", **snap}
            if time.monotonic() >= deadline:
                return {"status": "timeout", **snap}
            if not reserved:
                if not try_reserve(endpoint):
                    raise QueueRefused("a wait is already open for this endpoint")
                reserved = True
                if notify_url:
                    watch = WakeWatch(notify_url, endpoint, member_boards, on_wake=lambda: bump(endpoint))
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            wait_for_change(endpoint, _ticket, min(5.0, remaining))
    finally:
        if reserved:
            release(endpoint)
        if watch is not None:
            watch.stop()


def _page(records: list[dict], *, after: str | None, limit: int, positions: list[dict] | None = None) -> dict:
    ordered = positions if positions is not None else records
    if after is not None:
        if not _post_id(after):
            raise QueueRefused("after is invalid")
        match = [record for record in ordered if record.get("post_id") == after]
        if len(match) != 1:
            raise QueueRefused("after is unknown")
        records = [record for record in records if record["seq"] > match[0]["seq"]]
    page = records[:limit]
    return {
        "posts": page,
        "next_after": page[-1]["post_id"] if len(records) > limit else None,
    }


def _page_limit(limit: int | None) -> int:
    if limit is None:
        return BOARD_PAGE_LIMIT
    if type(limit) is not int or not 1 <= limit <= BOARD_PAGE_LIMIT:
        raise QueueRefused("limit is invalid")
    return limit


def _ack_path(queue: DurableQueue, board: str) -> Path:
    return queue.root / "boards" / board / "acks" / ("%s.json" % queue.agent_id)


def _acked_posts_locked(queue: DurableQueue, board: str) -> set[str]:
    path = _ack_path(queue, board)
    if not path.exists():
        return set()
    posts = queue._read(path).get("posts")
    if type(posts) is not list or not all(_post_id(item) for item in posts):
        raise QueueRefused()
    return set(posts)


def _promotion_sidecar(queue: DurableQueue, task_id: str, board: str, post_id: str, destination: str) -> None:
    promotions = queue.root / "promotions"
    promotions.mkdir(exist_ok=True, mode=0o700)
    os_chmod(promotions)
    queue._write(
        promotions / ("%s.json" % task_id),
        {"board": board, "post_id": post_id, "to": destination},
    )


def promote_board(
    queue: DurableQueue,
    *,
    config: Path,
    board: str,
    post_id: str,
    key: str,
    to: str | None = None,
    notify_url: str | None = None,
) -> str:
    """Turn the author's post into one handoff owned by one other member.

    The task is written with its board link before the queue lock is released.
    A worker cannot claim it until that link, the post, and the sidecar agree.
    """
    if not _safe_key(key):
        raise QueueRefused("key is invalid")
    if not _post_id(post_id):
        raise QueueRefused("post id is invalid")
    if not _identity(board):
        raise QueueRefused("board is invalid")
    members = _load_boards(config).get(board)
    if members is None or queue.agent_id not in members:
        raise QueueRefused("not a member of board %s" % board)
    destination = queue._destination(to, "task")
    if destination not in members:
        raise QueueRefused("destination is not a member of board %s" % board)
    post_path = queue.root / "boards" / board / "posts" / ("%s.json" % post_id)
    goal = "promoted %s %s" % (board, post_id)
    with queue._locked():
        post = queue._read(post_path)
        handoff_id = post.get("handoff_id")
        if post.get("author") != queue.agent_id:
            raise QueueRefused("only the author can promote this post")
        if post.get("board") != board or post.get("post_id") != post_id:
            raise QueueRefused("post is invalid")
        if handoff_id:
            if (
                type(handoff_id) is not str
                or post.get("promote_key") != key
                or post.get("promote_to") != destination
            ):
                raise QueueRefused("promotion key or destination does not match")
            task_id = queue._write_promoted_task(
                goal=goal,
                destination_id=destination,
                board=board,
                post_id=post_id,
                promote_key=key,
                existing_task_id=handoff_id,
            )
            _promotion_sidecar(queue, task_id, board, post_id, destination)
            queue.note_availability(destination)
            return task_id
        if post.get("promote_key") not in (None, key) or post.get("promote_to") not in (None, destination):
            raise QueueRefused("promotion key or destination does not match")
        post["promote_key"] = key
        post["promote_to"] = destination
        queue._write(post_path, post)
        task_id = queue._write_promoted_task(
            goal=goal,
            destination_id=destination,
            board=board,
            post_id=post_id,
            promote_key=key,
        )
        post["handoff_id"] = task_id
        queue._write(post_path, post)
        _promotion_sidecar(queue, task_id, board, post_id, destination)
        queue.note_availability(destination)
    webhook.fire_handoff(
        config, board=board, post_id=post_id, task_id=task_id, author=str(queue.agent_id), destination=destination
    )
    _publish_task(notify_url, board, post_id, task_id, destination)
    return task_id


def _safe_key(key: object) -> bool:
    return (
        type(key) is str
        and bool(key)
        and key not in (".", "..")
        and len(key) <= 160
        and all(char.isalnum() or char in "._-" for char in key)
    )


def _post_id(value: object) -> bool:
    if type(value) is not str or not value.startswith("post_"):
        return False
    suffix = value[5:]
    return len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix)


def _publish_post(url: str | None, record: dict) -> None:
    publish_wake(
        url,
        wake_subject_for_board(record.get("board")),
        {
            "kind": "post",
            "board": record["board"],
            "post_id": record["post_id"],
            "author": record["author"],
            "seq": record["seq"],
        },
    )


def _publish_task(url: str | None, board: str, post_id: str, task_id: str, destination: str) -> None:
    publish_wake(
        url,
        task_subject(destination),
        {
            "kind": "task",
            "board": board,
            "post_id": post_id,
            "task_id": task_id,
            "destination": destination,
        },
    )


def os_chmod(path: Path) -> None:
    import os

    os.chmod(path, 0o700)
