"""Local sender for one configured endpoint. It does not call a model."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .endpoints import (
    file_accepted,
    open_endpoint,
    post_board,
    post_chat,
    post_handoff,
    promote_board,
    read_accepted,
    read_board,
)
from .queue import QueueRefused


def _need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise QueueRefused()
    return value


def _queue():
    return open_endpoint(
        Path(_need("AGENT_BUS_STATE_DIR")),
        Path(_need("AGENT_BUS_CONFIG")),
        _need("AGENT_BUS_ENDPOINT_ID"),
    )


def _flag(argv: list[str], name: str) -> str:
    if name not in argv:
        raise QueueRefused()
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise QueueRefused()
    return argv[index + 1]


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if not arguments:
            raise QueueRefused()
        command, rest = arguments[0], arguments[1:]
        queue = _queue()
        if command == "post-chat":
            result = post_chat(
                queue,
                to=_flag(rest, "--to"),
                text=_flag(rest, "--text"),
                key=_flag(rest, "--key"),
            )
        elif command == "read-chat":
            if len(rest) != 1:
                raise QueueRefused()
            result = queue.read_answer(rest[0])
        elif command == "claim-chat":
            if rest:
                raise QueueRefused()
            result = queue.claim_question()
        elif command == "answer-chat":
            result = queue.answer_question(
                consultation_id=_flag(rest, "--id"),
                lease_id=_flag(rest, "--lease"),
                answer=_flag(rest, "--text"),
            )
        elif command == "file-accepted":
            result = file_accepted(
                queue,
                to=_flag(rest, "--to"),
                text=_flag(rest, "--text"),
                caller_id=_flag(rest, "--caller"),
                key=_flag(rest, "--key"),
            )
        elif command == "read-accepted":
            result = read_accepted(queue, to=_flag(rest, "--to"), key=_flag(rest, "--key"))
        elif command == "post-handoff":
            result = post_handoff(
                queue,
                to=_flag(rest, "--to"),
                pointer=_flag(rest, "--pointer"),
                key=_flag(rest, "--key"),
            )
        elif command == "claim-handoff":
            if rest:
                raise QueueRefused()
            result = queue.claim()
        elif command == "post-board":
            result = post_board(
                queue,
                config=Path(_need("AGENT_BUS_CONFIG")),
                board=_flag(rest, "--board"),
                text=_flag(rest, "--text"),
                key=_flag(rest, "--key"),
            )
        elif command == "read-board":
            if rest != ["--board", _flag(rest, "--board")]:
                raise QueueRefused()
            result = read_board(queue, config=Path(_need("AGENT_BUS_CONFIG")), board=_flag(rest, "--board"))
        elif command == "promote-board":
            result = promote_board(
                queue,
                config=Path(_need("AGENT_BUS_CONFIG")),
                board=_flag(rest, "--board"),
                post_id=_flag(rest, "--post"),
                key=_flag(rest, "--key"),
                to=_flag(rest, "--to") if "--to" in rest else None,
            )
        elif command == "complete-handoff":
            result = queue.finish(
                task_id=_flag(rest, "--id"),
                lease_id=_flag(rest, "--lease"),
                result=_flag(rest, "--text"),
                failed=False,
            )
        else:
            raise QueueRefused()
    except QueueRefused:
        print("request refused", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
