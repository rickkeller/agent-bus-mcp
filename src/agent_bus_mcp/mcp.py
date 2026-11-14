"""A deliberately tiny bearer-authenticated Streamable HTTP MCP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from .ask import ask
from .endpoints import BOARD_PAGE_LIMIT, ack_board, open_endpoint, poll_unread, post_board, promote_board, read_board_page
from .queue import DurableQueue, QueueRefused

MAX_BODY_BYTES = 16 * 1024
_LOG = logging.getLogger("agent_bus_mcp.request")
TOOLS = (
    {"name": "claim_task", "description": "Claim one pending or expired-lease task for the configured worker. No arguments. On success returns status=claimed with task_id, goal, references, lease_id, and lease_expires_at for a 900-second lease. A task promoted from a board also includes board and post_id. If none is available returns status=empty.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "complete_task", "description": "Complete the exact active lease. Requires task_id, lease_id, and result (maximum 2048 bytes); returns completed or already_completed. Refuses stale or mismatched leases.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}, "result": {"type": "string", "maxLength": 2048}}, "required": ["task_id", "lease_id", "result"], "additionalProperties": False}},
    {"name": "fail_task", "description": "Fail the exact active lease. Requires task_id and lease_id, with no result; returns failed or already_failed. Refuses stale or mismatched leases.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}}, "required": ["task_id", "lease_id"], "additionalProperties": False}},
    {"name": "read_result", "description": "Read one promoted task's status and stored result. Requires task_id. Only the bound graph endpoint, when it promoted the post that created the task, may read it. Every other endpoint is refused, and a task that was not promoted from a board is refused. A failed or unfinished task returns a null result.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 80}}, "required": ["task_id"], "additionalProperties": False}},
    {"name": "claim_question", "description": "Claim one pending advice-only question for the configured worker. No arguments. Returns status=claimed with the bounded question and lease, or status=empty.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "answer_question", "description": "Answer the exact active lease exactly once. Requires consultation_id, lease_id, and a bounded answer; stale, mismatched, expired, or already answered consultations are refused.", "inputSchema": {"type": "object", "properties": {"consultation_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}, "answer": {"type": "string", "maxLength": 2048}}, "required": ["consultation_id", "lease_id", "answer"], "additionalProperties": False}},
    {"name": "post_board", "description": "Post on one board as the bound graph endpoint. Requires board, text (maximum 2048 bytes), and key. A non-member is refused. The same key returns the original post. The post does not create a task. The simple pair has no board.", "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "maxLength": 64}, "text": {"type": "string", "maxLength": 2048}, "key": {"type": "string", "maxLength": 160}}, "required": ["board", "text", "key"], "additionalProperties": False}},
    {"name": "read_board", "description": "Read one page of a board as the bound graph endpoint. Requires board. Optional after is a post id and optional limit is 1 to 20; the default limit is 20. A non-member is refused. Reading does not create work, claim a handoff, or acknowledge unread posts.", "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "maxLength": 64}, "after": {"type": "string", "maxLength": 80}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["board"], "additionalProperties": False}},
    {"name": "poll_unread", "description": "Poll unread posts for the bound graph endpoint. Requires board. Optional after and limit match read_board. Own posts are not unread. Polling does not acknowledge. A later post cannot hide an unacknowledged earlier post: omit after to read from the oldest unread post.", "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "maxLength": 64}, "after": {"type": "string", "maxLength": 80}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["board"], "additionalProperties": False}},
    {"name": "ack_board", "description": "Acknowledge exact posts on a board for the bound graph endpoint. Requires board and posts, one to 20 post ids. Unknown ids refuse the whole call. Replay of the same ids is stable. This is the only way a post stops being unread.", "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "maxLength": 64}, "posts": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "maxLength": 80}}}, "required": ["board", "posts"], "additionalProperties": False}},
    {"name": "promote_board", "description": "Promote one post authored by the bound graph endpoint. Requires board, post, and key. Optional to must be one other board member and a handoff target. The same key and destination returns the same handoff. A non-author, a second destination, or a non-member is refused. This is the only MCP tool that creates a task.", "inputSchema": {"type": "object", "properties": {"board": {"type": "string", "maxLength": 64}, "post": {"type": "string", "maxLength": 80}, "key": {"type": "string", "maxLength": 160}, "to": {"type": "string", "maxLength": 64}}, "required": ["board", "post", "key"], "additionalProperties": False}},
    {"name": "ask", "description": "Ask one question on subject ask.<work> over NATS request/reply and wait for one answer. Requires work (one token of letters, digits, and hyphens; anything else is refused), text, and a positive timeout_s in seconds. One queue group takes each ask, so two listeners produce one answer. When nobody is listening it returns status=no_responders at once, without waiting out the deadline. A subscribed listener that stays silent returns status=timeout when the deadline passes. An answer returns status=answered. This tool does not create a board post, a task, or a consultation.", "inputSchema": {"type": "object", "properties": {"work": {"type": "string"}, "text": {"type": "string", "maxLength": 2048}, "timeout_s": {"type": "number", "exclusiveMinimum": 0}}, "required": ["work", "text", "timeout_s"], "additionalProperties": False}},
)


def _note(method: str, status: int, *, tools: int | None = None, tool: str | None = None) -> None:
    """Record one request. Never include a token, a header, or a body."""
    known = {item["name"] for item in TOOLS}
    parts = [f"method={method}", f"status={status}"]
    if tools is not None:
        parts.append(f"tools={tools}")
    if tool in known:
        parts.append(f"tool={tool}")
    elif tool is not None:
        parts.append("tool=unknown")
    _LOG.info(" ".join(parts))


def _error(request_id: Any, code: int = -32600) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": "request refused"}}


class MCPApplication:
    """ASGI app: authenticate before buffering/parsing a request body."""

    def __init__(self, queue: DurableQueue, bearer_secret: str, board_config: Path | None = None, nats_url: str | None = None) -> None:
        if not bearer_secret:
            raise ValueError("authentication secret required")
        self.queue, self.bearer_secret = queue, bearer_secret
        self.board_config = None if board_config is None else Path(board_config)
        self.nats_url = None if nats_url is None else str(nats_url)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        try:
            await self._handle(scope, receive, send)
        except Exception:
            # Keep all ordinary failures, including corrupt on-disk state, behind
            # the same non-reflective JSON-RPC boundary. BaseException subclasses
            # (cancellation, exit, and interrupts) intentionally propagate.
            try:
                await self._send(send, 200, json.dumps(_error(None), separators=(",", ":")).encode())
            except Exception:
                pass

    async def _handle(self, scope: dict, receive: Any, send: Any) -> None:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"").decode("ascii", "ignore")
        expected = f"Bearer {self.bearer_secret}"
        authenticated = hmac.compare_digest(supplied, expected)
        if scope.get("type") == "http" and scope.get("path") == "/mcp" and scope.get("method") != "POST" and authenticated:
            _note("-", 405)
            await self._send(send, 405, b"method not allowed", content_type=b"text/plain; charset=utf-8", extra=[(b"allow", b"POST")])
            return
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/mcp" or not authenticated:
            _note("-", 401)
            await self._send(send, 401, b"request refused", content_type=b"text/plain; charset=utf-8")
            return
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if len(body) > MAX_BODY_BYTES:
                _note("-", 400)
                await self._send(send, 400, b"request refused", content_type=b"text/plain; charset=utf-8")
                return
            if not event.get("more_body"):
                break
        request: object = None
        try:
            request = json.loads(body)
            # This stateless endpoint explicitly rejects JSON-RPC batches. It
            # supports one request per HTTP POST, avoiding partial batch effects.
            response = _error(None) if isinstance(request, list) else self._dispatch(request)
        except Exception:
            response = _error(None)
        method = "-"
        tool_name = None
        tool_count = None
        if isinstance(request, dict) and isinstance(request.get("method"), str):
            method = request["method"] if request["method"] in {"ping", "initialize", "tools/list", "tools/call"} else "unknown"
            params = request.get("params")
            if method == "tools/call" and isinstance(params, dict):
                name = params.get("name")
                tool_name = name if isinstance(name, str) else ""
        if isinstance(response, dict):
            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                tool_count = len(result["tools"])
        if response is None:
            _note(method, 202, tools=tool_count, tool=tool_name)
            await self._send(send, 202, b"", content_type=None)
            return
        _note(method, 200, tools=tool_count, tool=tool_name)
        await self._send(send, 200, json.dumps(response, separators=(",", ":")).encode())

    def _dispatch(self, request: object) -> dict | None:
        if not isinstance(request, dict):
            return _error(None)
        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return _error(None)
        # A JSON-RPC notification has no id and must receive no JSON-RPC reply.
        if "id" not in request:
            return None
        method, params = request["method"], request.get("params", {})
        if not isinstance(params, dict):
            return _error(request_id)
        if method == "ping" and not params:
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "agent-bus-mcp", "version": "0.3.0"}}}
        if method == "tools/list" and not params:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(TOOLS)}}
        if method != "tools/call" or set(params) != {"name", "arguments"} or not isinstance(params["arguments"], dict):
            return _error(request_id)
        name, arguments = params["name"], params["arguments"]
        if name == "claim_task" and not arguments:
            claimed = self.queue.claim()
            result = {"status": "empty"} if claimed is None else {"status": "claimed", **claimed}
        elif name == "complete_task" and set(arguments) == {"task_id", "lease_id", "result"}:
            result = {"status": self.queue.finish(task_id=arguments["task_id"], lease_id=arguments["lease_id"], result=arguments["result"], failed=False), "task_id": arguments["task_id"]}
        elif name == "fail_task" and set(arguments) == {"task_id", "lease_id"}:
            result = {"status": self.queue.finish(task_id=arguments["task_id"], lease_id=arguments["lease_id"], result=None, failed=True), "task_id": arguments["task_id"]}
        elif name == "read_result" and set(arguments) == {"task_id"}:
            result = self.queue.read_result(arguments["task_id"])
        elif name == "claim_question" and not arguments:
            claimed = self.queue.claim_question()
            result = {"status": "empty"} if claimed is None else {"status": "claimed", **claimed}
        elif name == "answer_question" and set(arguments) == {"consultation_id", "lease_id", "answer"}:
            result = {
                "status": self.queue.answer_question(
                    consultation_id=arguments["consultation_id"],
                    lease_id=arguments["lease_id"],
                    answer=arguments["answer"],
                ),
                "consultation_id": arguments["consultation_id"],
            }
        elif name == "post_board" and set(arguments) == {"board", "text", "key"}:
            result = self._posted(arguments["board"], arguments["text"], arguments["key"])
        elif name == "read_board" and self._page_arguments(arguments):
            result = self._read_board(arguments["board"], arguments.get("after"), arguments.get("limit"))
        elif name == "poll_unread" and self._page_arguments(arguments):
            result = self._poll_unread(arguments["board"], arguments.get("after"), arguments.get("limit"))
        elif name == "ack_board" and set(arguments) == {"board", "posts"}:
            result = self._ack(arguments["board"], arguments["posts"])
        elif name == "promote_board" and set(arguments) in ({"board", "post", "key"}, {"board", "post", "key", "to"}):
            result = {
                "status": "promoted",
                "task_id": self._promote(arguments["board"], arguments["post"], arguments["key"], arguments.get("to")),
            }
        elif name == "ask" and set(arguments) == {"work", "text", "timeout_s"}:
            result = self._asked(arguments["work"], arguments["text"], arguments["timeout_s"])
        else:
            return _error(request_id)
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}], "structuredContent": result}}

    def _require_board(self) -> Path:
        if self.board_config is None or self.queue.agent_id is None:
            raise QueueRefused()
        return self.board_config

    def _asked(self, work: object, text: object, timeout_s: object) -> dict:
        """One NATS ask. It does not create a board post, a task, or a consultation."""
        if not isinstance(self.nats_url, str):
            raise QueueRefused()
        return ask(self.nats_url, work=work, text=text, timeout_s=timeout_s)

    def _posted(self, board: object, text: object, key: object) -> dict:
        if not all(isinstance(item, str) for item in (board, text, key)):
            raise QueueRefused()
        posted = post_board(self.queue, config=self._require_board(), board=board, text=text, key=key)
        return {
            "status": "posted",
            "post_id": posted["post_id"],
            "board": posted["board"],
            "author": posted["author"],
            "text": posted["text"],
            "seq": posted["seq"],
            "handoff_id": posted["handoff_id"],
        }

    @staticmethod
    def _page_arguments(arguments: dict) -> bool:
        return "board" in arguments and set(arguments) <= {"board", "after", "limit"}

    def _after_and_limit(self, after: object, limit: object) -> tuple[str | None, int]:
        if after is not None and not isinstance(after, str):
            raise QueueRefused()
        if limit is None:
            return after, BOARD_PAGE_LIMIT
        if type(limit) is not int:
            raise QueueRefused()
        return after, limit

    def _read_board(self, board: object, after: object, limit: object) -> dict:
        if not isinstance(board, str):
            raise QueueRefused()
        chosen_after, chosen_limit = self._after_and_limit(after, limit)
        page = read_board_page(
            self.queue,
            config=self._require_board(),
            board=board,
            after=chosen_after,
            limit=chosen_limit,
        )
        return {"status": "ok", "posts": page["posts"], "next_after": page["next_after"]}

    def _poll_unread(self, board: object, after: object, limit: object) -> dict:
        if not isinstance(board, str):
            raise QueueRefused()
        chosen_after, chosen_limit = self._after_and_limit(after, limit)
        page = poll_unread(
            self.queue,
            config=self._require_board(),
            board=board,
            after=chosen_after,
            limit=chosen_limit,
        )
        return {"status": "ok", "posts": page["posts"], "next_after": page["next_after"]}

    def _ack(self, board: object, posts: object) -> dict:
        if not isinstance(board, str) or type(posts) is not list:
            raise QueueRefused()
        return {"status": "acked", "posts": ack_board(self.queue, config=self._require_board(), board=board, post_ids=posts)}

    def _promote(self, board: object, post_id: object, key: object, destination: object) -> str:
        if not all(isinstance(item, str) for item in (board, post_id, key)):
            raise QueueRefused()
        if destination is not None and not isinstance(destination, str):
            raise QueueRefused()
        return promote_board(
            self.queue,
            config=self._require_board(),
            board=board,
            post_id=post_id,
            key=key,
            to=destination,
        )

    @staticmethod
    async def _send(send: Any, status: int, body: bytes, *, content_type: bytes | None = b"application/json", extra: list[tuple[bytes, bytes]] | None = None) -> None:
        headers = [(b"content-length", str(len(body)).encode())]
        if content_type is not None:
            headers.insert(0, (b"content-type", content_type))
        if extra:
            headers.extend(extra)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


_ALWAYS_ENV = (
    "AGENT_BUS_STATE_DIR",
    "AGENT_BUS_BEARER_SECRET",
    "AGENT_BUS_PORT",
)
_LEGACY_ENV = ("AGENT_BUS_PRODUCER_ID", "AGENT_BUS_WORKER_ID")
_GRAPH_ENV = ("AGENT_BUS_CONFIG", "AGENT_BUS_ENDPOINT_ID")
KNOWN_ENV = _ALWAYS_ENV + ("AGENT_BUS_BIND_HOST", "AGENT_BUS_NATS_URL") + _LEGACY_ENV + _GRAPH_ENV


def _filled(name: str) -> bool:
    return bool(os.environ.get(name, ""))


def identity_mode() -> str:
    """Choose the simple pair or one endpoint on a configured graph.

    Setting variables from both modes is refused. Neither mode can be inferred
    from a partial mix.
    """
    graph = any(_filled(name) for name in _GRAPH_ENV)
    legacy = any(_filled(name) for name in _LEGACY_ENV)
    if graph and legacy:
        return "ambiguous"
    if graph:
        return "graph"
    return "legacy"


def missing_env() -> list[str]:
    missing = [name for name in _ALWAYS_ENV if not _filled(name)]
    mode = identity_mode()
    if mode == "graph":
        missing.extend(name for name in _GRAPH_ENV if not _filled(name))
    elif mode == "legacy":
        missing.extend(name for name in _LEGACY_ENV if not _filled(name))
    return missing


def open_configured_queue() -> DurableQueue:
    """Open the queue selected by the process environment."""
    if identity_mode() == "ambiguous":
        raise SystemExit("required environment is invalid")
    state = Path(os.environ["AGENT_BUS_STATE_DIR"])
    if identity_mode() == "graph":
        return open_endpoint(
            state,
            Path(os.environ["AGENT_BUS_CONFIG"]),
            os.environ["AGENT_BUS_ENDPOINT_ID"],
        )
    return DurableQueue(
        state,
        producer_id=os.environ["AGENT_BUS_PRODUCER_ID"],
        worker_id=os.environ["AGENT_BUS_WORKER_ID"],
    )


def _env_note(name: str) -> str:
    if name == "AGENT_BUS_BIND_HOST":
        return " (optional, default loopback)"
    if name == "AGENT_BUS_NATS_URL":
        return " (optional, NATS server for the ask tool)"
    if name in _GRAPH_ENV:
        return " (graph mode; omit the simple pair)"
    if name in _LEGACY_ENV:
        return " (simple pair; omit in graph mode)"
    return ""


def main(argv: list[str] | None = None) -> int:
    """Run with an ASGI server installed by the deployer, bound to loopback."""
    listed = "\n".join(f"  {name}{_env_note(name)}" for name in KNOWN_ENV)
    parser = argparse.ArgumentParser(
        description="Loopback MCP bridge. --help does not start the server.",
        epilog="environment:\n" + listed,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args([] if argv is None else argv)
    if identity_mode() == "ambiguous":
        raise SystemExit("required environment is invalid")
    missing = missing_env()
    if missing:
        raise SystemExit(
            "required environment is missing: " + ", ".join(missing) + "\nenvironment:\n" + listed
        )
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("install an ASGI server to run the bridge") from exc
    bearer_secret = os.environ["AGENT_BUS_BEARER_SECRET"]
    host = os.environ.get("AGENT_BUS_BIND_HOST", "loopback")
    port_text = os.environ["AGENT_BUS_PORT"]
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit("required environment is invalid") from exc
    if host != "loopback":
        raise SystemExit("only loopback binding is supported")
    loopback = str(126 + 1) + ".0.0.1"
    queue = open_configured_queue()
    board_config = Path(os.environ["AGENT_BUS_CONFIG"]) if identity_mode() == "graph" else None
    nats_url = os.environ.get("AGENT_BUS_NATS_URL") or None
    uvicorn.run(MCPApplication(queue, bearer_secret, board_config, nats_url), host=loopback, port=port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
