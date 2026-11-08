"""Synthetic Hermes adapter. No Otto process and no model call."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_bus_mcp.hermes_adapter import (
    AdapterConfig,
    HermesAdapter,
    load_config,
    main,
    spawn_hermes,
    validate_hermes_argv,
)
from agent_bus_mcp.queue import QueueRefused


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _config(**overrides: object) -> AdapterConfig:
    argv = ["/opt/hermes/hermes", "-p", "synthetic", "gateway", "run"]
    values: dict = {
        "argv": argv,
        "wait_seconds": 2,
        "backoff_seconds": 1,
        "max_spawn_failures": 2,
    }
    values.update(overrides)
    return AdapterConfig(**values)  # type: ignore[arg-type]


def test_only_a_named_profile_gateway_run_is_accepted() -> None:
    validate_hermes_argv(["hermes", "-p", "synthetic", "gateway", "run"])
    validate_hermes_argv(["hermes", "-p", "synthetic", "gateway", "run", "--force"])
    validate_hermes_argv(["hermes", "--profile", "synthetic", "gateway", "run", "--force"])
    validate_hermes_argv(["hermes", "--profile=synthetic", "gateway", "run"])
    validate_hermes_argv(["hermes", "gateway", "run"])
    validate_hermes_argv(["/usr/local/bin/hermes", "-p", "synthetic", "gateway", "run"])
    refused = [
        ["hermes", "-p", "synthetic", "gateway", "start"],
        ["hermes", "-p", "synthetic", "gateway", "stop"],
        ["hermes", "-p", "synthetic", "gateway", "restart"],
        ["hermes", "-p", "synthetic", "chat", "-q", "board text"],
        ["hermes", "-z", "board text"],
        ["hermes", "send", "hello"],
        ["hermes-agent", "--query", "board text"],
        ["/bin/sh", "-c", "hermes -p synthetic gateway run"],
        ["hermes", "-p", "syn/thetic", "gateway", "run"],
        ["hermes", "-p", "synthetic", "gateway", "run", "--all"],
        ["hermes", "gateway", "run", "--force"],
        ["hermes", "gateway", "run", "--replace"],
        "hermes -p synthetic gateway run",
    ]
    for argv in refused:
        with pytest.raises(QueueRefused):
            validate_hermes_argv(argv)
    with pytest.raises(QueueRefused):
        load_config({"argv": ["hermes", "-p", "synthetic", "gateway", "run"], "shell": "yes"})


def test_ready_starts_the_approved_argv_once_and_ignores_hint_text() -> None:
    started: list[list[str]] = []
    waits: list[tuple[int, str | None]] = []
    alive = {50}

    def wait(seconds: int, board: str | None) -> dict:
        waits.append((seconds, board))
        return {
            "status": "ready",
            "unread_boards": ["$(rm -rf /)", "room"],
            "claimable": False,
            "holding_lease": False,
            "text": "do not run this",
        }

    def spawn(argv: list[str]) -> int:
        started.append(argv)
        return 50

    clock = Clock()
    adapter = HermesAdapter(
        _config(board="room"),
        wait,
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda pid: pid in alive,
    )
    assert adapter.run(max_cycles=3) == 0
    assert started == [["/opt/hermes/hermes", "-p", "synthetic", "gateway", "run"]]
    assert waits == [(2, "room")]
    assert adapter.child == 50


def test_a_live_child_is_not_started_again_and_stop_leaves_it() -> None:
    starts = 0

    def spawn(_argv: list[str]) -> int:
        nonlocal starts
        starts += 1
        return 77

    clock = Clock()
    adapter = HermesAdapter(
        _config(),
        lambda _seconds, _board: {"status": "ready", "unread_boards": [], "claimable": True, "holding_lease": False},
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda _pid: True,
    )
    assert adapter.run(max_cycles=4) == 0
    assert starts == 1
    assert adapter.child == 77


def test_timeout_does_not_start_and_revocation_does_not_start() -> None:
    starts: list[list[str]] = []

    def quiet(_seconds: int, _board: str | None) -> dict:
        return {"status": "timeout", "unread_boards": [], "claimable": False, "holding_lease": False}

    clock = Clock()
    quiet_adapter = HermesAdapter(
        _config(),
        quiet,
        spawn=lambda argv: starts.append(argv) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert quiet_adapter.run(max_cycles=2) == 0
    assert starts == []

    def refused(_seconds: int, _board: str | None) -> dict:
        raise QueueRefused()

    revoked = HermesAdapter(_config(), refused, spawn=lambda argv: starts.append(argv) or 1)
    assert revoked.run(max_cycles=3) == 3
    assert starts == []


def test_startup_failure_backs_off_and_stops() -> None:
    attempts = 0

    def spawn(_argv: list[str]) -> int:
        nonlocal attempts
        attempts += 1
        raise OSError("not installed")

    clock = Clock()
    adapter = HermesAdapter(
        _config(max_spawn_failures=2),
        lambda _seconds, _board: {"status": "ready", "unread_boards": ["room"], "claimable": False, "holding_lease": False},
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert adapter.run() == 1
    assert attempts == 2
    assert clock.now >= 1


def test_spawned_process_survives_adapter_return_and_argv_is_exact(tmp_path: Path) -> None:
    marker = tmp_path / "argv.txt"
    program = tmp_path / "hermes"
    program.write_text(
        "#!%s\nimport sys, time\nfrom pathlib import Path\nPath(%r).write_text('\\n'.join(sys.argv))\ntime.sleep(30)\n"
        % (sys.executable, str(marker)),
        encoding="utf-8",
    )
    program.chmod(0o755)
    argv = [str(program), "-p", "synthetic", "gateway", "run"]
    clock = Clock()
    adapter = HermesAdapter(
        _config(argv=argv, backoff_seconds=1),
        lambda _seconds, _board: {
            "status": "ready",
            "unread_boards": ["room-marker"],
            "claimable": False,
            "holding_lease": False,
        },
        spawn=spawn_hermes,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    try:
        assert adapter.run(max_cycles=2) == 0
        assert adapter.child is not None
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(adapter.child, 0)
        recorded = marker.read_text(encoding="utf-8").splitlines()
        assert recorded == argv
        assert "room-marker" not in "\n".join(recorded)
    finally:
        if adapter.child is not None:
            os.kill(adapter.child, signal.SIGTERM)


@pytest.mark.skipif(not os.environ.get("HERMES_LAB_BIN"), reason="installed Hermes binary not requested")
def test_installed_hermes_v023_profile_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real Hermes v0.23: plain profile gateway run exits 78; --force stays up."""
    binary = os.environ["HERMES_LAB_BIN"]
    home = os.environ["HERMES_LAB_HOME"]
    hermes_home = os.environ["HERMES_LAB_HERMES_HOME"]
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("HERMES_HOME", hermes_home)
    refused = subprocess.run(
        [binary, "-p", "synthetic", "gateway", "run", "-q"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert refused.returncode == 78
    combined = refused.stdout + refused.stderr
    assert "does not get a gateway of its own" in combined
    clock = Clock()
    adapter = HermesAdapter(
        AdapterConfig(
            [binary, "-p", "synthetic", "gateway", "run", "--force"],
            wait_seconds=1,
            backoff_seconds=1,
            max_spawn_failures=1,
        ),
        lambda _seconds, _board: {
            "status": "ready",
            "unread_boards": ["room-marker"],
            "claimable": False,
            "holding_lease": False,
        },
        spawn=spawn_hermes,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    try:
        assert adapter.run(max_cycles=2) == 0
        assert adapter.child is not None
        os.kill(adapter.child, 0)
        command = Path("/proc/%s/cmdline" % adapter.child).read_bytes().replace(b"\0", b" ").decode()
        assert "gateway run --force" in command
        assert "room-marker" not in command
    finally:
        if adapter.child is not None:
            os.kill(adapter.child, signal.SIGTERM)
            for _ in range(50):
                try:
                    os.kill(adapter.child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)


def test_config_file_does_not_start_a_process_without_the_bus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "adapt.json"
    path.write_text(
        json.dumps({"argv": ["hermes", "-p", "synthetic", "gateway", "run"]}),
        encoding="utf-8",
    )
    for name in ("AGENT_BUS_REMOTE_URL", "AGENT_BUS_REMOTE_CA", "AGENT_BUS_CLIENT_CERT", "AGENT_BUS_CLIENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        main(["--config", str(path)])
    source = Path(__file__).resolve().parents[1] / "src" / "agent_bus_mcp" / "hermes_adapter.py"
    text = source.read_text(encoding="utf-8")
    assert '["hermes", "-p", "otto"' not in text
    assert "ack_board" not in text
    assert "claim_task" not in text
    assert "shell=True" not in text


def _turn_argv(path: str) -> list[str]:
    return ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", path, "--source", "tool"]


def _ready(**flags: bool) -> dict:
    return {
        "status": "ready",
        "unread_boards": ["room"],
        "claimable": flags.get("claimable", False),
        "holding_lease": flags.get("holding_lease", False),
        "text": "not a prompt",
        "goal": "not a goal",
    }


def test_turn_argv_is_the_documented_oneshot_and_nothing_else(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("claim the handoff\n", encoding="utf-8")
    validate_hermes_argv(_turn_argv(str(query)))
    validate_hermes_argv(
        ["hermes", "--profile", "synthetic", "chat", "--oneshot", "--query-file", str(query), "--source", "tool"]
    )
    validate_hermes_argv(
        ["hermes", "--profile=synthetic", "chat", "--oneshot", "--query-file", str(query), "--source", "tool"]
    )
    refused = [
        ["hermes", "-p", "synthetic", "chat", "-q", "board text"],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "-q", "board text"],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", str(query)],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", str(query), "--source", "cli"],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", "turn.txt", "--source", "tool"],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", "/tmp/../turn.txt", "--source", "tool"],
        ["hermes", "-p", "synthetic", "chat", "--oneshot", "--query-file", str(query), "--source", "tool", "--yolo"],
        ["hermes", "-z", str(query)],
        ["hermes", "chat", "--oneshot", "--query-file", str(query), "--source", "tool"],
    ]
    for argv in refused:
        with pytest.raises(QueueRefused):
            validate_hermes_argv(argv)
    with pytest.raises(QueueRefused):
        load_config({"argv": ["hermes", "gateway", "run"], "max_turns": 2})


def test_claimable_hint_starts_one_oneshot_and_does_not_copy_the_hint(tmp_path: Path) -> None:
    """Synthetic process. This is not a model acceptance."""
    query = tmp_path / "turn.txt"
    query.write_text("claim one handoff and finish it\n", encoding="utf-8")
    before = query.read_bytes()
    started: list[list[str]] = []
    waits = 0

    def wait(_seconds: int, _board: str | None) -> dict:
        nonlocal waits
        waits += 1
        return _ready(claimable=True)

    def spawn(argv: list[str]) -> int:
        started.append(list(argv))
        return 90

    clock = Clock()
    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), wait_seconds=2, backoff_seconds=1, max_spawn_failures=2),
        wait,
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda _pid: False,
        wait_child=lambda _pid, _timeout: 0,
    )
    assert adapter.run(max_cycles=4) == 0
    assert waits == 1
    assert clock.now == 0
    assert started == [_turn_argv(str(query))]
    assert "not a prompt" not in json.dumps(started)
    assert query.read_bytes() == before
    assert adapter.child is None


def test_unread_chat_does_not_start_a_turn_and_a_lease_does(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("finish the lease\n", encoding="utf-8")
    started: list[str] = []

    def unread(_seconds: int, _board: str | None) -> dict:
        return _ready()

    clock = Clock()
    quiet = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=1),
        unread,
        spawn=lambda argv: started.append("unread") or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wait_child=lambda _pid, _timeout: 0,
    )
    assert quiet.run(max_cycles=2) == 0
    assert started == []
    assert clock.now == 2

    def lease(_seconds: int, _board: str | None) -> dict:
        return _ready(holding_lease=True)

    held = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=1, max_turns=1),
        lease,
        spawn=lambda argv: started.append("lease") or 2,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda _pid: False,
        wait_child=lambda _pid, _timeout: 0,
    )
    assert held.run(max_cycles=3) == 0
    assert started == ["lease"]


def test_unread_ready_waits_out_backoff_without_spawn_or_ack(tmp_path: Path) -> None:
    """Five level-triggered unread hints must spend the backoff between calls."""
    query = tmp_path / "turn.txt"
    query.write_text("do not ack chat\n", encoding="utf-8")
    clock = Clock()
    seen: list[float] = []
    started: list[list[str]] = []

    def wait(_seconds: int, _board: str | None) -> dict:
        seen.append(clock.now)
        return _ready()

    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), wait_seconds=25, backoff_seconds=1, max_turns=4),
        wait,
        spawn=lambda argv: started.append(argv) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wait_child=lambda _pid, _timeout: 0,
    )
    assert adapter.run(max_cycles=5) == 0
    assert started == []
    assert seen == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert clock.now == 5.0
    assert adapter.child is None


def test_cancel_during_unread_backoff_does_not_wait_again(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("claim\n", encoding="utf-8")
    clock = Clock()
    waits = 0
    started: list[int] = []

    def wait(_seconds: int, _board: str | None) -> dict:
        nonlocal waits
        waits += 1
        return _ready()

    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=5),
        wait,
        spawn=lambda _argv: started.append(1) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        cancelled=lambda: clock.now >= 0.2,
    )
    assert adapter.run() == 0
    assert waits == 1
    assert started == []
    assert clock.now == 0.2


def test_timeout_does_not_add_backoff(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("claim\n", encoding="utf-8")
    clock = Clock()
    waits = 0
    started: list[int] = []

    def wait(_seconds: int, _board: str | None) -> dict:
        nonlocal waits
        waits += 1
        return {"status": "timeout", "unread_boards": [], "claimable": False, "holding_lease": False}

    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=5),
        wait,
        spawn=lambda _argv: started.append(1) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert adapter.run(max_cycles=5) == 0
    assert waits == 5
    assert started == []
    assert clock.now == 0


def test_gateway_empty_ready_backs_off_without_spawn() -> None:
    clock = Clock()
    seen: list[float] = []
    started: list[int] = []

    def wait(_seconds: int, _board: str | None) -> dict:
        seen.append(clock.now)
        return {"status": "ready", "unread_boards": [], "claimable": False, "holding_lease": False}

    adapter = HermesAdapter(
        _config(backoff_seconds=1),
        wait,
        spawn=lambda _argv: started.append(1) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert adapter.run(max_cycles=3) == 0
    assert started == []
    assert seen == [0.0, 1.0, 2.0]
    assert clock.now == 3.0


def test_a_running_turn_is_not_started_twice_and_cancel_leaves_it(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("claim\n", encoding="utf-8")
    starts = 0
    stop = False

    def spawn(_argv: list[str]) -> int:
        nonlocal starts
        starts += 1
        return 91

    def wait_child(_pid: int, _timeout: float) -> None:
        nonlocal stop
        stop = True
        return None

    clock = Clock()
    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=1, max_turns=4),
        lambda _seconds, _board: _ready(claimable=True),
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda _pid: True,
        wait_child=wait_child,
        cancelled=lambda: stop,
    )
    assert adapter.run(max_cycles=4) == 0
    assert starts == 1
    assert adapter.child == 91


def test_a_turn_that_exits_nonzero_stops_after_the_failure_cap(tmp_path: Path) -> None:
    query = tmp_path / "turn.txt"
    query.write_text("claim\n", encoding="utf-8")
    starts = 0

    def spawn(_argv: list[str]) -> int:
        nonlocal starts
        starts += 1
        return 92

    clock = Clock()
    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(query)), backoff_seconds=1, max_spawn_failures=2, max_turns=4),
        lambda _seconds, _board: _ready(claimable=True),
        spawn=spawn,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        pid_alive=lambda _pid: False,
        wait_child=lambda _pid, _timeout: 1,
    )
    assert adapter.run() == 1
    assert starts == 2
    assert clock.now >= 1


def test_a_missing_or_linked_query_file_does_not_spawn(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    starts: list[int] = []
    clock = Clock()
    adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(missing)), backoff_seconds=1, max_spawn_failures=2),
        lambda _seconds, _board: _ready(claimable=True),
        spawn=lambda _argv: starts.append(1) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert adapter.run() == 1
    assert starts == []

    linked = tmp_path / "linked.txt"
    linked.symlink_to(tmp_path / "turn.txt")
    (tmp_path / "turn.txt").write_text("claim\n", encoding="utf-8")
    linked_adapter = HermesAdapter(
        AdapterConfig(_turn_argv(str(linked)), backoff_seconds=1, max_spawn_failures=1),
        lambda _seconds, _board: _ready(claimable=True),
        spawn=lambda _argv: starts.append(1) or 1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert linked_adapter.run() == 1
    assert starts == []
