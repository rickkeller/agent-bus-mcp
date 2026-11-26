"""The graph-door wait_unread: a hint hold, not a read, ack, claim, or answer."""

import json
import threading
import time

import pytest

from agent_bus_mcp.endpoints import ack_board, open_endpoint, post_board, wait_unread
from agent_bus_mcp.queue import QueueRefused


def _pair(tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text(
        """{
  "endpoints": ["north", "south"],
  "allow": [{"from": "north", "to": "south", "kinds": ["chat"]},
            {"from": "south", "to": "north", "kinds": ["chat"]}],
  "boards": [{"id": "pair", "members": ["north", "south"]}]
}""",
        encoding="utf-8",
    )
    return path


def _member(tmp_path, config, name):
    return open_endpoint(tmp_path / "state", config, name)


def test_wait_returns_ready_with_a_hint_and_no_ids(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    south = _member(tmp_path, config, "south")
    post_board(south, config=config, board="pair", text="south asks north", key="s1")
    result = wait_unread(north, config=config, arguments={"timeout_seconds": 2})
    assert result["status"] == "ready"
    assert result["unread_boards"] == ["pair"]
    assert result["claimable"] is False
    assert result["holding_lease"] is False
    assert set(result) == {"status", "unread_boards", "claimable", "holding_lease"}
    assert "south asks north" not in str(result)


def test_wait_times_out_when_the_board_is_quiet(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    started = time.monotonic()
    result = wait_unread(north, config=config, arguments={"timeout_seconds": 1})
    assert result["status"] == "timeout"
    assert result["unread_boards"] == []
    assert 0.8 <= time.monotonic() - started <= 4


def test_own_posts_do_not_make_the_wait_ready(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    post_board(north, config=config, board="pair", text="north talks to itself", key="n1")
    result = wait_unread(north, config=config, arguments={"timeout_seconds": 1})
    assert result["status"] == "timeout"
    assert result["unread_boards"] == []


def test_an_ack_clears_the_wait_without_touching_the_board(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    south = _member(tmp_path, config, "south")
    posted = post_board(south, config=config, board="pair", text="ack me", key="s1")
    acked = ack_board(north, config=config, board="pair", post_ids=[posted["post_id"]])
    assert acked == [posted["post_id"]]
    assert wait_unread(north, config=config, arguments={"timeout_seconds": 1})["status"] == "timeout"


def test_a_second_parked_wait_for_the_same_endpoint_is_refused(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    parked: dict = {}

    def hold():
        parked["first"] = wait_unread(north, config=config, arguments={"timeout_seconds": 4})

    thread = threading.Thread(target=hold)
    thread.start()
    time.sleep(0.3)
    try:
        with pytest.raises(QueueRefused):
            wait_unread(north, config=config, arguments={"timeout_seconds": 1})
    finally:
        thread.join(6)
    assert parked.get("first", {}).get("status") == "timeout"


def test_an_in_process_write_without_nats_wakes_quicker_than_five_seconds(tmp_path):
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    south = _member(tmp_path, config, "south")
    outcomes: dict = {}

    def hold():
        outcomes["wait"] = wait_unread(north, config=config, arguments={"timeout_seconds": 12})

    thread = threading.Thread(target=hold)
    thread.start()
    time.sleep(0.3)  # let the wait park before the write
    started = time.monotonic()
    post_board(south, config=config, board="pair", text="in process bump", key="s1", notify_url=None)
    thread.join(6)
    result = outcomes.get("wait") or {}
    assert result.get("status") == "ready", result
    assert time.monotonic() - started <= 4, "the in-process bump should beat the five-second recheck"


def test_an_in_process_write_wakes_quicker_than_five_seconds(tmp_path, nats_url):
    """Both wake paths fire: the in-process bump and the NATS wake."""
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    south = _member(tmp_path, config, "south")
    outcomes: dict = {}

    def hold():
        outcomes["wait"] = wait_unread(
            north, config=config, arguments={"timeout_seconds": 12}, notify_url=nats_url
        )

    thread = threading.Thread(target=hold)
    thread.start()
    time.sleep(0.5)  # let the wait park before the write
    started = time.monotonic()
    post_board(south, config=config, board="pair", text="wake up", key="s1", notify_url=nats_url)
    thread.join(6)
    result = outcomes.get("wait") or {}
    assert result.get("status") == "ready", result
    assert time.monotonic() - started <= 4, "a bump or a wake must beat the five-second recheck"


def test_the_wake_subject_fires_the_watch_callback(tmp_path, nats_url):
    from agent_bus_mcp.notify import WakeWatch, publish_wake, wake_subject_for_board

    marks: list[int] = []
    watch = WakeWatch(nats_url, "north", ["pair"], on_wake=lambda: marks.append(1))
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            probe = publish_wake(
                nats_url,
                wake_subject_for_board("pair"),
                {"kind": "post", "board": "pair", "post_id": "post_" + "ab" * 16, "author": "south", "seq": 1},
            )
            if probe and marks:
                break
            time.sleep(0.05)
        watch.stop()
    finally:
        pass
    assert marks, "the wake did not reach the watch callback"


def test_a_dead_nats_leaves_no_lingering_watch_thread(tmp_path):
    from agent_bus_mcp.notify import WakeWatch

    watch = WakeWatch("nats://127.0.0.1:1", "north", ["pair"])
    time.sleep(0.8)  # the bounded connect attempt fails and the thread exits
    watch.stop()
    assert not watch._thread.is_alive()


def test_parked_wait_does_not_stall_the_live_offer(tmp_path, nats_url):
    """A parked watch must not sit on board.<board>, so no offer waits it out."""
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    posted = post_board(north, config=config, board="pair", text="own note", key="n1")

    parked: dict = {}

    def hold():
        parked["wait"] = wait_unread(
            north, config=config, arguments={"timeout_seconds": 8}, notify_url=nats_url
        )

    thread = threading.Thread(target=hold)
    thread.start()
    time.sleep(1.0)  # the wait parks and the wake watch subscribes
    from agent_bus_mcp.notify import board_subject, offer

    started = time.monotonic()
    outcome = offer(
        nats_url,
        board_subject("pair"),
        {
            "kind": "post",
            "board": "pair",
            "author": "north",
            "text": "fast offer",
            "key": "probe",
            "post_id": posted["post_id"],
            "seq": 1,
        },
    )
    elapsed = time.monotonic() - started
    try:
        # Nobody is subscribed to board.pair: the parked watch is not, so the
        # offer fails over at once instead of waiting out a silent listener.
        assert outcome == "no_responders"
        assert elapsed <= 0.5, f"the offer must not wait behind a parked wake watch (took {elapsed:.2f}s)"
    finally:
        thread.join(10)
        assert parked.get("wait", {}).get("status") == "timeout"


def test_a_crash_left_offering_mark_resolves_on_replay(tmp_path, nats_url):
    """Offering older than the window is what a crash left behind: resolved once."""
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    posted = post_board(north, config=config, board="pair", text="crashed mid offer", key="c1", notify_url=nats_url)
    key_path = tmp_path / "state" / "boards" / "pair" / "keys" / "north" / "c1"
    key_path.write_text(
        json.dumps({"post_id": posted["post_id"], "offering": True, "offering_since": 0.0}),
        encoding="utf-8",
    )
    started = time.monotonic()
    again = post_board(north, config=config, board="pair", text="crashed mid offer", key="c1", notify_url=nats_url)
    elapsed = time.monotonic() - started
    assert again["delivered"] == "board"
    assert again["post_id"] == posted["post_id"]
    # The stale mark resolves at once: no full four-second window is paid.
    assert elapsed <= 0.5, f"the stale offering mark must not cost a full window (took {elapsed:.2f}s)"
    saved = json.loads(key_path.read_text())
    assert saved.get("offering") is None
    assert saved.get("delivered") == "board"


def test_an_offer_mark_without_a_stamp_resolves_at_once(tmp_path, nats_url):
    """A mark from the format before the stamp is treated as stale."""
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    posted = post_board(north, config=config, board="pair", text="unstamped mark", key="u1", notify_url=nats_url)
    key_path = tmp_path / "state" / "boards" / "pair" / "keys" / "north" / "u1"
    key_path.write_text(json.dumps({"post_id": posted["post_id"], "offering": True}), encoding="utf-8")
    started = time.monotonic()
    again = post_board(north, config=config, board="pair", text="unstamped mark", key="u1", notify_url=nats_url)
    assert again["delivered"] == "board"
    assert again["post_id"] == posted["post_id"]
    assert time.monotonic() - started <= 0.5
    saved = json.loads(key_path.read_text())
    assert saved.get("offering") is None
    assert saved.get("delivered") == "board"


def test_a_fresh_crash_mark_waits_out_the_window_then_resolves(tmp_path, nats_url):
    """A one-second-old mark is still inside the window, so the replay waits it out."""
    config = _pair(tmp_path)
    north = _member(tmp_path, config, "north")
    posted = post_board(north, config=config, board="pair", text="fresh crash", key="f1", notify_url=nats_url)
    key_path = tmp_path / "state" / "boards" / "pair" / "keys" / "north" / "f1"
    key_path.write_text(
        json.dumps({"post_id": posted["post_id"], "offering": True, "offering_since": time.time() - 1.0}),
        encoding="utf-8",
    )
    started = time.monotonic()
    again = post_board(north, config=config, board="pair", text="fresh crash", key="f1", notify_url=nats_url)
    elapsed = time.monotonic() - started
    assert again["delivered"] == "board"
    assert again["post_id"] == posted["post_id"]
    assert 2.0 <= elapsed <= 5.0, f"the fresh mark should wait out the remaining window (took {elapsed:.2f}s)"
    saved = json.loads(key_path.read_text())
    assert saved.get("offering") is None
    assert saved.get("delivered") == "board"
