"""Local waiter that may start one operator-approved Hermes process.

This process is not the bus host. It calls ``wait_available`` while the Hermes
process it started is stopped. It does not wake a sleeping computer, dial out,
or send a message. Official Hermes distinguishes three different commands:

- ``hermes gateway run`` is a foreground gateway. It does not by itself run an
  agent turn. Hermes v0.23 keeps one host gateway. A named profile's
  ``gateway run`` exits 78 unless ``--force`` is present.
- ``hermes chat --oneshot --query-file PATH`` is one foreground agent turn. It
  reads the prompt from a file, answers, and exits. ``--source tool`` marks
  that turn as a third-party integration. ``-z`` returns only a final answer
  and is not this turn. ``-q`` puts the prompt on the command line and is refused.
- ``gateway start`` launches a service manager. This adapter refuses it.

https://hermes-agent.nousresearch.com/docs/reference/cli-commands
https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways

The query file is the operator's. A hint is not copied into it. Otto is not
named here.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .queue import QueueRefused

_PROFILE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_PATH = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/._-")


class AdapterConfig:
    """Operator file. The argv is fixed before any hint is read."""

    def __init__(
        self,
        argv: list[str],
        *,
        wait_seconds: int = 25,
        backoff_seconds: float = 5,
        max_spawn_failures: int = 3,
        board: str | None = None,
        turn_timeout_seconds: int = 120,
        max_turns: int = 1,
    ) -> None:
        validate_hermes_argv(argv)
        if type(wait_seconds) is not int or not 1 <= wait_seconds <= 25:
            raise QueueRefused()
        if type(backoff_seconds) not in (int, float) or isinstance(backoff_seconds, bool):
            raise QueueRefused()
        if not 1 <= float(backoff_seconds) <= 60:
            raise QueueRefused()
        if type(max_spawn_failures) is not int or not 1 <= max_spawn_failures <= 5:
            raise QueueRefused()
        if board is not None and not _token(board):
            raise QueueRefused()
        if type(turn_timeout_seconds) is not int or isinstance(turn_timeout_seconds, bool):
            raise QueueRefused()
        if not 1 <= turn_timeout_seconds <= 300:
            raise QueueRefused()
        if type(max_turns) is not int or isinstance(max_turns, bool) or not 1 <= max_turns <= 8:
            raise QueueRefused()
        self.argv = list(argv)
        self.wait_seconds = wait_seconds
        self.backoff_seconds = float(backoff_seconds)
        self.max_spawn_failures = max_spawn_failures
        self.board = board
        self.turn_timeout_seconds = turn_timeout_seconds
        self.max_turns = max_turns


def classify_hermes_argv(argv: object) -> str:
    """Return ``gateway`` or ``turn`` for one closed argv. Refuse every other form.

    A profile token is the operator's. ``--force`` is allowed only on a profile
    gateway run, because Hermes v0.23 exits 78 without it. The turn form is the
    documented one-shot that reads a file. Bus text is never appended.
    """
    if type(argv) is not list or not argv or not all(_arg(item) for item in argv):
        raise QueueRefused()
    if not _hermes_program(argv[0]):
        raise QueueRefused()
    rest = argv[1:]
    profile: str | None
    if len(rest) >= 2 and rest[0] in {"-p", "--profile"}:
        profile = rest[1]
        tail = rest[2:]
    elif len(rest) >= 1 and rest[0].startswith("--profile="):
        profile = rest[0].split("=", 1)[1]
        tail = rest[1:]
    else:
        profile = None
        tail = rest
    if profile is None:
        if tail != ["gateway", "run"]:
            raise QueueRefused()
        return "gateway"
    if not _token(profile):
        raise QueueRefused()
    if tail in (["gateway", "run"], ["gateway", "run", "--force"]):
        return "gateway"
    if (
        len(tail) == 6
        and tail[0] == "chat"
        and tail[1] == "--oneshot"
        and tail[2] == "--query-file"
        and _query_path(tail[3])
        and tail[4] == "--source"
        and tail[5] == "tool"
    ):
        return "turn"
    raise QueueRefused()


def validate_hermes_argv(argv: object) -> None:
    classify_hermes_argv(argv)


def load_config(document: object) -> AdapterConfig:
    if type(document) is not dict or "argv" not in document:
        raise QueueRefused()
    allowed = {
        "argv",
        "wait_seconds",
        "backoff_seconds",
        "max_spawn_failures",
        "board",
        "turn_timeout_seconds",
        "max_turns",
    }
    if not set(document) <= allowed:
        raise QueueRefused()
    kind = classify_hermes_argv(document["argv"])
    if kind == "gateway" and ({"turn_timeout_seconds", "max_turns"} & set(document)):
        raise QueueRefused()
    return AdapterConfig(
        document["argv"],
        wait_seconds=document.get("wait_seconds", 25),
        backoff_seconds=document.get("backoff_seconds", 5),
        max_spawn_failures=document.get("max_spawn_failures", 3),
        board=document.get("board"),
        turn_timeout_seconds=document.get("turn_timeout_seconds", 120),
        max_turns=document.get("max_turns", 1),
    )


def spawn_hermes(argv: list[str]) -> int:
    """Start the approved argv. The child is not signalled when this process exits."""
    validate_hermes_argv(argv)
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        shell=False,
    )
    return process.pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class HermesAdapter:
    """One waiter. It does not acknowledge, claim, or signal a process it did not start.

    While the child it started is alive, it does not call ``wait_available``, so
    it does not hold that endpoint's wait slot beside that process. Stopping the
    adapter does not kill the child. A turn argv is one foreground ``chat``
    process, not a gateway that stays up.
    """

    def __init__(
        self,
        config: AdapterConfig,
        wait: Callable[[int, str | None], dict],
        *,
        spawn: Callable[[list[str]], int] = spawn_hermes,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        pid_alive: Callable[[int], bool] = _pid_alive,
        wait_child: Callable[[int, float], int | None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.kind = classify_hermes_argv(config.argv)
        self.query_path = _turn_query_path(config.argv) if self.kind == "turn" else ""
        self.wait = wait
        self.spawn = spawn
        self.sleep = sleep
        self.monotonic = monotonic
        self.pid_alive = pid_alive
        self.wait_child = wait_child or self._wait_child
        self.cancelled = cancelled or (lambda: False)
        self.child: int | None = None

    def run(self, *, max_cycles: int | None = None) -> int:
        failures = 0
        cycles = 0
        turns = 0
        while not self.cancelled():
            if max_cycles is not None and cycles >= max_cycles:
                return 0
            cycles += 1
            if self.child is not None and self.pid_alive(self.child):
                if self.kind != "turn":
                    self._backoff()
                    continue
                failures, turns, stop = self._finish_turn(failures, turns)
                if stop is not None:
                    return stop
                continue
            self.child = None
            try:
                hint = self.wait(self.config.wait_seconds, self.config.board)
            except QueueRefused:
                return 3
            if self.kind == "turn":
                if not _handoff_ready(hint):
                    if _status_ready(hint):
                        self._backoff()
                    continue
                if not _query_file_ready(self.query_path):
                    failures += 1
                    if failures >= self.config.max_spawn_failures:
                        return 1
                    self._backoff()
                    continue
            elif not _actionable(hint):
                if _status_ready(hint):
                    self._backoff()
                continue
            try:
                self.child = self.spawn(list(self.config.argv))
            except OSError:
                self.child = None
                failures += 1
                if failures >= self.config.max_spawn_failures:
                    return 1
                self._backoff()
                continue
            if self.kind != "turn":
                self._backoff()
                if self.cancelled():
                    return 0
                if self.child is None or not self.pid_alive(self.child):
                    self.child = None
                    failures += 1
                    if failures >= self.config.max_spawn_failures:
                        return 1
                else:
                    failures = 0
                continue
            failures, turns, stop = self._finish_turn(failures, turns)
            if stop is not None:
                return stop
        return 0

    def _finish_turn(self, failures: int, turns: int) -> tuple[int, int, int | None]:
        if self.child is None:
            return failures, turns, None
        code = self.wait_child(self.child, float(self.config.turn_timeout_seconds))
        if code is None and self.pid_alive(self.child):
            self._backoff()
            return failures, turns, None
        self.child = None
        if code == 0:
            turns += 1
            failures = 0
            if turns >= self.config.max_turns:
                return failures, turns, 0
            self._backoff()
            return failures, turns, None
        failures += 1
        if failures >= self.config.max_spawn_failures:
            return failures, turns, 1
        self._backoff()
        return failures, turns, None

    def _wait_child(self, pid: int, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while not self.cancelled() and time.monotonic() < deadline:
            try:
                waited, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return None
            if waited == pid:
                return os.waitstatus_to_exitcode(status)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        return None

    def _backoff(self) -> None:
        deadline = self.monotonic() + self.config.backoff_seconds
        while not self.cancelled() and self.monotonic() < deadline:
            self.sleep(min(0.2, deadline - self.monotonic()))


def _status_ready(hint: object) -> bool:
    return type(hint) is dict and hint.get("status") == "ready"


def _hint_shape(hint: object) -> bool:
    if type(hint) is not dict or hint.get("status") != "ready":
        return False
    boards = hint.get("unread_boards")
    if type(boards) is not list or not all(type(item) is str for item in boards):
        return False
    return type(hint.get("claimable")) is bool and type(hint.get("holding_lease")) is bool


def _actionable(hint: object) -> bool:
    """A gateway starts only when the hint says ready and names a real condition.

    Board names and any other fields are ignored. They are not a command.
    """
    if not _hint_shape(hint):
        return False
    assert type(hint) is dict
    return bool(hint["unread_boards"] or hint["claimable"] or hint["holding_lease"])


def _handoff_ready(hint: object) -> bool:
    """A turn starts only for a claimable handoff or a lease this endpoint holds.

    Unread chat is not a handoff. The hint still carries no post text.
    """
    if not _hint_shape(hint):
        return False
    assert type(hint) is dict
    return bool(hint["claimable"] or hint["holding_lease"])


def _arg(value: object) -> bool:
    return type(value) is str and bool(value) and "\x00" not in value and "\n" not in value and "\r" not in value


def _token(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 64 and all(char in _PROFILE for char in value)


def _hermes_program(value: str) -> bool:
    if value == "hermes":
        return True
    path = Path(value)
    return path.is_absolute() and path.name == "hermes" and ".." not in path.parts


def _query_path(value: str) -> bool:
    if not value.startswith("/") or len(value) > 4096 or not all(char in _PATH for char in value):
        return False
    parts = value.split("/")
    return ".." not in parts and "" not in parts[1:]


def _turn_query_path(argv: list[str]) -> str:
    return argv[argv.index("--query-file") + 1]


def _query_file_ready(path: str) -> bool:
    candidate = Path(path)
    if candidate.is_symlink():
        return False
    return candidate.is_file()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wait as one endpoint and start the operator's Hermes gateway run, "
            "or one documented chat oneshot from a query file. Does not stop that process."
        ),
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, QueueRefused):
        print("request refused", file=sys.stderr)
        return 2
    required = ("AGENT_BUS_REMOTE_URL", "AGENT_BUS_REMOTE_CA", "AGENT_BUS_CLIENT_CERT", "AGENT_BUS_CLIENT_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("required environment is missing: " + ", ".join(missing))
    if not os.environ["AGENT_BUS_REMOTE_URL"].startswith("https://"):
        raise SystemExit("remote url must be https")
    stop = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    from .remote import call_remote

    def wait(seconds: int, board: str | None) -> dict:
        arguments: dict = {"timeout_seconds": seconds}
        if board is not None:
            arguments["board"] = board
        return call_remote(
            url=os.environ["AGENT_BUS_REMOTE_URL"],
            ca=Path(os.environ["AGENT_BUS_REMOTE_CA"]),
            certificate=Path(os.environ["AGENT_BUS_CLIENT_CERT"]),
            key=Path(os.environ["AGENT_BUS_CLIENT_KEY"]),
            tool="wait_available",
            arguments=arguments,
            request_id="hermes-adapt-%s" % time.monotonic_ns(),
            timeout=float(min(30, seconds + 5)),
        )

    return HermesAdapter(config, wait, cancelled=lambda: stop).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
