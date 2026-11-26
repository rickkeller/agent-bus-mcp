#!/usr/bin/env python3
"""One always-on board watcher next to a coding agent.

Loop `wait_unread` on one graph door. On a ready hint, read those boards,
skip anything already handled, and start exactly one agent turn with a
notice file. The turn command comes from the operator's config; bus text
never becomes the prompt itself. This never answers a live offer, never
subscribes to NATS, and changes nothing on the server or network.

Pacing keeps a stuck or failing turn from burning tokens:
- a ready hint with nothing new (posts handled but not yet acked, or unread
  on boards this watcher does not handle) waits AGENT_BUS_IDLE_BACKOFF_SECONDS,
  rechecking for a genuinely new post every few seconds, instead of spinning;
- a failed turn is retried after 30 s, then 60 s, 120 s, ... capped at 10 min;
- at most AGENT_BUS_MAX_TURNS_PER_HOUR turns start in any rolling hour.

The run-once check is short and bounded: one `wait_unread` of
AGENT_BUS_ONCE_WAIT_SECONDS (default 2) instead of a full hold, retried
up to AGENT_BUS_ONCE_MAX_ATTEMPTS (default 3) times with a backoff. A
watcher that was just stopped leaves its open wait holding the door for
up to WAIT_SECONDS — disconnect does not interrupt it — so one slow or
failed call is not a verdict.
"""

from __future__ import annotations

import collections
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WAIT_SECONDS = 25
ONCE_WAIT_SECONDS = 2
ONCE_MAX_ATTEMPTS = 3
ONCE_RETRY_SECONDS = 5
TURN_TIMEOUT_SECONDS = 900
SEEN_CAP = 500
IDLE_BACKOFF_SECONDS = 60
IDLE_RECHECK_SECONDS = 5
MAX_TURNS_PER_HOUR = 12
FAIL_BACKOFF_SECONDS = 30
FAIL_BACKOFF_CAP_SECONDS = 600
DEFAULT_PROMPT = (
    "A board watcher noticed unread board posts. Read {notice} and act on them: "
    "poll the board, answer what needs answering, then ack the handled posts. "
    "Keep the reply short."
)


class Stop(Exception):
    pass


def die(message: str) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _number(raw: str | None, default: float, minimum: float = 0) -> float:
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError:
        return default
    return value if value >= minimum else default


def default_turn_command(tools: str = "") -> list[str]:
    """An argv list, so the prompt is always one argument."""
    command = ["claude", "-p", DEFAULT_PROMPT]
    if tools:
        command += ["--allowedTools", tools]
    return command


def build_turn_argv(turn_command: str | list[str], notice_path: Path) -> list[str]:
    """Split a configured command with shell quoting and fill in {notice}.

    Quote the prompt in AGENT_BUS_TURN_COMMAND so it stays one argument:
    claude -p "Read {notice} and act on it" --allowedTools "mcp__bus__*"
    """
    parts = shlex.split(turn_command) if isinstance(turn_command, str) else list(turn_command)
    if not parts:
        raise ValueError("turn command is empty")
    return [part.replace("{notice}", str(notice_path)) for part in parts]


class Config:
    """One state directory with a watch.env file and process env on top."""

    def __init__(self, state_dir: Path, environ: dict | None = None) -> None:
        self.state = state_dir
        self.state.mkdir(parents=True, exist_ok=True)
        values = dict(os.environ if environ is None else environ)
        env_file = self.state / "watch.env"
        env_file_items = []
        for line in env_file.read_text().splitlines() if env_file.exists() else []:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_file_items.append((key.strip(), value.strip()))
                values.setdefault(key.strip(), value.strip())
        self.door_url = values.get("AGENT_BUS_DOOR_URL", "").rstrip("/")
        self.bearer = values.get("AGENT_BUS_BEARER", "")
        # Empty means: handle whichever boards the hint names.
        self.boards = [b.strip() for b in values.get("AGENT_BUS_BOARDS", "").split(",") if b.strip()]
        configured = values.get("AGENT_BUS_TURN_COMMAND", "")
        self.turn_command: str | list[str] = configured or default_turn_command(values.get("AGENT_BUS_TURN_TOOLS", ""))
        self.turn_cwd = Path(values.get("AGENT_BUS_TURN_CWD", str(Path.home()))).expanduser()
        self.turn_timeout = _number(values.get("AGENT_BUS_TURN_TIMEOUT_SECONDS"), TURN_TIMEOUT_SECONDS, 1)
        self.idle_backoff = _number(values.get("AGENT_BUS_IDLE_BACKOFF_SECONDS"), IDLE_BACKOFF_SECONDS)
        self.max_turns_per_hour = int(_number(values.get("AGENT_BUS_MAX_TURNS_PER_HOUR"), MAX_TURNS_PER_HOUR, 1))
        self.fail_backoff = _number(values.get("AGENT_BUS_FAIL_BACKOFF_SECONDS"), FAIL_BACKOFF_SECONDS, 1)
        # The run-once check: one short wait, a few bounded attempts, so a
        # door held by a just-stopped watcher's open wait is waited out.
        self.once_wait = int(min(WAIT_SECONDS, _number(values.get("AGENT_BUS_ONCE_WAIT_SECONDS"), ONCE_WAIT_SECONDS, 1)))
        self.once_attempts = max(1, int(_number(values.get("AGENT_BUS_ONCE_MAX_ATTEMPTS"), ONCE_MAX_ATTEMPTS, 1)))
        self.log_path = self.state / "listener.log"
        # Keys in watch.env that are not AGENT_BUS_* are handed to the turn
        # process, so the operator can add what the turn needs.
        self.extra_env = {key: value for key, value in env_file_items if not key.startswith("AGENT_BUS_")}
        if not self.door_url or not self.bearer:
            die(
                f"Set AGENT_BUS_DOOR_URL and AGENT_BUS_BEARER in "
                f"{env_file} or the environment. Copy the door URL and bearer "
                "from the running MCP client config; never commit them."
            )
        try:
            build_turn_argv(self.turn_command, self.state / "notice.json")
        except ValueError as error:
            die(f"AGENT_BUS_TURN_COMMAND cannot be parsed: {error}")

    def log(self, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        line = f"{stamp} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)


class Pacer:
    """Failure backoff and a rolling per-hour turn cap. Clock is injectable."""

    def __init__(self, max_turns_per_hour: int, fail_backoff: float,
                 fail_cap: float = FAIL_BACKOFF_CAP_SECONDS, clock=time.monotonic) -> None:
        self.max_turns = max_turns_per_hour
        self.fail_backoff = fail_backoff
        self.fail_cap = fail_cap
        self.clock = clock
        self.starts: collections.deque[float] = collections.deque()
        self.failures = 0
        self.not_before = 0.0

    def wait_seconds(self) -> float:
        """Seconds until a turn may start. Zero means start now."""
        now = self.clock()
        while self.starts and now - self.starts[0] >= 3600:
            self.starts.popleft()
        wait = max(0.0, self.not_before - now)
        if len(self.starts) >= self.max_turns:
            wait = max(wait, self.starts[0] + 3600 - now)
        return wait

    def record_turn(self, ok: bool) -> None:
        now = self.clock()
        self.starts.append(now)
        if ok:
            self.failures = 0
            self.not_before = 0.0
        else:
            self.failures += 1
            delay = min(self.fail_backoff * 2 ** (self.failures - 1), self.fail_cap)
            self.not_before = now + delay


def door_call(config: Config, name: str, arguments: dict) -> dict:
    url = config.door_url
    if not url.endswith("/mcp"):
        url += "/mcp"
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        body = json.loads(response.read())
    return body.get("result", {}).get("structuredContent") or {}


def load_state(config: Config) -> dict:
    path = config.state / "watched.json"
    if not path.exists():
        return {"seen": []}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("seen"), list) else {"seen": []}
    except (json.JSONDecodeError, OSError):
        return {"seen": []}


def save_state(config: Config, seen: list[str]) -> None:
    path = config.state / "watched.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seen": seen[-SEEN_CAP:]}), encoding="utf-8")
    tmp.replace(path)


def start_turn(config: Config, posts: list[dict]) -> bool:
    notice_path = config.state / f"notice-{int(time.time())}.json"
    notice_path.write_text(
        json.dumps(
            {"posts": [{key: post.get(key) for key in ("post_id", "board", "author", "seq", "text")} for post in posts]},
            indent=2,
        ),
        encoding="utf-8",
    )
    command = build_turn_argv(config.turn_command, notice_path)
    child_env = dict(os.environ)
    child_env.update(config.extra_env)
    config.log(f"starting turn: {command[0]} ... (notice {notice_path.name}, {len(posts)} post(s))")
    try:
        finished = subprocess.run(command, cwd=config.turn_cwd, check=False, env=child_env, timeout=config.turn_timeout)
    except subprocess.TimeoutExpired:
        config.log(f"turn killed after {config.turn_timeout:g}s; posts stay unread for the next cycle")
        return False
    except OSError as error:
        config.log(f"turn could not start: {error}")
        return False
    config.log(f"turn finished (exit {finished.returncode})")
    return finished.returncode == 0


def watch(config: Config, *, once: bool, call=None, turn=None, sleep=time.sleep,
          pacer: Pacer | None = None, max_cycles: int | None = None) -> int:
    """The main loop. call/turn/sleep/pacer/max_cycles exist for tests."""
    call = call or (lambda name, arguments: door_call(config, name, arguments))
    turn = turn or (lambda posts: start_turn(config, posts))
    pacer = pacer or Pacer(config.max_turns_per_hour, config.fail_backoff)
    seen: list[str] = list(load_state(config)["seen"])
    config.log(f"watching {', '.join(config.boards) or 'every board the hint names'} via {config.door_url}")
    cycles = 0
    attempts = 0

    def once_failed(reason: str) -> bool:
        """True when the run-once check is out of attempts.

        A watcher stopped a moment ago leaves its open wait holding the
        door for up to WAIT_SECONDS — disconnect does not interrupt it —
        so a slow or failed call is retried, not judged.
        """
        nonlocal attempts
        attempts += 1
        if attempts < config.once_attempts:
            config.log(
                f"run-once: {reason}; attempt {attempts} of {config.once_attempts}, "
                f"retrying in {ONCE_RETRY_SECONDS:g}s (a stopped watcher's open "
                f"wait can hold the door up to {WAIT_SECONDS}s)"
            )
            sleep(ONCE_RETRY_SECONDS)
            return False
        config.log(f"run-once check failed after {attempts} attempt(s): {reason}")
        return True

    def fresh_posts(boards: list[str]) -> list[dict]:
        return [
            post
            for board in boards
            for post in list(call("poll_unread", {"board": board}).get("posts") or [])
            if post.get("post_id") and post["post_id"] not in seen
        ]

    while True:
        cycles += 1
        if max_cycles is not None and cycles > max_cycles:
            return 0
        try:
            hint = call("wait_unread", {"timeout_seconds": config.once_wait if once else WAIT_SECONDS})
            status = hint.get("status")
            if status not in ("ready", "timeout"):
                if once:
                    if once_failed(f"unexpected hint status {status!r}"):
                        return 1
                    continue
                config.log(f"unexpected hint status {status!r}; retrying in 5s")
                sleep(5)
                continue
            boards = config.boards or [b for b in hint.get("unread_boards") or [] if isinstance(b, str)]
            fresh = fresh_posts(boards) if status == "ready" and hint.get("unread_boards") else []
            if status == "ready" and hint.get("unread_boards") and not fresh:
                # Level-triggered hint with nothing new: wait instead of spinning,
                # but recheck often so a genuinely new post is not delayed long.
                config.log(f"unread but nothing new; idle backoff {config.idle_backoff:g}s")
                waited = 0.0
                while waited < config.idle_backoff and not fresh:
                    step = min(IDLE_RECHECK_SECONDS, config.idle_backoff - waited)
                    sleep(step)
                    waited += step
                    fresh = fresh_posts(boards)
                if not fresh:
                    if once:
                        return 0
                    continue
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            if once:
                if once_failed(f"door call failed: {error}"):
                    return 1
                continue
            config.log(f"door call failed, retrying in 5s: {error}")
            sleep(5)
            continue
        if fresh:
            wait = pacer.wait_seconds()
            if wait > 0:
                config.log(
                    f"{len(fresh)} new post(s) wait {wait:.0f}s "
                    f"(failure backoff or {config.max_turns_per_hour}/hour cap); they stay unread"
                )
                if once:
                    return 0
                sleep(min(wait, 60))
                continue
            turn_lock = config.state / "turn.lock"
            with turn_lock.open("w") as lock_handle:
                try:
                    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    config.log(f"turn already running; {len(fresh)} post(s) stay unread for the next cycle")
                else:
                    ok = turn(fresh)
                    pacer.record_turn(ok)
                    if ok:
                        seen.extend(post["post_id"] for post in fresh)
                        save_state(config, seen)
                    else:
                        config.log(f"turn failed; next try in {pacer.wait_seconds():.0f}s")
        if once:
            return 0


def stop_daemon(config: Config) -> int:
    pid_path = config.state / "listener.pid"
    if not pid_path.exists():
        die(f"no pidfile at {pid_path}; is it running?")
    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"pid {pid} is already gone; removing the pidfile")
        pid_path.unlink()
        return 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"stopped {pid}")
            pid_path.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    print(f"pid {pid} did not exit in 10 s; send SIGKILL yourself", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("run", "stop", "status", "run-once"):
        die(__doc__ + "\nUsage: board-watch.py run|run-once|stop|status [--state-dir DIR]")
    command, *rest = args
    state_dir = Path(os.environ.get("AGENT_BUS_WATCH_DIR", "") or Path.home() / ".agent-bus-watch").expanduser()
    if "--state-dir" in rest:
        state_dir = Path(rest[rest.index("--state-dir") + 1]).expanduser()
    config = Config(state_dir)
    pid_path = config.state / "listener.pid"
    if command == "stop":
        return stop_daemon(config)
    if command == "status":
        if pid_path.exists() and _alive(pid_path):
            print(f"running (pid {pid_path.read_text().strip()}); state {config.state}; log {config.log_path}")
        else:
            print(f"not running; state {config.state}; log {config.log_path}")
        return 0
    if command == "run-once":
        return watch(config, once=True)
    pid_path.write_text(str(os.getpid()) + "\n")

    def goodbye(_signal, _frame):
        pid_path.unlink(missing_ok=True)
        raise Stop

    signal.signal(signal.SIGTERM, goodbye)
    signal.signal(signal.SIGINT, goodbye)
    try:
        code = watch(config, once=False)
    except Stop:
        code = 0
    return code


def _alive(pid_path: Path) -> bool:
    try:
        os.kill(int(pid_path.read_text().strip()), 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


if __name__ == "__main__":
    sys.exit(main())
