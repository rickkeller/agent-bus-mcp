"""The NATS ask path: one question, one queue group, one answer, no stored work."""

import asyncio
import json
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import nats
import pytest

from agent_bus_mcp.ask import QUEUE_GROUP, ask, ask_subject
from agent_bus_mcp.endpoints import open_endpoint
from agent_bus_mcp.mcp import MCPApplication, TOOLS
from agent_bus_mcp.queue import DurableQueue, QueueRefused

SERVER = Path(shutil.which("nats-server") or "")


@pytest.fixture()
def nats_server() -> Iterator[str]:
    """Start the local server binary on one free loopback port, then stop it."""
    if not SERVER.is_file():
        pytest.skip("nats-server is not on PATH")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    process = subprocess.Popen(
        [str(SERVER), "-a", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise AssertionError("the NATS server exited before accepting connections")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("the NATS server did not start")
                time.sleep(0.05)
        yield f"nats://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class _Listener:
    """One queue-group member on ask.<work>. It answers, or stays silent."""

    def __init__(self, url: str, work: str, answer: str | None) -> None:
        self._url, self._subject = url, ask_subject(work)
        self._answer = None if answer is None else answer.encode("utf-8")
        self.received: list[bytes] = []
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        connection = await nats.connect(servers=[self._url])

        async def handle(message) -> None:
            self.received.append(message.data)
            if self._answer is not None:
                await message.respond(self._answer)

        await connection.subscribe(self._subject, queue=QUEUE_GROUP, cb=handle)
        await connection.flush()
        self.ready.set()
        while not self._stop.is_set():
            await asyncio.sleep(0.02)
        await connection.close()

    def join(self, timeout: float = 10) -> None:
        assert self.ready.wait(timeout=timeout)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def _call(app: MCPApplication, name: str, arguments: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    sent: list[dict] = []

    async def send(event: dict) -> None:
        sent.append(event)

    asyncio.run(
        app(
            {"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"authorization", b"Bearer synthetic-secret")]},
            receive,
            send,
        )
    )
    return json.loads(sent[1]["body"])


def _member(tmp_path: Path, endpoint: str, nats_url: str) -> MCPApplication:
    config = tmp_path / "endpoints.json"
    config.write_text(
        json.dumps(
            {
                "endpoints": ["north", "south"],
                "allow": [
                    {"from": "north", "to": "south", "kinds": ["chat", "handoff"]},
                    {"from": "south", "to": "north", "kinds": ["chat"]},
                ],
                "boards": [{"id": "room", "members": ["north", "south"]}],
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    return MCPApplication(open_endpoint(state, config, endpoint), "synthetic-secret", config, nats_url)


def test_the_subject_takes_one_plain_token_of_work() -> None:
    assert ask_subject("alpha") == "ask.alpha"
    assert ask_subject("A1-b2") == "ask.A1-b2"
    for refused in ("", "a.b", "a b", "a_b", "a/b", "a+b", "a,b", "a:b", "a\nb", " north", "north ", "..", "ask.alpha"):
        with pytest.raises(QueueRefused):
            ask_subject(refused)
    for refused in (None, 7, b"alpha", ["alpha"]):
        with pytest.raises(QueueRefused):
            ask_subject(refused)


def test_text_and_deadline_are_checked_before_the_ask_leaves() -> None:
    for bad_text in ("", "   ", 5, None, b"raw", "x" * 2049):
        with pytest.raises(QueueRefused):
            ask("nats://127.0.0.1:1", work="alpha", text=bad_text, timeout_s=1)
    for bad_deadline in (0, -1, "1", True, None, float("inf"), float("nan")):
        with pytest.raises(QueueRefused):
            ask("nats://127.0.0.1:1", work="alpha", text="who owns the fix?", timeout_s=bad_deadline)


def test_one_queue_group_takes_each_ask_so_two_listeners_give_one_answer(nats_server: str) -> None:
    first = _Listener(nats_server, "alpha", answer="first answer")
    second = _Listener(nats_server, "alpha", answer="second answer")
    other = _Listener(nats_server, "beta", answer="wrong subject")
    try:
        first.join()
        second.join()
        other.join()
        result = ask(nats_server, work="alpha", text="who owns the fix?", timeout_s=10)
        assert result["status"] == "answered"
        assert result["answer"] in {"first answer", "second answer"}
        assert len(first.received) + len(second.received) == 1
        assert other.received == []
        again = ask(nats_server, work="alpha", text="and now?", timeout_s=10)
        assert again["status"] == "answered"
        assert again["answer"] in {"first answer", "second answer"}
        assert sorted(first.received + second.received) == [b"and now?", b"who owns the fix?"]
        assert other.received == []
    finally:
        first.stop()
        second.stop()
        other.stop()


def test_nobody_listening_fails_at_once_with_no_responders(nats_server: str) -> None:
    started = time.monotonic()
    result = ask(nats_server, work="alpha", text="is anyone there?", timeout_s=30)
    elapsed = time.monotonic() - started
    assert result == {"status": "no_responders", "answer": None}
    assert elapsed < 10


def test_a_silent_listener_fails_when_the_deadline_passes(nats_server: str) -> None:
    silent = _Listener(nats_server, "alpha", answer=None)
    try:
        silent.join()
        started = time.monotonic()
        result = ask(nats_server, work="alpha", text="please answer", timeout_s=1.0)
        elapsed = time.monotonic() - started
        assert result == {"status": "timeout", "answer": None}
        assert 0.9 <= elapsed < 5
        assert silent.received == [b"please answer"]
    finally:
        silent.stop()


def test_a_dead_server_is_refused_quickly() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = int(probe.getsockname()[1])
    started = time.monotonic()
    with pytest.raises(nats.errors.NoServersError):
        ask(f"nats://127.0.0.1:{dead_port}", work="alpha", text="is anyone there?", timeout_s=30)
    assert time.monotonic() - started < 20


def test_the_ask_tool_is_one_closed_tool_with_three_arguments() -> None:
    tool = {item["name"]: item for item in TOOLS}["ask"]
    schema = tool["inputSchema"]
    assert set(schema["properties"]) == {"work", "text", "timeout_s"}
    assert schema["required"] == ["work", "text", "timeout_s"]
    assert schema["additionalProperties"] is False
    assert "does not create a board post" in tool["description"]


def test_the_ask_tool_answers_and_creates_no_board_post(tmp_path: Path, nats_server: str) -> None:
    north = _member(tmp_path, "north", nats_server)
    responder = _Listener(nats_server, "alpha", answer="the fix is south's")
    try:
        responder.join()
        called = _call(north, "ask", {"work": "alpha", "text": "who owns the fix?", "timeout_s": 10})
        assert called["result"]["structuredContent"] == {"status": "answered", "answer": "the fix is south's"}
        assert responder.received == [b"who owns the fix?"]
        empty = _call(north, "read_board", {"board": "room"})["result"]["structuredContent"]
        assert empty["posts"] == []
        state = tmp_path / "state"
        assert list((state / "tasks").glob("task_*.json")) == []
        assert list((state / "consultations").glob("consultation_*.json")) == []
        assert list((state / "boards").glob("**/post_*.json")) == []
    finally:
        responder.stop()


def test_the_ask_tool_reports_no_responders_and_timeout(tmp_path: Path, nats_server: str) -> None:
    app = MCPApplication(DurableQueue(tmp_path / "state", producer_id="producer", worker_id="worker"), "synthetic-secret", None, nats_server)
    started = time.monotonic()
    quiet = _call(app, "ask", {"work": "alpha", "text": "anyone?", "timeout_s": 30})
    elapsed = time.monotonic() - started
    assert quiet["result"]["structuredContent"] == {"status": "no_responders", "answer": None}
    assert elapsed < 10
    silent = _Listener(nats_server, "alpha", answer=None)
    try:
        silent.join()
        timed_out = _call(app, "ask", {"work": "alpha", "text": "anyone?", "timeout_s": 1})
        assert timed_out["result"]["structuredContent"] == {"status": "timeout", "answer": None}
    finally:
        silent.stop()


def test_the_ask_tool_refuses_other_shapes_and_values(tmp_path: Path, nats_server: str) -> None:
    configured = MCPApplication(DurableQueue(tmp_path / "state", producer_id="producer", worker_id="worker"), "synthetic-secret", None, nats_server)
    for arguments in (
        {"work": "a.b", "text": "hi", "timeout_s": 1},
        {"work": "north south", "text": "hi", "timeout_s": 1},
        {"work": "alpha", "text": "hi", "timeout_s": 0},
        {"work": "alpha", "text": "hi", "timeout_s": -2},
        {"work": "alpha", "text": "hi", "timeout_s": "1"},
        {"work": "alpha", "text": "hi", "timeout_s": True},
        {"work": "alpha", "text": "", "timeout_s": 1},
        {"work": "alpha", "text": "hi", "timeout_s": 1, "to": "south"},
        {"work": "alpha", "text": "x" * 2049, "timeout_s": 1},
        {"work": "alpha", "text": "hi"},
        {},
    ):
        refused = _call(configured, "ask", arguments)
        assert refused["error"]["message"] == "request refused"
    unconfigured = MCPApplication(DurableQueue(tmp_path / "other", producer_id="producer", worker_id="worker"), "synthetic-secret")
    without_server = _call(unconfigured, "ask", {"work": "alpha", "text": "hi", "timeout_s": 1})
    assert without_server["error"]["message"] == "request refused"
