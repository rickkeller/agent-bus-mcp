"""Write one home directory and check it. The server still reads the same variables."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .endpoints import QueueRefused, load_config
from .mcp import _ALWAYS_ENV, _GRAPH_ENV, _LEGACY_ENV, identity_mode, missing_env

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PLACEHOLDER = "REPLACE_WITH"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def home_path() -> Path:
    """The directory that holds env. A new install uses ~/.agent-bus.

    AGENT_BUS_HOME wins. An env file setup already wrote wins over a file
    someone created by hand under ~/.config, so a later setup does not start
    reading the older file.
    """
    override = os.environ.get("AGENT_BUS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    preferred = Path.home() / ".agent-bus"
    if (preferred / "env").is_file():
        return preferred
    legacy = Path.home() / ".config" / "agent-bus-mcp"
    if (legacy / "env").is_file():
        return legacy
    return preferred


def parse_env(text: str) -> dict[str, str]:
    """Read KEY=VALUE lines. Comments and blank lines are ignored."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def expand_value(value: str) -> str:
    """Expand the two home variables setup writes. Nothing else is a shell."""
    home = str(Path.home())
    bus = os.environ.get("AGENT_BUS_HOME", "")
    value = value.replace("${HOME}", home).replace("$HOME", home)
    if bus:
        value = value.replace("${AGENT_BUS_HOME}", bus).replace("$AGENT_BUS_HOME", bus)
    return value


def _identity(value: str) -> bool:
    return _ID.fullmatch(value) is not None


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def _allowlist(endpoint: str) -> dict[str, object]:
    peer = "bob" if endpoint == "alice" else "alice" if endpoint == "bob" else "peer"
    if peer == endpoint:
        peer = "other"
    return {
        "endpoints": [endpoint, peer],
        "allow": [
            {"from": peer, "to": endpoint, "kinds": ["chat"]},
            {"from": endpoint, "to": peer, "kinds": ["chat"]},
        ],
    }


def setup_home(
    home: Path,
    *,
    simple: bool = False,
    port: int = 8765,
    endpoint: str = "alice",
    producer: str = "producer",
    worker: str = "worker",
) -> list[str]:
    """Create the home. An existing env file is left as it is, including its secret."""
    if not 1 <= port <= 65535:
        raise SystemExit("port must be from 1 to 65535")
    names = (producer, worker) if simple else (endpoint,)
    for name in names:
        if not _identity(name):
            raise SystemExit("an endpoint id must be 1-64 characters from A-Z, a-z, 0-9, _ and -")
    env_path = home / "env"
    if env_path.exists():
        return [f"kept {env_path}"]
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    state = home / "state"
    state.mkdir(exist_ok=True)
    os.chmod(state, 0o700)
    lines = [
        "# Written by agent-bus setup. This file holds a secret. Do not commit it.",
        f"AGENT_BUS_STATE_DIR={state}",
        f"AGENT_BUS_BEARER_SECRET={secrets.token_hex(32)}",
        "AGENT_BUS_BIND_HOST=loopback",
        f"AGENT_BUS_PORT={port}",
    ]
    notes = [f"wrote {env_path}", f"state directory {state}"]
    if simple:
        lines.append(f"AGENT_BUS_PRODUCER_ID={producer}")
        lines.append(f"AGENT_BUS_WORKER_ID={worker}")
        notes.append(f"simple pair {producer} and {worker}")
    else:
        config = home / "endpoints.json"
        lines.append(f"AGENT_BUS_CONFIG={config}")
        lines.append(f"AGENT_BUS_ENDPOINT_ID={endpoint}")
        if not config.exists():
            _write_private(config, json.dumps(_allowlist(endpoint), indent=2) + "\n")
            notes.append(f"wrote {config}")
        else:
            notes.append(f"kept {config}")
    _write_private(env_path, "\n".join(lines) + "\n")
    return notes


def load_home(home: Path) -> dict[str, str]:
    env_path = home / "env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OSError(env_path) from exc
    return {key: expand_value(value) for key, value in parse_env(text).items()}


def doctor(home: Path) -> list[Check]:
    """Check one home. The bearer token is never included in a detail."""
    env_path = home / "env"
    if not env_path.is_file():
        return [Check("env file", False, f"missing {env_path}. Run agent-bus setup.")]
    mode = _mode(env_path)
    if mode != 0o600:
        return [Check("env file", False, f"{env_path} must be mode 600. Run chmod 600 {env_path}")]
    try:
        values = load_home(home)
    except OSError:
        return [Check("env file", False, f"could not read {env_path}")]
    checks = [Check("env file", True, str(env_path))]
    checks.extend(_value_checks(values))
    checks.append(_directory("state", values.get("AGENT_BUS_STATE_DIR", "")))
    if identity_mode(values) == "graph":
        checks.extend(_graph_checks(values))
    checks.append(_server())
    other = _other_home(home)
    if other is not None:
        checks.append(Check("other env", True, f"left unread at {other}"))
    return checks


def _value_checks(values: Mapping[str, str]) -> list[Check]:
    mode = identity_mode(values)
    if mode == "ambiguous":
        return [
            Check(
                "mode",
                False,
                "both the simple pair and the graph settings are set. "
                "Unset AGENT_BUS_PRODUCER_ID and AGENT_BUS_WORKER_ID, "
                "or unset AGENT_BUS_CONFIG and AGENT_BUS_ENDPOINT_ID.",
            )
        ]
    missing = missing_env(values)
    if missing:
        return [Check("mode", False, "missing " + ", ".join(missing))]
    label = "graph" if mode == "graph" else "simple pair"
    checks = [Check("mode", True, label)]
    for name in _ALWAYS_ENV + (_GRAPH_ENV if mode == "graph" else _LEGACY_ENV):
        if values.get(name, "").startswith(_PLACEHOLDER):
            checks.append(Check(name, False, f"{name} is still a placeholder. Replace it, or move this file aside and run agent-bus setup."))
    if any(not item.ok for item in checks):
        return checks
    bearer = values.get("AGENT_BUS_BEARER_SECRET", "")
    if len(bearer) < 32:
        checks.append(Check("bearer", False, "AGENT_BUS_BEARER_SECRET is shorter than 32 characters. Replace it with a random token."))
    else:
        checks.append(Check("bearer", True, "set"))
    port = values.get("AGENT_BUS_PORT", "")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        checks.append(Check("port", False, "AGENT_BUS_PORT must be a number from 1 to 65535"))
    else:
        checks.append(Check("port", True, port))
    host = values.get("AGENT_BUS_BIND_HOST", "loopback")
    if host != "loopback":
        checks.append(Check("bind", False, "AGENT_BUS_BIND_HOST must be loopback"))
    else:
        checks.append(Check("bind", True, "loopback"))
    for name in ("AGENT_BUS_PRODUCER_ID", "AGENT_BUS_WORKER_ID", "AGENT_BUS_ENDPOINT_ID"):
        value = values.get(name, "")
        if value and not _identity(value):
            checks.append(Check(name, False, f"{name} must be 1-64 characters from A-Z, a-z, 0-9, _ and -"))
    nats = values.get("AGENT_BUS_NATS_URL", "")
    if nats.startswith(_PLACEHOLDER):
        checks.append(Check("nats", False, "AGENT_BUS_NATS_URL is still a placeholder. Remove the line, or set a nats:// URL."))
    elif nats:
        checks.append(Check("nats", True, "set"))
    return checks


def _directory(name: str, raw: str) -> Check:
    if not raw or raw.startswith(_PLACEHOLDER):
        return Check(name, False, f"AGENT_BUS_STATE_DIR is unset. Run agent-bus setup.")
    path = Path(raw)
    if not path.is_dir():
        return Check(name, False, f"missing {path}. Create it with mode 700.")
    mode = _mode(path)
    if mode != 0o700:
        return Check(name, False, f"{path} must be mode 700. Run chmod 700 {path}")
    return Check(name, True, str(path))


def _webhook_check(document: dict) -> Check:
    """Check the optional webhooks block. Never prints a URL path or a secret."""
    from .webhook import webhooks_from_config

    try:
        hooks = webhooks_from_config(document)
    except QueueRefused as exc:
        return Check("webhooks", False, str(exc))
    for hook in hooks:
        if hook.secret_file and not Path(hook.secret_file).is_file():
            return Check("webhooks", False, f"secret_file for {hook.endpoint} is missing")
    return Check("webhooks", True, ", ".join(hook.endpoint for hook in hooks) or "none")


def _graph_checks(values: Mapping[str, str]) -> list[Check]:
    raw = values.get("AGENT_BUS_CONFIG", "")
    path = Path(raw)
    if not path.is_file():
        return [Check("endpoints", False, f"missing {path}")]
    mode = _mode(path)
    if mode != 0o600:
        return [Check("endpoints", False, f"{path} must be mode 600. Run chmod 600 {path}")]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        load_config(path)
    except (OSError, UnicodeError, json.JSONDecodeError, QueueRefused):
        return [Check("endpoints", False, f"{path} is not a valid allowlist. It needs endpoints and allow.")]
    names = document.get("endpoints") if isinstance(document, dict) else None
    listed = [item for item in names if isinstance(item, str)] if isinstance(names, list) else []
    endpoint = values.get("AGENT_BUS_ENDPOINT_ID", "")
    checks = [Check("endpoints", True, ", ".join(listed))]
    if isinstance(document, dict) and "webhooks" in document:
        checks.append(_webhook_check(document))
    if endpoint not in listed:
        checks.append(Check("this endpoint", False, f"{endpoint} is not in {path}"))
    else:
        checks.append(Check("this endpoint", True, endpoint))
    return checks


def _server() -> Check:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        return Check("server", False, "install an ASGI server to run the bridge")
    return Check("server", True, "uvicorn")


def _other_home(home: Path) -> Path | None:
    candidates = [Path.home() / ".agent-bus" / "env", Path.home() / ".config" / "agent-bus-mcp" / "env"]
    current = home / "env"
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() != current.resolve():
            return candidate
    return None


def format_checks(checks: list[Check]) -> str:
    lines = []
    for item in checks:
        mark = "pass" if item.ok else "fail"
        lines.append(f"{item.name}: {mark}  {item.detail}")
    return "\n".join(lines)


def apply_env(path: Path) -> None:
    """Put the file into the process. A variable that is already set wins."""
    if not path.is_file():
        raise SystemExit(f"no env file at {path}. Run agent-bus setup.")
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, expand_value(value))


def _run(home: Path) -> int:
    checks = doctor(home)
    print(format_checks(checks))
    if any(not item.ok for item in checks):
        return 1
    apply_env(home / "env")
    from .mcp import main as serve

    return serve([])


def _endpoint(home: Path, argv: list[str]) -> int:
    if not argv:
        raise SystemExit("usage: agent-bus endpoint ENDPOINT_ID COMMAND ...")
    endpoint_id = argv[0]
    if not _identity(endpoint_id):
        raise SystemExit("an endpoint id must be 1-64 characters from A-Z, a-z, 0-9, _ and -")
    env_path = home / "env"
    if not env_path.is_file():
        raise SystemExit(f"no env file at {env_path}. Run agent-bus setup.")
    values = load_home(home)
    if "AGENT_BUS_CONFIG" not in values:
        raise SystemExit("this home has no endpoints file. Graph commands need the file agent-bus setup writes.")
    apply_env(env_path)
    os.environ["AGENT_BUS_ENDPOINT_ID"] = endpoint_id
    from .endpoint import main as endpoint_main

    return endpoint_main(argv[1:])


def _setup_from_args(home: Path, args: argparse.Namespace) -> int:
    simple = args.simple
    port = 8765 if args.port is None else args.port
    endpoint = "alice" if args.endpoint is None else args.endpoint
    producer = "producer" if args.producer is None else args.producer
    worker = "worker" if args.worker is None else args.worker
    if sys.stdin.isatty() and not args.non_interactive and args.port is None and args.endpoint is None and not args.simple:
        answer = input("Mode [graph/simple] (graph): ").strip().lower()
        simple = answer == "simple"
        if simple:
            producer = input("Producer id (producer): ").strip() or producer
            worker = input("Worker id (worker): ").strip() or worker
        else:
            endpoint = input("This endpoint id (alice): ").strip() or endpoint
        port_text = input("Port (8765): ").strip()
        if port_text:
            if not port_text.isdigit():
                raise SystemExit("port must be from 1 to 65535")
            port = int(port_text)
    notes = setup_home(home, simple=simple, port=port, endpoint=endpoint, producer=producer, worker=worker)
    print("\n".join(notes))
    print("Next: agent-bus doctor")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-bus", description="Write and check one local Agent Bus home.")
    sub = parser.add_subparsers(dest="command", required=True)
    setup_parser = sub.add_parser("setup", help="Write the home, the env file, and a starter allowlist.")
    setup_parser.add_argument("--non-interactive", action="store_true")
    setup_parser.add_argument("--simple", action="store_true", help="Write the producer and worker pair instead of a graph.")
    setup_parser.add_argument("--port", type=int)
    setup_parser.add_argument("--endpoint")
    setup_parser.add_argument("--producer")
    setup_parser.add_argument("--worker")
    sub.add_parser("doctor", help="Check the home and print the fix. Does not print the bearer token.")
    sub.add_parser("run", help="Load the env file and start the loopback server.")
    endpoint_parser = sub.add_parser("endpoint", help="Load the env file and run one endpoint command.")
    endpoint_parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args([] if argv is None else argv)
    home = home_path()
    if args.command == "setup":
        return _setup_from_args(home, args)
    if args.command == "doctor":
        checks = doctor(home)
        print(format_checks(checks))
        return 0 if all(item.ok for item in checks) else 1
    if args.command == "run":
        return _run(home)
    remainder = list(args.args)
    if remainder[:1] == ["--"]:
        remainder = remainder[1:]
    return _endpoint(home, remainder)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
