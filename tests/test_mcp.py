import asyncio
import inspect
import json
import threading
from pathlib import Path

import pytest

from agent_bus_mcp.endpoints import open_endpoint
from agent_bus_mcp.mcp import MCPApplication, TOOLS, main, open_configured_queue
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


def test_request_log_counts_tools_and_omits_the_secret(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
    headers = [
        (b"authorization", b"Bearer synthetic-secret"),
        (b"content-type", b"application/json"),
    ]
    with caplog.at_level(logging.INFO, logger="agent_bus_mcp.request"):
        listed = _request(
            app,
            headers,
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
        )
        called = _request(
            app,
            headers,
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}',
        )
    assert listed[0]["status"] == 200
    assert called[0]["status"] == 200
    assert "method=tools/list status=200 tools=12" in caplog.text
    assert "method=tools/call status=200 tool=claim_task" in caplog.text
    assert "synthetic-secret" not in caplog.text
    assert "Bearer" not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent_bus_mcp.request"):
        refused = _request(app, [(b"authorization", b"Bearer wrong-token")], b"{}")
    assert refused[0]["status"] == 401
    assert "method=- status=401" in caplog.text
    assert "wrong-token" not in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_only_the_closed_task_and_consultation_tools_are_discoverable(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
    response = app._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert [tool["name"] for tool in response["result"]["tools"]] == [tool["name"] for tool in TOOLS]
    assert [tool["name"] for tool in TOOLS] == [
        "claim_task",
        "complete_task",
        "fail_task",
        "read_result",
        "claim_question",
        "answer_question",
        "post_board",
        "read_board",
        "poll_unread",
        "ack_board",
        "promote_board",
        "ask",
    ]


def test_tool_descriptions_document_lease_and_terminal_contract() -> None:
    descriptions = {tool["name"]: tool["description"] for tool in TOOLS}
    assert "900-second lease" in descriptions["claim_task"]
    assert "status=empty" in descriptions["claim_task"]
    assert "task_id, lease_id, and result" in descriptions["complete_task"]
    assert "2048 bytes" in descriptions["complete_task"]
    assert "completed or already_completed" in descriptions["complete_task"]
    assert "stale or mismatched leases" in descriptions["complete_task"]
    assert "task_id and lease_id" in descriptions["fail_task"]
    assert "no result" in descriptions["fail_task"]
    assert "failed or already_failed" in descriptions["fail_task"]
    assert "stale or mismatched leases" in descriptions["fail_task"]
    assert "promoted the post that created the task" in descriptions["read_result"]
    assert "not promoted from a board" in descriptions["read_result"]
    assert "advice-only" in descriptions["claim_question"]
    assert "status=empty" in descriptions["claim_question"]
    assert "exact active lease" in descriptions["answer_question"]
    assert "exactly once" in descriptions["answer_question"]
    assert "does not create a task" in descriptions["post_board"]
    assert "2048 bytes" in descriptions["post_board"]
    assert "does not create work" in descriptions["read_board"]
    assert "authored by the bound graph endpoint" in descriptions["promote_board"]
    assert "only MCP tool that creates a task" in descriptions["promote_board"]


def test_consultation_schemas_are_closed_and_advice_only() -> None:
    schemas = {tool["name"]: tool["inputSchema"] for tool in TOOLS}
    assert schemas["claim_question"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert set(schemas["answer_question"]["properties"]) == {
        "consultation_id",
        "lease_id",
        "answer",
    }
    assert schemas["answer_question"]["required"] == [
        "consultation_id",
        "lease_id",
        "answer",
    ]
    assert schemas["answer_question"]["additionalProperties"] is False
    assert schemas["post_board"]["additionalProperties"] is False
    assert schemas["post_board"]["required"] == ["board", "text", "key"]
    assert schemas["read_board"]["required"] == ["board"]
    assert schemas["promote_board"]["required"] == ["board", "post", "key"]
    assert "to" in schemas["promote_board"]["properties"]
    assert "endpoint_id" not in schemas["promote_board"]["properties"]


def test_mcp_authentication_and_task_lifecycle(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
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
    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
    for params in (
        {"name": "enqueue", "arguments": {}},
        {"name": "claim_task", "arguments": {"extra": True}},
        {"name": "complete_task", "arguments": {}},
        {"name": "claim_question", "arguments": {"destination": "elsewhere"}},
        {"name": "answer_question", "arguments": {"consultation_id": "x", "lease_id": "y", "answer": "z", "task_complete": True}},
    ):
        response = app._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert response["error"]["message"] == "request refused"


def test_mcp_uses_only_the_queue_bound_principal(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    assert "worker_id" not in inspect.signature(MCPApplication).parameters
    spoofable_identity_fields = {
        "agent_id",
        "source_id",
        "producer_id",
        "worker_id",
        "destination_id",
        "endpoint_id",
        "author",
    }
    for tool in TOOLS:
        assert spoofable_identity_fields.isdisjoint(
            tool["inputSchema"].get("properties", {})
        )
    assert MCPApplication(queue, "synthetic-secret").queue is queue


def test_mcp_boundary_refusals_are_sanitized_and_bounded(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
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
    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
    headers = [(b"authorization", b"Bearer synthetic-secret")]
    stream = _request(app, headers, b"x" * (16 * 1024 + 1), method="GET", path="/mcp")
    assert stream[0]["status"] == 405
    assert (b"allow", b"POST") in stream[0]["headers"]
    assert stream[1]["body"] == b"method not allowed"
    wrong_path = _request(app, headers, b"x" * (16 * 1024 + 1), method="POST", path="/wrong")
    assert wrong_path[0]["status"] == 401
    assert wrong_path[1]["body"] == b"request refused"
    missing = _request(app, [], b"x" * (16 * 1024 + 1), method="GET", path="/mcp")
    assert missing[0]["status"] == 401
    assert missing[1]["body"] == b"request refused"


def test_mcp_ping_notifications_and_batches(tmp_path: Path) -> None:
    app = MCPApplication(DurableQueue(tmp_path, producer_id="producer", worker_id="worker"), "synthetic-secret")
    headers = [(b"authorization", b"Bearer synthetic-secret")]
    ping = _request(app, headers, b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}')
    assert json.loads(ping[1]["body"]) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    notification = _request(app, headers, b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}')
    assert notification[0]["status"] == 202
    assert notification[1]["body"] == b""
    batch = _request(app, headers, b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]')
    assert json.loads(batch[1]["body"]) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request refused"}}


def test_mcp_corrupt_task_record_is_sanitized(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    task_id = queue.enqueue(goal="read", references=[], idempotency_key="corrupt")
    task_path = tmp_path / "tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text())
    task["status"] = "leased"
    task["lease_expires_at"] = 7
    task_path.write_text(json.dumps(task))
    app = MCPApplication(queue, "synthetic-secret")
    response = _request(app, [(b"authorization", b"Bearer synthetic-secret")], b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}')
    assert json.loads(response[1]["body"]) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request refused"}}


def test_help_lists_env_and_startup_names_the_missing_ones(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for name in (
        "AGENT_BUS_STATE_DIR",
        "AGENT_BUS_BEARER_SECRET",
        "AGENT_BUS_PORT",
        "AGENT_BUS_PRODUCER_ID",
        "AGENT_BUS_WORKER_ID",
        "AGENT_BUS_CONFIG",
        "AGENT_BUS_ENDPOINT_ID",
        "AGENT_BUS_BIND_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "AGENT_BUS_STATE_DIR" in help_text
    assert "AGENT_BUS_BIND_HOST" in help_text
    assert "AGENT_BUS_CONFIG" in help_text
    assert "AGENT_BUS_ENDPOINT_ID" in help_text
    with pytest.raises(SystemExit) as missing:
        main([])
    message = str(missing.value)
    assert "AGENT_BUS_PORT" in message
    assert "AGENT_BUS_WORKER_ID" in message
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", "/tmp/unused")
    monkeypatch.setenv("AGENT_BUS_BEARER_SECRET", "synthetic")
    monkeypatch.setenv("AGENT_BUS_PRODUCER_ID", "producer")
    monkeypatch.setenv("AGENT_BUS_WORKER_ID", "worker")
    with pytest.raises(SystemExit) as still_missing:
        main([])
    assert "missing: AGENT_BUS_PORT" in str(still_missing.value)


def test_graph_mode_is_exclusive_and_binds_one_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENT_BUS_STATE_DIR",
        "AGENT_BUS_BEARER_SECRET",
        "AGENT_BUS_PORT",
        "AGENT_BUS_PRODUCER_ID",
        "AGENT_BUS_WORKER_ID",
        "AGENT_BUS_CONFIG",
        "AGENT_BUS_ENDPOINT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_BUS_BEARER_SECRET", "synthetic")
    monkeypatch.setenv("AGENT_BUS_PORT", "9")
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(tmp_path / "endpoints.json"))
    monkeypatch.setenv("AGENT_BUS_PRODUCER_ID", "north")
    with pytest.raises(SystemExit) as mixed:
        main([])
    assert str(mixed.value) == "required environment is invalid"
    monkeypatch.delenv("AGENT_BUS_PRODUCER_ID", raising=False)
    with pytest.raises(SystemExit) as missing_endpoint:
        main([])
    assert "AGENT_BUS_ENDPOINT_ID" in str(missing_endpoint.value)
    (tmp_path / "endpoints.json").write_text(
        json.dumps(
            {
                "endpoints": ["north", "south"],
                "allow": [{"from": "north", "to": "south", "kinds": ["chat", "handoff"]}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "south")
    queue = open_configured_queue()
    assert queue.agent_id == "south"


def _graph(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "endpoints.json"
    config.write_text(
        json.dumps(
            {
                "endpoints": ["north", "south", "witness"],
                "allow": [
                    {"from": "north", "to": "south", "kinds": ["chat", "handoff"]},
                    {"from": "south", "to": "north", "kinds": ["chat"]},
                ],
                "boards": [{"id": "room", "members": ["north", "south"]}],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path / "state", config


def _member(state: Path, config: Path, endpoint: str) -> MCPApplication:
    return MCPApplication(open_endpoint(state, config, endpoint), "synthetic-secret", config)


def _call(app: MCPApplication, name: str, arguments: dict) -> dict:
    response = _request(
        app,
        [(b"authorization", b"Bearer synthetic-secret")],
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        ).encode(),
    )
    return json.loads(response[1]["body"])


def test_graph_member_can_post_read_and_promote_without_impersonation(tmp_path: Path) -> None:
    state, config = _graph(tmp_path)
    north = _member(state, config, "north")
    south = _member(state, config, "south")
    witness = _member(state, config, "witness")
    legacy = MCPApplication(DurableQueue(tmp_path / "legacy", producer_id="north", worker_id="south"), "synthetic-secret")
    posted = _call(north, "post_board", {"board": "room", "text": "south owns the fix", "key": "talk"})
    body = posted["result"]["structuredContent"]
    assert body["status"] == "posted"
    assert body["author"] == "north"
    assert body["handoff_id"] is None
    assert list((state / "tasks").glob("task_*.json")) == []
    replay = _call(north, "post_board", {"board": "room", "text": "a different sentence", "key": "talk"})
    assert replay["result"]["structuredContent"]["post_id"] == body["post_id"]
    assert replay["result"]["structuredContent"]["text"] == "south owns the fix"
    assert len(list((state / "boards" / "room" / "posts").glob("post_*.json"))) == 1
    visible = _call(south, "read_board", {"board": "room"})["result"]["structuredContent"]
    assert visible["posts"][0]["text"] == "south owns the fix"
    assert visible["posts"][0]["handoff_id"] is None
    for app, arguments in (
        (witness, {"board": "room", "text": "no", "key": "x"}),
        (legacy, {"board": "room", "text": "no board here", "key": "z"}),
        (north, {"board": "room", "text": "   ", "key": "blank"}),
        (north, {"board": "room", "text": "x" * 2049, "key": "long"}),
        (north, {"board": "room", "text": "restart jig", "key": "blocked"}),
        (north, {"board": "../room", "text": "path", "key": "path"}),
    ):
        refused = _call(app, "post_board", arguments)
        assert refused["error"]["message"] == "request refused"
    assert _call(witness, "read_board", {"board": "room"})["error"]["message"] == "request refused"
    assert _call(legacy, "read_board", {"board": "room"})["error"]["message"] == "request refused"
    spoofed = _call(
        north,
        "post_board",
        {"board": "room", "text": "pretend", "key": "spoof", "endpoint_id": "south", "author": "south"},
    )
    assert spoofed["error"]["message"] == "request refused"
    assert _call(south, "promote_board", {"board": "room", "post": body["post_id"], "key": "own"})["error"]["message"] == "request refused"
    assert list((state / "tasks").glob("task_*.json")) == []
    promoted = _call(north, "promote_board", {"board": "room", "post": body["post_id"], "key": "own"})
    task_id = promoted["result"]["structuredContent"]["task_id"]
    assert promoted["result"]["structuredContent"]["status"] == "promoted"
    again = _call(north, "promote_board", {"board": "room", "post": body["post_id"], "key": "own"})
    assert again["result"]["structuredContent"]["task_id"] == task_id
    moved = _call(north, "promote_board", {"board": "room", "post": body["post_id"], "key": "own", "to": "witness"})
    assert moved["error"]["message"] == "request refused"
    other = _call(north, "promote_board", {"board": "room", "post": body["post_id"], "key": "other"})
    assert other["error"]["message"] == "request refused"
    assert len(list((state / "tasks").glob("task_*.json"))) == 1
    claimed = _call(south, "claim_task", {})["result"]["structuredContent"]
    assert claimed["status"] == "claimed"
    assert claimed["task_id"] == task_id
    assert claimed["board"] == "room"
    assert claimed["post_id"] == body["post_id"]
    assert _call(north, "claim_task", {})["result"]["structuredContent"]["status"] == "empty"
    linked = _call(south, "read_board", {"board": "room"})["result"]["structuredContent"]["posts"][0]
    assert linked["handoff_id"] == task_id
    assert (tmp_path / "legacy" / "boards").exists() is False


def test_read_result_allows_only_the_promoting_endpoint(tmp_path: Path) -> None:
    state, config = _graph(tmp_path)
    north = _member(state, config, "north")
    south = _member(state, config, "south")
    witness = _member(state, config, "witness")
    legacy = MCPApplication(DurableQueue(tmp_path / "legacy", producer_id="producer", worker_id="worker"), "synthetic-secret")
    posted = _call(north, "post_board", {"board": "room", "text": "south owns the fix", "key": "talk"})
    post_id = posted["result"]["structuredContent"]["post_id"]
    task_id = _call(north, "promote_board", {"board": "room", "post": post_id, "key": "own"})["result"]["structuredContent"]["task_id"]
    pending = _call(north, "read_result", {"task_id": task_id})["result"]["structuredContent"]
    assert pending == {"task_id": task_id, "status": "pending", "result": None}
    claimed = _call(south, "claim_task", {})["result"]["structuredContent"]
    assert claimed["task_id"] == task_id
    completed = _call(
        south,
        "complete_task",
        {"task_id": task_id, "lease_id": claimed["lease_id"], "result": "the fix is in"},
    )["result"]["structuredContent"]
    assert completed["status"] == "completed"
    read = _call(north, "read_result", {"task_id": task_id})["result"]["structuredContent"]
    assert read == {"task_id": task_id, "status": "completed", "result": "the fix is in"}
    for app in (south, witness, legacy):
        refused = _call(app, "read_result", {"task_id": task_id})
        assert refused["error"]["message"] == "request refused"
    extra = _call(north, "read_result", {"task_id": task_id, "lease_id": "lease_x"})
    assert extra["error"]["message"] == "request refused"
    unknown = _call(north, "read_result", {"task_id": "task_" + "0" * 32})
    assert unknown["error"]["message"] == "request refused"


def test_simultaneous_member_posts_stay_isolated(tmp_path: Path) -> None:
    state, config = _graph(tmp_path)
    north = _member(state, config, "north")
    south = _member(state, config, "south")
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def post(app: MCPApplication, key: str) -> None:
        barrier.wait(timeout=5)
        results.append(_call(app, "post_board", {"board": "room", "text": key, "key": key}))

    threads = [
        threading.Thread(target=post, args=(north, "north-note")),
        threading.Thread(target=post, args=(south, "south-note")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert all("result" in item for item in results)
    posts = _call(north, "read_board", {"board": "room"})["result"]["structuredContent"]["posts"]
    assert {item["author"] for item in posts} == {"north", "south"}
    assert {item["text"] for item in posts} == {"north-note", "south-note"}
    assert list((state / "tasks").glob("task_*.json")) == []


def _post(app: MCPApplication, key: str, text: str | None = None) -> dict:
    posted = _call(app, "post_board", {"board": "room", "text": text or key, "key": key})
    return posted["result"]["structuredContent"]


def _unread(app: MCPApplication, **arguments: object) -> dict:
    payload = {"board": "room", **arguments}
    return _call(app, "poll_unread", payload)["result"]["structuredContent"]


def test_unread_requires_an_explicit_ack_and_skips_own_posts(tmp_path: Path) -> None:
    state, config = _graph(tmp_path)
    north = _member(state, config, "north")
    south = _member(state, config, "south")
    witness = _member(state, config, "witness")
    legacy = MCPApplication(DurableQueue(tmp_path / "legacy", producer_id="north", worker_id="south"), "synthetic-secret")
    first = _post(north, "one")
    second = _post(north, "two")
    assert first["seq"] < second["seq"]
    assert _unread(north)["posts"] == []
    visible = _unread(south)
    assert [item["post_id"] for item in visible["posts"]] == [first["post_id"], second["post_id"]]
    assert visible["next_after"] is None
    assert _call(south, "read_board", {"board": "room"})["result"]["structuredContent"]["posts"]
    assert [item["post_id"] for item in _unread(south)["posts"]] == [first["post_id"], second["post_id"]]
    acked = _call(south, "ack_board", {"board": "room", "posts": [first["post_id"]]})
    assert acked["result"]["structuredContent"] == {"status": "acked", "posts": [first["post_id"]]}
    again = _call(south, "ack_board", {"board": "room", "posts": [first["post_id"]]})
    assert again["result"]["structuredContent"]["posts"] == [first["post_id"]]
    assert [item["post_id"] for item in _unread(south)["posts"]] == [second["post_id"]]
    reopened = _member(state, config, "south")
    assert [item["post_id"] for item in _unread(reopened)["posts"]] == [second["post_id"]]
    mixed = _call(south, "ack_board", {"board": "room", "posts": [second["post_id"], "post_" + ("ab" * 16)]})
    assert mixed["error"]["message"] == "request refused"
    assert [item["post_id"] for item in _unread(south)["posts"]] == [second["post_id"]]
    assert _call(south, "ack_board", {"board": "room", "posts": [second["post_id"]]})["result"]["structuredContent"]["status"] == "acked"
    assert _unread(_member(state, config, "south"))["posts"] == []
    own = _post(south, "mine")
    assert _unread(south)["posts"] == []
    assert [item["post_id"] for item in _unread(north)["posts"]] == [own["post_id"]]
    assert _call(south, "ack_board", {"board": "room", "posts": [own["post_id"]]})["result"]["structuredContent"]["status"] == "acked"
    assert [item["post_id"] for item in _unread(north)["posts"]] == [own["post_id"]]
    page = _unread(north, limit=1)
    assert page["posts"][0]["post_id"] == own["post_id"]
    assert page["next_after"] is None
    third = _post(south, "next")
    first_page = _unread(north, limit=1)
    assert first_page["next_after"] == own["post_id"]
    second_page = _unread(north, after=first_page["next_after"], limit=1)
    assert [item["post_id"] for item in second_page["posts"]] == [third["post_id"]]
    assert [item["post_id"] for item in _unread(north)["posts"]] == [own["post_id"], third["post_id"]]
    for app in (witness, legacy):
        assert _call(app, "poll_unread", {"board": "room"})["error"]["message"] == "request refused"
        assert _call(app, "ack_board", {"board": "room", "posts": [own["post_id"]]})["error"]["message"] == "request refused"
    assert _call(north, "poll_unread", {"board": "room", "endpoint_id": "south"})["error"]["message"] == "request refused"
    assert _call(north, "ack_board", {"board": "room", "posts": []})["error"]["message"] == "request refused"


def test_simultaneous_posts_and_acks_do_not_drop_unread(tmp_path: Path) -> None:
    state, config = _graph(tmp_path)
    north = _member(state, config, "north")
    south = _member(state, config, "south")
    ready = threading.Barrier(2)

    def post(key: str) -> None:
        ready.wait(timeout=5)
        _post(north, key)

    threads = [threading.Thread(target=post, args=(key,)) for key in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    unread = _unread(south)["posts"]
    assert {item["text"] for item in unread} == {"alpha", "beta"}
    assert unread[0]["seq"] < unread[1]["seq"]
    ids = [item["post_id"] for item in unread]

    def acknowledge(post_id: str) -> None:
        ready.wait(timeout=5)
        assert _call(south, "ack_board", {"board": "room", "posts": [post_id]})["result"]["structuredContent"]["status"] == "acked"

    ready = threading.Barrier(2)
    acks = [threading.Thread(target=acknowledge, args=(post_id,)) for post_id in ids]
    for thread in acks:
        thread.start()
    for thread in acks:
        thread.join()
    assert _unread(south)["posts"] == []

    held = _post(north, "held")
    arrived: list[str] = []

    def ack_held() -> None:
        ready.wait(timeout=5)
        _call(south, "ack_board", {"board": "room", "posts": [held["post_id"]]})

    def post_during() -> None:
        ready.wait(timeout=5)
        arrived.append(_post(north, "during")["post_id"])

    ready = threading.Barrier(2)
    pair = [threading.Thread(target=ack_held), threading.Thread(target=post_during)]
    for thread in pair:
        thread.start()
    for thread in pair:
        thread.join()
    remaining = [item["post_id"] for item in _unread(south)["posts"]]
    assert held["post_id"] not in remaining
    assert arrived[0] in remaining


def test_http_graph_server_posts_and_promotes_for_its_bound_member(tmp_path: Path) -> None:
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    example = Path(__file__).resolve().parents[1] / "examples" / "two-endpoints.json"
    state = tmp_path / "state"
    open_endpoint(state, example, "north")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("AGENT_BUS_PRODUCER_ID", "AGENT_BUS_WORKER_ID"):
        env.pop(name, None)
    env.update(
        {
            "AGENT_BUS_STATE_DIR": str(state),
            "AGENT_BUS_CONFIG": str(example),
            "AGENT_BUS_ENDPOINT_ID": "north",
            "AGENT_BUS_BEARER_SECRET": "synthetic-review-secret",
            "AGENT_BUS_PORT": str(port),
            "AGENT_BUS_BIND_HOST": "loopback",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_bus_mcp.mcp"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def call(name: str, arguments: dict) -> dict:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=payload,
            headers={"Authorization": "Bearer synthetic-review-secret", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode())

    try:
        deadline = time.monotonic() + 8
        posted = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                posted = call("post_board", {"board": "room", "text": "south owns the fix", "key": "talk"})
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.05)
        else:
            raise AssertionError(process.stderr.read() if process.stderr else "server did not start")
        body = posted["result"]["structuredContent"]
        assert body["author"] == "north"
        assert body["handoff_id"] is None
        assert list((state / "tasks").glob("task_*.json")) == []
        read = call("read_board", {"board": "room"})["result"]["structuredContent"]["posts"][0]
        assert read["post_id"] == body["post_id"]
        promoted = call("promote_board", {"board": "room", "post": body["post_id"], "key": "own"})
        assert promoted["result"]["structuredContent"]["status"] == "promoted"
        assert len(list((state / "tasks").glob("task_*.json"))) == 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
