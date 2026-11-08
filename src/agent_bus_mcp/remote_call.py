"""TLS client for the authoritative host. The certificate paths come from the environment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .queue import QueueRefused
from .remote import call_remote


def _arguments(args: argparse.Namespace) -> dict:
    arguments: dict = {}
    if args.board is not None:
        arguments["board"] = args.board
    if args.text is not None:
        arguments["text"] = args.text
    if args.key is not None:
        arguments["key"] = args.key
    if args.post is not None:
        arguments["post"] = args.post
    if args.to is not None:
        arguments["to"] = args.to
    if args.after is not None:
        arguments["after"] = args.after
    if args.limit is not None:
        arguments["limit"] = args.limit
    if args.posts is not None:
        arguments["posts"] = [item for item in args.posts.split(",") if item]
    if args.task_id is not None:
        arguments["task_id"] = args.task_id
    if args.lease is not None:
        arguments["lease_id"] = args.lease
    if args.result is not None:
        arguments["result"] = args.result
    if args.tool == "wait_available":
        arguments["timeout_seconds"] = 25 if args.wait_seconds is None else args.wait_seconds
    return arguments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Call one tool on the TLS host as this client certificate.",
        epilog=(
            "wait_available holds this connection until this certificate has unread posts, "
            "a claimable task, or an unexpired lease, or until --wait-seconds passes. "
            "It does not acknowledge or claim, and it does not start a stopped agent. "
            "A ready result stays ready until you ack, claim, or resume, so do that before waiting again. "
            "--board limits the unread names only. --timeout is the HTTP deadline and must outlast the wait; "
            "for wait_available it defaults to wait-seconds plus 5, and it cannot exceed 30."
        ),
    )
    parser.add_argument("--tool", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--board")
    parser.add_argument("--text")
    parser.add_argument("--key")
    parser.add_argument("--post")
    parser.add_argument("--to")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--posts")
    parser.add_argument("--task-id")
    parser.add_argument("--lease")
    parser.add_argument("--result")
    parser.add_argument("--wait-seconds", type=int)
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    required = ("AGENT_BUS_REMOTE_URL", "AGENT_BUS_REMOTE_CA", "AGENT_BUS_CLIENT_CERT", "AGENT_BUS_CLIENT_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("required environment is missing: " + ", ".join(missing))
    if not os.environ["AGENT_BUS_REMOTE_URL"].startswith("https://"):
        raise SystemExit("remote url must be https")
    if args.tool == "wait_available":
        seconds = 25 if args.wait_seconds is None else args.wait_seconds
        if type(seconds) is not int or not 1 <= seconds <= 25:
            raise SystemExit("wait seconds must be an integer from 1 to 25")
        http_timeout = args.timeout if args.timeout is not None else float(min(30, seconds + 5))
    else:
        if args.wait_seconds is not None:
            raise SystemExit("wait seconds are only for wait_available")
        http_timeout = 5.0 if args.timeout is None else args.timeout
    try:
        result = call_remote(
            url=os.environ["AGENT_BUS_REMOTE_URL"],
            ca=Path(os.environ["AGENT_BUS_REMOTE_CA"]),
            certificate=Path(os.environ["AGENT_BUS_CLIENT_CERT"]),
            key=Path(os.environ["AGENT_BUS_CLIENT_KEY"]),
            tool=args.tool,
            arguments=_arguments(args),
            request_id=args.request_id,
            timeout=http_timeout,
        )
    except QueueRefused:
        print("request refused", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
