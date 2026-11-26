"""A new post or promotion publishes one wake. The text stays in the file."""

import asyncio
import json
import threading
import time
from pathlib import Path

import nats
import pytest

from agent_bus_mcp.endpoints import open_endpoint, post_board, promote_board

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


def test_an_answered_offer_still_writes_the_post(tmp_path: Path, nats_url: str) -> None:
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
        text="the report text goes to the subscriber",
        key="once",
        notify_url=nats_url,
    )
    stop.set()
    thread.join(5)
    assert posted["delivered"] == "live"
    assert posted["post_id"].startswith("post_")
    assert type(posted["seq"]) is int and posted["seq"] >= 1
    saved = json.loads(
        (tmp_path / "state" / "boards" / "pair" / "posts" / f"{posted['post_id']}.json").read_text()
    )
    assert saved["text"] == "the report text goes to the subscriber"
    assert b"report text" in seen[0]
    again = post_board(
        north,
        config=config,
        board="pair",
        text="the report text goes to the subscriber",
        key="once",
        notify_url=nats_url,
    )
    assert again["delivered"] == "live"
    assert again["post_id"] == posted["post_id"]
    offers = [payload for payload in seen if b"report text" in payload]
    assert len(offers) == 1


def test_the_same_key_offers_once_only(tmp_path: Path, nats_url: str) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    seen: list[bytes] = []
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_answer, args=(nats_url, "board.pair", seen, ready, stop))
    thread.start()
    assert ready.wait(5)
    first = post_board(north, config=config, board="pair", text="offer once", key="once", notify_url=nats_url)
    stop.set()
    thread.join(5)
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_answer, args=(nats_url, "board.pair", seen, ready, stop))
    thread.start()
    assert ready.wait(5)
    second = post_board(north, config=config, board="pair", text="offer once", key="once", notify_url=nats_url)
    stop.set()
    thread.join(5)
    assert first["post_id"] == second["post_id"]
    assert sum(1 for payload in seen if b"offer once" in payload) == 1


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
    assert posted["delivered"] == "board"
    saved = json.loads((tmp_path / "state" / "boards" / "pair" / "posts" / f"{posted['post_id']}.json").read_text())
    assert saved["text"] == "nobody was listening"


def test_nats_up_with_no_subscriber_writes_the_post_at_once(tmp_path: Path, nats_url: str) -> None:
    """The offer is offered to nobody: no_responders, post written, no error."""
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    posted = post_board(
        north,
        config=config,
        board="pair",
        text="no subscriber anywhere",
        key="quiet",
        notify_url=nats_url,
    )
    assert posted["delivered"] == "board"
    assert posted["post_id"].startswith("post_")
    assert (tmp_path / "state" / "boards" / "pair" / "posts" / f"{posted['post_id']}.json").exists()


def test_a_written_post_publishes_one_observer_wake_without_text(tmp_path: Path, nats_url: str) -> None:
    config = _pair(tmp_path)
    north = open_endpoint(tmp_path / "state", config, "north")
    received: list[bytes] = []
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_collect, args=(nats_url, "wake.board.pair", received, ready, stop))
    thread.start()
    assert ready.wait(5)
    posted = post_board(north, config=config, board="pair", text="see me", key="w1", notify_url=nats_url)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            wakes = [json.loads(payload) for payload in received if "text" not in json.loads(payload)]
        except json.JSONDecodeError:
            wakes = []
        if wakes:
            break
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    assert wakes == [
        {"kind": "post", "board": "pair", "post_id": posted["post_id"], "author": "north", "seq": posted["seq"]}
    ]


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
