"""A deliberately tiny bearer-authenticated Streamable HTTP MCP endpoint."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
from typing import Any

from .queue import DurableQueue

MAX_BODY_BYTES = 16 * 1024
TOOLS = (
    {"name": "claim_task", "description": "Claim one pending or expired-lease task for the configured worker. No arguments. On success returns status=claimed with task_id, goal, references, lease_id, and lease_expires_at for a 900-second lease. If none is available returns status=empty.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "complete_task", "description": "Complete the exact active lease. Requires task_id, lease_id, and result (maximum 2048 bytes); returns completed or already_completed. Refuses stale or mismatched leases.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}, "result": {"type": "string", "maxLength": 2048}}, "required": ["task_id", "lease_id", "result"], "additionalProperties": False}},
    {"name": "fail_task", "description": "Fail the exact active lease. Requires task_id and lease_id, with no result; returns failed or already_failed. Refuses stale or mismatched leases.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}}, "required": ["task_id", "lease_id"], "additionalProperties": False}},
    {"name": "claim_question", "description": "Claim one pending advice-only question for the configured worker. No arguments. Returns status=claimed with the bounded question and lease, or status=empty.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "answer_question", "description": "Answer the exact active lease exactly once. Requires consultation_id, lease_id, and a bounded answer; stale, mismatched, expired, or already answered consultations are refused.", "inputSchema": {"type": "object", "properties": {"consultation_id": {"type": "string", "maxLength": 80}, "lease_id": {"type": "string", "maxLength": 80}, "answer": {"type": "string", "maxLength": 2048}}, "required": ["consultation_id", "lease_id", "answer"], "additionalProperties": False}},
)


def _error(request_id: Any, code: int = -32600) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": "request refused"}}


class MCPApplication:
    """ASGI app: authenticate before buffering/parsing a request body."""

    def __init__(self, queue: DurableQueue, bearer_secret: str) -> None:
        if not bearer_secret:
            raise ValueError("authentication secret required")
        self.queue, self.bearer_secret = queue, bearer_secret

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
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/mcp" or not hmac.compare_digest(supplied, expected):
            await self._send(send, 401, b"request refused", content_type=b"text/plain; charset=utf-8")
            return
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if len(body) > MAX_BODY_BYTES:
                await self._send(send, 400, b"request refused", content_type=b"text/plain; charset=utf-8")
                return
            if not event.get("more_body"):
                break
        try:
            request = json.loads(body)
            # This stateless endpoint explicitly rejects JSON-RPC batches. It
            # supports one request per HTTP POST, avoiding partial batch effects.
            response = _error(None) if isinstance(request, list) else self._dispatch(request)
        except Exception:
            response = _error(None)
        if response is None:
            await self._send(send, 202, b"", content_type=None)
            return
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
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "agent-bus-mcp", "version": "0.1.0"}}}
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
        else:
            return _error(request_id)
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}], "structuredContent": result}}

    @staticmethod
    async def _send(send: Any, status: int, body: bytes, *, content_type: bytes | None = b"application/json") -> None:
        headers = [(b"content-length", str(len(body)).encode())]
        if content_type is not None:
            headers.insert(0, (b"content-type", content_type))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def main() -> int:
    """Run with an ASGI server installed by the deployer, bound to loopback."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("install an ASGI server to run the bridge") from exc
    state = os.environ.get("AGENT_BUS_STATE_DIR")
    bearer_secret = os.environ.get("AGENT_BUS_BEARER_SECRET")
    producer_id = os.environ.get("AGENT_BUS_PRODUCER_ID")
    worker_id = os.environ.get("AGENT_BUS_WORKER_ID")
    if not state or not bearer_secret or not producer_id or not worker_id:
        raise SystemExit("required environment is missing")
    host = os.environ.get("AGENT_BUS_BIND_HOST", "loopback")
    port_text = os.environ.get("AGENT_BUS_PORT")
    if not port_text:
        raise SystemExit("required environment is missing")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit("required environment is invalid") from exc
    if host != "loopback":
        raise SystemExit("only loopback binding is supported")
    loopback = str(126 + 1) + ".0.0.1"
    queue = DurableQueue(Path(state), producer_id=producer_id, worker_id=worker_id)
    uvicorn.run(MCPApplication(queue, bearer_secret), host=loopback, port=port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
