"""A board post is offered before it is written. A promotion still publishes a wake."""

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

from agent_bus_mcp.endpoints import open_endpoint, post_board, promote_board

SERVER = Path(shutil.which("nats-server") or "")


@pytest.fixture()
def nats_url() -> Iterator[str]:
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


def _pair(tmp_path: Path) -> Path:
    path = tmp_path / "endpoints.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": ["north", "south"],
                "allow": [{"from": "north", "to": "south", "kinds": ["chat", "handoff"]}],
                "boards": [{"id": "pair", "members": ["north", "south"]}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _collect(url: str, subject: str, received: list[bytes], ready: threading.Event, stop: threading.Event) -> None:
    async def run() -> None:
        connection = await nats.connect(servers=[url])

        async def save(message) -> None:
            received.append(message.data)

        await connection.subscribe(subject, cb=save)
        await connection.flush()
        ready.set()
        while not stop.is_set():
            await asyncio.sleep(0.05)
        await connection.close()

    asyncio.run(run())


def _answer(url: str, subject: str, seen: list[bytes], ready: threading.Event, stop: threading.Event) -> None:
    async def run() -> None:
        connection = await nats.connect(servers=[url])

        async def take(message) -> None:
            seen.append(message.data)
            if message.reply:
                await message.respond(b"ok")

        await connection.subscribe(subject, cb=take)
        await connection.flush()
        ready.set()
        while not stop.is_set():
            await asyncio.sleep(0.05)
        await connection.close()

    asyncio.run(run())


def test_a_subscriber_takes_the_post_and_the_board_stays_empty(tmp_path: Path, nats_url: str) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    seen: list[bytes] = []
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_answer, args=(nats_url, "board.pair", seen, ready, stop))
    thread.start()
    assert ready.wait(5)
    posted = post_board(
        north,
        config=config,
        board="pair",
        text="the note goes to the subscriber",
        key="once",
        notify_url=nats_url,
    )
    stop.set()
    thread.join(5)
    assert posted["delivered"] == "nats"
    assert posted["post_id"] is None
    assert not (tmp_path / "state" / "boards" / "pair" / "posts").exists()
    assert b"note goes" in seen[0]
    again = post_board(
        north,
        config=config,
        board="pair",
        text="the note goes to the subscriber",
        key="once",
        notify_url=nats_url,
    )
    assert again["delivered"] == "nats"
    assert not (tmp_path / "state" / "boards" / "pair" / "posts").exists()


def test_nobody_listening_writes_the_board(tmp_path: Path, nats_url: str) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    posted = post_board(
        north,
        config=config,
        board="pair",
        text="nobody was listening",
        key="once",
        notify_url=nats_url,
    )
    assert posted["post_id"].startswith("post_")
    assert posted["text"] == "nobody was listening"
    saved = json.loads((tmp_path / "state" / "boards" / "pair" / "posts" / f"{posted['post_id']}.json").read_text())
    assert saved["text"] == "nobody was listening"


def test_missing_server_does_not_fail_the_post(tmp_path: Path) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    posted = post_board(
        north,
        config=config,
        board="pair",
        text="still stored",
        key="once",
        notify_url="nats://127.0.0.1:1",
    )
    assert posted["text"] == "still stored"


def test_new_promotion_publishes_a_task_wake(tmp_path: Path, nats_url: str) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    posted = post_board(north, config=config, board="pair", text="own this", key="talk")
    received: list[bytes] = []
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_collect, args=(nats_url, "task.>", received, ready, stop))
    thread.start()
    assert ready.wait(5)
    task_id = promote_board(
        north,
        config=config,
        board="pair",
        post_id=posted["post_id"],
        key="own",
        to="south",
        notify_url=nats_url,
    )
    deadline = time.monotonic() + 2
    while not received and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    assert json.loads(received[0]) == {
        "kind": "task",
        "board": "pair",
        "post_id": posted["post_id"],
        "task_id": task_id,
        "destination": "south",
    }
