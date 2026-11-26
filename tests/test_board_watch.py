"""The board watcher example: turn-command quoting, idle backoff, failure backoff, and the hourly cap."""

import importlib.util
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "examples" / "board-watch.py"
_SPEC = importlib.util.spec_from_file_location("board_watch", _PATH)
bw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bw)

ENV = {"AGENT_BUS_DOOR_URL": "https://door.example.invalid/room/mcp", "AGENT_BUS_BEARER": "not-a-real-token"}


def _config(tmp_path: Path, **extra: str):
    return bw.Config(tmp_path / "watch", environ={**ENV, **extra})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeDoor:
    """wait_unread is level-triggered: ready while any post is unacked."""

    def __init__(self, posts: list[dict]) -> None:
        self.posts = posts
        self.calls: list[str] = []

    def __call__(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        if name == "wait_unread":
            boards = sorted({post["board"] for post in self.posts})
            return {"status": "ready" if boards else "timeout", "unread_boards": boards}
        if name == "poll_unread":
            return {"posts": [post for post in self.posts if post["board"] == arguments["board"]]}
        raise AssertionError(name)


def _post(n: int, board: str = "room") -> dict:
    return {"post_id": "post_%032x" % n, "board": board, "author": "peer", "seq": n, "text": "hello"}


def test_default_turn_command_keeps_the_prompt_as_one_argument(tmp_path: Path) -> None:
    config = _config(tmp_path, AGENT_BUS_TURN_TOOLS="mcp__bus__*")
    argv = bw.build_turn_argv(config.turn_command, Path("/tmp/notice-1.json"))
    assert argv[:2] == ["claude", "-p"]
    assert argv[2].startswith("A board watcher") and "/tmp/notice-1.json" in argv[2]
    assert argv[3:] == ["--allowedTools", "mcp__bus__*"]
    assert "--allowedTools" not in bw.default_turn_command()


def test_a_quoted_configured_command_is_split_like_a_shell(tmp_path: Path) -> None:
    command = (
        'ollama launch claude --model some-model:cloud --yes -- -p '
        '"Read {notice} and act on it. Keep it short." --allowedTools "mcp__bus__*"'
    )
    config = _config(tmp_path, AGENT_BUS_TURN_COMMAND=command)
    argv = bw.build_turn_argv(config.turn_command, Path("/s/notice-9.json"))
    assert argv == [
        "ollama", "launch", "claude", "--model", "some-model:cloud", "--yes", "--",
        "-p", "Read /s/notice-9.json and act on it. Keep it short.", "--allowedTools", "mcp__bus__*",
    ]


def test_an_unbalanced_quote_is_refused_at_start(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _config(tmp_path, AGENT_BUS_TURN_COMMAND='claude -p "unterminated')


def test_failure_backoff_doubles_to_the_cap_and_success_resets() -> None:
    clock = Clock()
    pacer = bw.Pacer(max_turns_per_hour=1000, fail_backoff=30, clock=clock)
    assert pacer.wait_seconds() == 0
    waits = []
    for _ in range(7):
        pacer.record_turn(False)
        waits.append(pacer.wait_seconds())
        clock.now += waits[-1]
    assert waits == [30, 60, 120, 240, 480, 600, 600]
    pacer.record_turn(True)
    assert pacer.wait_seconds() == 0


def test_hourly_cap_blocks_the_next_turn_until_the_oldest_ages_out() -> None:
    clock = Clock()
    pacer = bw.Pacer(max_turns_per_hour=3, fail_backoff=30, clock=clock)
    for _ in range(3):
        assert pacer.wait_seconds() == 0
        pacer.record_turn(True)
        clock.now += 60
    assert pacer.wait_seconds() == pytest.approx(3600 - 180)
    clock.now += 3600 - 180
    assert pacer.wait_seconds() == 0


def test_handled_but_unacked_posts_do_not_spin_or_start_a_second_turn(tmp_path: Path) -> None:
    config = _config(tmp_path, AGENT_BUS_IDLE_BACKOFF_SECONDS="60")
    door = FakeDoor([_post(1)])
    turns, sleeps = [], []
    bw.watch(config, once=False, call=door, turn=lambda posts: turns.append(posts) or True,
             sleep=sleeps.append, max_cycles=3)
    assert len(turns) == 1  # the turn did not ack, but the post is not handed over again
    assert sum(sleeps) == pytest.approx(120)  # two idle cycles of 60 s, in 5 s rechecks
    assert max(sleeps) <= 5
    assert door.calls.count("wait_unread") == 3


def test_a_new_post_during_idle_backoff_starts_a_turn_promptly(tmp_path: Path) -> None:
    config = _config(tmp_path, AGENT_BUS_IDLE_BACKOFF_SECONDS="60")
    door = FakeDoor([_post(1)])
    turns, sleeps = [], []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            door.posts.append(_post(2))

    bw.watch(config, once=False, call=door, turn=lambda posts: turns.append(posts) or True, sleep=sleep, max_cycles=2)
    assert [[p["seq"] for p in t] for t in turns] == [[1], [2]]
    assert sum(sleeps) == 10


def test_failed_turns_back_off_instead_of_retrying_at_once(tmp_path: Path) -> None:
    config = _config(tmp_path, AGENT_BUS_FAIL_BACKOFF_SECONDS="30")
    clock = Clock()
    pacer = bw.Pacer(1000, 30, clock=clock)
    door = FakeDoor([_post(1)])
    turns, sleeps = [], []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    bw.watch(config, once=False, call=door, turn=lambda posts: turns.append(posts) or False,
             sleep=sleep, pacer=pacer, max_cycles=6)
    # fail, wait 30, fail, wait 60, fail, wait 60 of 120
    assert len(turns) == 3
    assert sleeps == [30, 60, 60]
    assert bw.load_state(config)["seen"] == []  # failed turns never mark posts handled


def test_the_hourly_cap_holds_turns_back(tmp_path: Path) -> None:
    config = _config(tmp_path, AGENT_BUS_MAX_TURNS_PER_HOUR="2", AGENT_BUS_IDLE_BACKOFF_SECONDS="0")
    clock = Clock()
    pacer = bw.Pacer(config.max_turns_per_hour, 30, clock=clock)
    door = FakeDoor([])
    turns, sleeps = [], []
    counter = iter(range(1, 100))

    def call(name, arguments):
        if name == "wait_unread":
            door.posts.append(_post(next(counter)))
        return door(name, arguments)

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    bw.watch(config, once=False, call=call, turn=lambda posts: turns.append(posts) or True,
             sleep=sleep, pacer=pacer, max_cycles=5)
    assert len(turns) == 2
    assert sleeps and all(value == 60 for value in sleeps)


def test_empty_boards_setting_follows_the_hint(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.boards == []
    door = FakeDoor([_post(1, "alpha"), _post(2, "beta")])
    turns = []
    bw.watch(config, once=True, call=door, turn=lambda posts: turns.append(posts) or True, sleep=lambda s: None)
    assert sorted(p["board"] for p in turns[0]) == ["alpha", "beta"]


def test_non_bus_keys_in_watch_env_reach_the_turn_only(tmp_path: Path) -> None:
    state = tmp_path / "watch"
    state.mkdir()
    (state / "watch.env").write_text("AGENT_BUS_BOARDS=room\nEXTRA_FOR_TURN=1\n# comment\n")
    config = bw.Config(state, environ=dict(ENV))
    assert config.boards == ["room"]
    assert config.extra_env == {"EXTRA_FOR_TURN": "1"}


def test_run_once_waits_short_instead_of_a_full_hold(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[tuple[str, dict]] = []

    def call(name: str, arguments: dict) -> dict:
        calls.append((name, dict(arguments)))
        return {"status": "timeout"}

    assert bw.watch(config, once=True, call=call, turn=lambda posts: True, sleep=lambda s: None) == 0
    assert calls == [("wait_unread", {"timeout_seconds": bw.ONCE_WAIT_SECONDS})]


def test_the_run_once_wait_and_attempts_are_configurable_and_capped(tmp_path: Path) -> None:
    assert _config(tmp_path).once_wait == bw.ONCE_WAIT_SECONDS
    assert _config(tmp_path).once_attempts == bw.ONCE_MAX_ATTEMPTS
    assert _config(tmp_path, AGENT_BUS_ONCE_WAIT_SECONDS="7").once_wait == 7
    assert _config(tmp_path, AGENT_BUS_ONCE_WAIT_SECONDS="90").once_wait == bw.WAIT_SECONDS
    assert _config(tmp_path, AGENT_BUS_ONCE_MAX_ATTEMPTS="1").once_attempts == 1


def test_run_once_retries_a_door_held_by_a_stopped_watchers_open_wait(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls, sleeps = [], []

    def call(name: str, arguments: dict) -> dict:
        calls.append(name)
        if len(calls) == 1:
            raise urllib.error.URLError("The read operation timed out")
        return {"status": "timeout"}

    assert bw.watch(config, once=True, call=call, turn=lambda posts: True, sleep=sleeps.append) == 0
    assert calls == ["wait_unread", "wait_unread"]
    assert sleeps == [bw.ONCE_RETRY_SECONDS]


def test_run_once_retries_a_refused_wait_until_the_old_one_drains(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls = []

    def call(name: str, arguments: dict) -> dict:
        calls.append(name)
        # A refused wait answers with no structured status; door_call returns {}.
        return {} if len(calls) == 1 else {"status": "timeout"}

    assert bw.watch(config, once=True, call=call, turn=lambda posts: True, sleep=lambda s: None) == 0
    assert len(calls) == 2


def test_run_once_fails_only_after_the_last_attempt(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls, sleeps = [], []

    def call(name: str, arguments: dict) -> dict:
        calls.append(name)
        raise TimeoutError("The read operation timed out")

    assert bw.watch(config, once=True, call=call, turn=lambda posts: True, sleep=sleeps.append) == 1
    assert len(calls) == bw.ONCE_MAX_ATTEMPTS
    assert sleeps == [bw.ONCE_RETRY_SECONDS] * (bw.ONCE_MAX_ATTEMPTS - 1)
