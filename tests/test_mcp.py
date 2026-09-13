import asyncio
import json
from pathlib import Path

from agent_bus_mcp.mcp import MCPApplication, TOOLS
from agent_bus_mcp.queue import DurableQueue


def _request(app: MCPApplication, headers: list[tuple[bytes, bytes]], body: bytes, *, method: str = "POST", path: str = "/mcp") -> list[dict]:
    received = False
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(event: dict) -> None:
        sent.append(event)

    asyncio.run(app({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
    return sent


def test_only_the_three_task_tools_are_discoverable(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path), "synthetic-secret")
    response = app._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert [tool["name"] for tool in response["result"]["tools"]] == [tool["name"] for tool in TOOLS]
    assert [tool["name"] for tool in TOOLS] == ["claim_task", "complete_task", "fail_task"]


def test_mcp_authentication_and_task_lifecycle(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    task_id = queue.enqueue(goal="read", references=[], idempotency_key="mcp")
    app = MCPApplication(queue, "synthetic-secret")
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claim_task", "arguments": {}}}).encode()
    denied = _request(app, [], payload)
    assert denied[0]["status"] == 401
    accepted = _request(app, [(b"authorization", b"Bearer synthetic-secret")], payload)
    result = json.loads(accepted[1]["body"])["result"]["structuredContent"]
    assert result["task_id"] == task_id
    completed = app._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "complete_task", "arguments": {"task_id": task_id, "lease_id": result["lease_id"], "result": "done"}}})
    assert completed["result"]["structuredContent"]["status"] == "completed"


def test_mcp_refuses_unknown_or_malformed_tools(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path), "synthetic-secret")
    for params in (
        {"name": "enqueue", "arguments": {}},
        {"name": "claim_task", "arguments": {"extra": True}},
        {"name": "complete_task", "arguments": {}},
    ):
        response = app._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert response["error"]["message"] == "request refused"


def test_mcp_cannot_override_configured_worker(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    try:
        MCPApplication(queue, "synthetic-secret", worker_id="other")
    except ValueError as exc:
        assert "configured" in str(exc)
    else:
        raise AssertionError("unconfigured worker was accepted")


def test_mcp_boundary_refusals_are_sanitized_and_bounded(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path), "synthetic-secret")
    authorized = [(b"authorization", b"Bearer synthetic-secret")]
    for body in (b"[]", b"null", b"5", b"\"x\"", b"{not json"):
        response = _request(app, authorized, body)
        assert response[0]["status"] == 200
        assert json.loads(response[1]["body"]) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request refused"}}
    oversized = _request(app, authorized, b"x" * (16 * 1024 + 1))
    assert oversized[0]["status"] == 400
    assert oversized[0]["headers"][0] == (b"content-type", b"text/plain; charset=utf-8")
    assert oversized[1]["body"] == b"request refused"


def test_mcp_authenticates_before_wrong_method_or_path_body_read(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path), "synthetic-secret")
    headers = [(b"authorization", b"Bearer synthetic-secret")]
    for method, path in (("GET", "/mcp"), ("POST", "/wrong")):
        response = _request(app, headers, b"x" * (16 * 1024 + 1), method=method, path=path)
        assert response[0]["status"] == 401
        assert response[0]["headers"][0] == (b"content-type", b"text/plain; charset=utf-8")
        assert response[1]["body"] == b"request refused"


def test_mcp_ping_notifications_and_batches(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path), "synthetic-secret")
    headers = [(b"authorization", b"Bearer synthetic-secret")]
    ping = _request(app, headers, b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}')
    assert json.loads(ping[1]["body"]) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    notification = _request(app, headers, b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}')
    assert notification[0]["status"] == 202
    assert notification[1]["body"] == b""
    batch = _request(app, headers, b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]')
    assert json.loads(batch[1]["body"]) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request refused"}}


def test_mcp_corrupt_task_record_is_sanitized(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    task_id = queue.enqueue(goal="read", references=[], idempotency_key="corrupt")
    task_path = tmp_path / "tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text())
    task["status"] = "leased"
    task["lease_expires_at"] = 7
    task_path.write_text(json.dumps(task))
    app = MCPApplication(queue, "synthetic-secret")
    response = _request(app, [(b"authorization", b"Bearer synthetic-secret")], b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}')
    assert json.loads(response[1]["body"]) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request refused"}}
