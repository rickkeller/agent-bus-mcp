"""Refusal messages name the failed check without echoing the refused content."""

from pathlib import Path

import pytest

from agent_bus_mcp.endpoints import file_accepted, open_endpoint, post_board, promote_board
from agent_bus_mcp.mcp import MCPApplication
from agent_bus_mcp.queue import DurableQueue, MAX_ANSWER_BYTES, MAX_QUESTION_BYTES, MAX_RESULT_BYTES, QueueRefused


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "endpoints.json"
    path.write_text(
        '{"endpoints":["alice","bob","carol"],'
        '"allow":[{"from":"alice","to":"bob","kinds":["chat","handoff"]},'
        '{"from":"bob","to":"alice","kinds":["chat"]}],'
        '"boards":[{"id":"pair","members":["alice","bob"]}]}',
        encoding="utf-8",
    )
    return path


def _request(app: MCPApplication, arguments: dict, *, tool: str = "post_board") -> dict:
    try:
        return app._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
    except QueueRefused as exc:
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": str(exc)}}


def _member(tmp_path: Path, endpoint: str) -> MCPApplication:
    config = _config(tmp_path)
    return MCPApplication(open_endpoint(tmp_path / "state", config, endpoint), "synthetic-secret", config)


def test_queue_refused_prefixes_a_reason_and_keeps_the_bare_message() -> None:
    assert str(QueueRefused()) == "request refused"
    assert str(QueueRefused("key is invalid")) == "request refused: key is invalid"


def test_post_board_names_the_byte_limit_and_does_not_echo_the_text(tmp_path: Path) -> None:
    config = _config(tmp_path)
    alice = open_endpoint(tmp_path / "state", config, "alice")
    post_board(alice, config=config, board="pair", text="x" * 8192, key="fit")
    with pytest.raises(QueueRefused, match=r"^request refused: text is 8193 bytes, limit is 8192$"):
        post_board(alice, config=config, board="pair", text="x" * 8193, key="over")
    with pytest.raises(QueueRefused, match=r"^request refused: text is empty$"):
        post_board(alice, config=config, board="pair", text="   ", key="blank")
    with pytest.raises(QueueRefused, match=r"^request refused: not a member of board pair$") as caught:
        post_board(open_endpoint(tmp_path / "state", config, "carol"), config=config, board="pair", text="secret-token", key="no")
    assert "secret-token" not in str(caught.value)


def test_file_accepted_names_a_control_character_position_and_a_spoofed_caller(tmp_path: Path) -> None:
    config = _config(tmp_path)
    alice = open_endpoint(tmp_path / "state", config, "alice")
    with pytest.raises(QueueRefused, match=r"^request refused: contains a control character at position 6$") as caught:
        file_accepted(alice, to="bob", text="unsafe\x00text", caller_id="alice", key="control")
    assert "unsafe" not in str(caught.value)
    assert "\x00" not in str(caught.value)
    with pytest.raises(QueueRefused, match=r"^request refused: caller is not this endpoint$"):
        file_accepted(alice, to="bob", text="develop the intake", caller_id="bob", key="spoof")
    with pytest.raises(QueueRefused, match=r"^request refused: text is empty$"):
        file_accepted(alice, to="bob", text="  ", caller_id="alice", key="blank")


def test_question_result_and_answer_caps_stay_at_2048(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    with pytest.raises(QueueRefused, match=r"^request refused: question is 2049 bytes, limit is 2048$"):
        queue.ask_question(
            question="x" * (MAX_QUESTION_BYTES + 1),
            origin_ref="ref",
            idempotency_key="q",
            expires_in_seconds=60,
        )
    with pytest.raises(QueueRefused, match=r"^request refused: contains a control character at position 0$"):
        queue.ask_question(
            question="\x01ok",
            origin_ref="ref",
            idempotency_key="c",
            expires_in_seconds=60,
        )
    task_id = queue.enqueue(goal="work", references=[], idempotency_key="task")
    claimed = DurableQueue(tmp_path, producer_id="producer", worker_id="worker").claim()
    assert claimed is not None
    with pytest.raises(QueueRefused, match=r"^request refused: result is 2049 bytes, limit is 2048$"):
        DurableQueue(tmp_path, producer_id="producer", worker_id="worker").finish(
            task_id=claimed["task_id"],
            lease_id=claimed["lease_id"],
            result="y" * (MAX_RESULT_BYTES + 1),
            failed=False,
        )
    assert task_id == claimed["task_id"]
    DurableQueue(tmp_path, producer_id="producer", worker_id="worker").finish(
        task_id=claimed["task_id"],
        lease_id=claimed["lease_id"],
        result="ok",
        failed=False,
    )
    consultation = queue.ask_question(
        question="bounded?",
        origin_ref="ref",
        idempotency_key="ans",
        expires_in_seconds=60,
    )
    taken = DurableQueue(tmp_path, producer_id="producer", worker_id="worker").claim_question()
    assert taken is not None
    with pytest.raises(QueueRefused, match=r"^request refused: answer is 2049 bytes, limit is 2048$"):
        DurableQueue(tmp_path, producer_id="producer", worker_id="worker").answer_question(
            consultation_id=taken["consultation_id"],
            lease_id=taken["lease_id"],
            answer="z" * (MAX_ANSWER_BYTES + 1),
        )
    assert consultation["consultation_id"] == taken["consultation_id"]


def test_only_the_author_can_promote_a_post(tmp_path: Path) -> None:
    path = tmp_path / "endpoints.json"
    path.write_text(
        '{"endpoints":["alice","bob"],'
        '"allow":[{"from":"alice","to":"bob","kinds":["chat","handoff"]},'
        '{"from":"bob","to":"alice","kinds":["chat","handoff"]}],'
        '"boards":[{"id":"pair","members":["alice","bob"]}]}',
        encoding="utf-8",
    )
    alice = open_endpoint(tmp_path / "state", path, "alice")
    bob = open_endpoint(tmp_path / "state", path, "bob")
    posted = post_board(alice, config=path, board="pair", text="bob should not promote this", key="talk")
    with pytest.raises(QueueRefused, match=r"^request refused: only the author can promote this post$"):
        promote_board(bob, config=path, board="pair", post_id=posted["post_id"], key="own")


def test_mcp_returns_the_same_code_with_the_specific_reason(tmp_path: Path) -> None:
    alice = _member(tmp_path, "alice")
    carol = _member(tmp_path, "carol")
    cases = [
        (alice, "post_board", {"board": "pair", "text": "x" * 8193, "key": "long"}, "request refused: text is 8193 bytes, limit is 8192"),
        (alice, "post_board", {"board": "pair", "text": "   ", "key": "blank"}, "request refused: text is empty"),
        (carol, "post_board", {"board": "pair", "text": "secret-token", "key": "no"}, "request refused: not a member of board pair"),
        (alice, "post_board", {"board": "../pair", "text": "path", "key": "path"}, "request refused: board is invalid"),
        (alice, "post_board", {"board": "pair", "text": "ok", "key": "bad key"}, "request refused: key is invalid"),
        (alice, "complete_task", {"task_id": "task_" + "0" * 32, "lease_id": "lease_" + "0" * 32, "result": "n" * 2049}, "request refused: result is 2049 bytes, limit is 2048"),
        (alice, "unknown_tool", {"board": "pair"}, "request refused: unknown tool or bad arguments"),
    ]
    for app, tool, arguments, message in cases:
        response = _request(app, arguments, tool=tool)
        assert response["id"] == 1
        assert response["error"]["code"] == -32600
        assert response["error"]["message"] == message
        assert "secret-token" not in response["error"]["message"]
        assert "n" * 20 not in response["error"]["message"]


def test_mcp_auth_and_corrupt_state_stay_a_bare_refusal(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    task_id = queue.enqueue(goal="read", references=[], idempotency_key="corrupt")
    task_path = tmp_path / "tasks" / f"{task_id}.json"
    record = __import__("json").loads(task_path.read_text())
    record["status"] = "leased"
    record["lease_expires_at"] = 7
    task_path.write_text(__import__("json").dumps(record))
    import asyncio
    import json

    app = MCPApplication(queue, "synthetic-secret")
    sent: list[dict] = []

    async def receive() -> dict:
        return {
            "type": "http.request",
            "body": b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}',
            "more_body": False,
        }

    async def send(event: dict) -> None:
        sent.append(event)

    asyncio.run(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"authorization", b"Bearer synthetic-secret")],
            },
            receive,
            send,
        )
    )
    assert json.loads(sent[1]["body"]) == {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "request refused"}}
