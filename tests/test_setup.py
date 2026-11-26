import json
import os
import stat
from pathlib import Path

import pytest

from agent_bus_mcp.mcp import missing_env
from agent_bus_mcp.setup import home_path, main, parse_env, setup_home


_KEYS = (
    "AGENT_BUS_STATE_DIR",
    "AGENT_BUS_BEARER_SECRET",
    "AGENT_BUS_BIND_HOST",
    "AGENT_BUS_PORT",
    "AGENT_BUS_CONFIG",
    "AGENT_BUS_ENDPOINT_ID",
    "AGENT_BUS_PRODUCER_ID",
    "AGENT_BUS_WORKER_ID",
    "AGENT_BUS_NATS_URL",
)


def _clear(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("AGENT_BUS_HOME", str(home))
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)


def _bearer(home: Path) -> str:
    for key, value in parse_env((home / "env").read_text(encoding="utf-8")).items():
        if key == "AGENT_BUS_BEARER_SECRET":
            return value
    raise AssertionError("bearer was not written")


def test_setup_writes_a_private_home_and_doctor_hides_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _clear(monkeypatch, tmp_path)
    assert main(["setup", "--non-interactive"]) == 0
    env = tmp_path / "env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "endpoints.json").stat().st_mode) == 0o600
    secret = _bearer(tmp_path)
    assert len(secret) == 64
    assert secret not in capsys.readouterr().out
    assert main(["doctor"]) == 0
    report = capsys.readouterr().out
    assert "bearer: pass  set" in report
    assert "mode: pass  graph" in report
    assert secret not in report
    assert "alice, bob" in report


def test_setup_keeps_an_existing_env_file(tmp_path: Path) -> None:
    setup_home(tmp_path)
    secret = _bearer(tmp_path)
    (tmp_path / "endpoints.json").write_text('{"kept": true}\n', encoding="utf-8")
    os.chmod(tmp_path / "endpoints.json", 0o600)
    notes = setup_home(tmp_path, simple=True, endpoint="carol")
    assert notes == [f"kept {tmp_path / 'env'}"]
    assert _bearer(tmp_path) == secret
    assert "kept" in (tmp_path / "endpoints.json").read_text(encoding="utf-8")


def test_simple_pair_has_no_graph_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _clear(monkeypatch, tmp_path)
    assert main(["setup", "--non-interactive", "--simple", "--producer", "producer", "--worker", "worker"]) == 0
    text = (tmp_path / "env").read_text(encoding="utf-8")
    assert "AGENT_BUS_PRODUCER_ID=producer" in text
    assert "AGENT_BUS_CONFIG" not in text
    assert not (tmp_path / "endpoints.json").exists()
    capsys.readouterr()
    assert main(["doctor"]) == 0
    assert "simple pair" in capsys.readouterr().out


def test_bad_endpoint_id_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        main(["setup", "--non-interactive", "--endpoint", "bad id"])
    assert not (tmp_path / "env").exists()


def test_doctor_reports_the_manual_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _clear(monkeypatch, tmp_path)
    setup_home(tmp_path)
    env = tmp_path / "env"
    text = env.read_text(encoding="utf-8")
    env.write_text(text.replace("AGENT_BUS_PORT=8765", "AGENT_BUS_PORT=nope"), encoding="utf-8")
    os.chmod(env, 0o600)
    assert main(["doctor"]) == 1
    assert "AGENT_BUS_PORT must be a number" in capsys.readouterr().out

    env.write_text(text + "AGENT_BUS_PRODUCER_ID=producer\nAGENT_BUS_WORKER_ID=worker\n", encoding="utf-8")
    os.chmod(env, 0o600)
    assert main(["doctor"]) == 1
    assert "both the simple pair and the graph settings are set" in capsys.readouterr().out

    placeholder = "REPLACE_WITH_OPERATOR_GENERATED_TOKEN"
    rewritten = []
    for line in text.splitlines():
        if line.startswith("AGENT_BUS_BEARER_SECRET="):
            rewritten.append(f"AGENT_BUS_BEARER_SECRET={placeholder}")
        else:
            rewritten.append(line)
    env.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    os.chmod(env, 0o600)
    assert main(["doctor"]) == 1
    report = capsys.readouterr().out
    assert "still a placeholder" in report
    assert placeholder not in report

    os.chmod(env, 0o644)
    assert main(["doctor"]) == 1
    assert "chmod 600" in capsys.readouterr().out


def test_doctor_reads_a_hand_written_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENT_BUS_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".config" / "agent-bus-mcp"
    state = tmp_path / ".local" / "state" / "agent-bus-mcp"
    legacy.mkdir(parents=True)
    state.mkdir(parents=True)
    os.chmod(legacy, 0o700)
    os.chmod(state, 0o700)
    secret = "a" * 64
    (legacy / "env").write_text(
        "\n".join(
            [
                f"AGENT_BUS_STATE_DIR={state}",
                f"AGENT_BUS_BEARER_SECRET={secret}",
                "AGENT_BUS_BIND_HOST=loopback",
                "AGENT_BUS_PORT=8765",
                "AGENT_BUS_PRODUCER_ID=producer",
                "AGENT_BUS_WORKER_ID=worker",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(legacy / "env", 0o600)
    assert home_path() == legacy
    assert main(["doctor"]) == 0
    report = capsys.readouterr().out
    assert secret not in report
    assert not (tmp_path / ".agent-bus").exists()


def test_run_loads_the_file_without_printing_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _clear(monkeypatch, tmp_path)
    setup_home(tmp_path)
    secret = _bearer(tmp_path)
    seen: dict[str, object] = {}

    def fake_serve(argv: list[str] | None = None) -> int:
        seen["argv"] = argv
        seen["port"] = os.environ["AGENT_BUS_PORT"]
        seen["secret"] = os.environ["AGENT_BUS_BEARER_SECRET"]
        return 0

    monkeypatch.setattr("agent_bus_mcp.mcp.main", fake_serve)
    assert main(["run"]) == 0
    assert seen == {"argv": [], "port": "8765", "secret": secret}
    assert secret not in capsys.readouterr().out


def test_endpoint_command_loads_the_file_and_sets_the_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, tmp_path)
    setup_home(tmp_path)
    seen: dict[str, object] = {}

    def fake_endpoint(argv: list[str] | None = None) -> int:
        seen["argv"] = argv
        seen["id"] = os.environ["AGENT_BUS_ENDPOINT_ID"]
        seen["state"] = os.environ["AGENT_BUS_STATE_DIR"]
        return 0

    monkeypatch.setattr("agent_bus_mcp.endpoint.main", fake_endpoint)
    assert main(["endpoint", "bob", "post-chat", "--to", "alice", "--text", "hello", "--key", "once"]) == 0
    assert seen["argv"] == ["post-chat", "--to", "alice", "--text", "hello", "--key", "once"]
    assert seen["id"] == "bob"
    assert seen["state"] == str(tmp_path / "state")


def test_endpoint_command_refuses_a_simple_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, tmp_path)
    setup_home(tmp_path, simple=True)
    with pytest.raises(SystemExit, match="endpoints file"):
        main(["endpoint", "bob", "post-chat"])


def test_missing_env_can_check_a_file_without_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUS_STATE_DIR", raising=False)
    found = missing_env({"AGENT_BUS_CONFIG": "/tmp/endpoints.json"})
    assert found == ["AGENT_BUS_STATE_DIR", "AGENT_BUS_BEARER_SECRET", "AGENT_BUS_PORT", "AGENT_BUS_ENDPOINT_ID"]
    assert "AGENT_BUS_STATE_DIR" not in os.environ


def test_setup_leaves_a_hand_written_env_file_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENT_BUS_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".config" / "agent-bus-mcp"
    legacy.mkdir(parents=True)
    secret = "c" * 64
    (legacy / "env").write_text(f"AGENT_BUS_BEARER_SECRET={secret}\n", encoding="utf-8")
    os.chmod(legacy / "env", 0o600)
    assert main(["setup", "--non-interactive"]) == 0
    report = capsys.readouterr().out
    assert "kept" in report
    assert secret not in report
    assert not (tmp_path / ".agent-bus").exists()
    assert not (legacy / "state").exists()
    assert secret in (legacy / "env").read_text(encoding="utf-8")


def test_new_home_is_checked_ahead_of_the_hand_written_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENT_BUS_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".config" / "agent-bus-mcp"
    legacy.mkdir(parents=True)
    secret = "d" * 64
    (legacy / "env").write_text(
        "\n".join(["AGENT_BUS_PORT=9999", f"AGENT_BUS_BEARER_SECRET={secret}"]) + "\n",
        encoding="utf-8",
    )
    os.chmod(legacy / "env", 0o600)
    setup_home(tmp_path / ".agent-bus", port=8765)
    assert home_path() == tmp_path / ".agent-bus"
    assert main(["doctor"]) == 0
    report = capsys.readouterr().out
    assert "port: pass  8765" in report
    assert "9999" not in report
    assert secret not in report
    assert "left unread" in report


def test_doctor_does_not_print_a_nats_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _clear(monkeypatch, tmp_path)
    setup_home(tmp_path)
    env = tmp_path / "env"
    secret_url = "nats://user:secret-token@127.0.0.1:4222"
    env.write_text(env.read_text(encoding="utf-8") + f"AGENT_BUS_NATS_URL={secret_url}\n", encoding="utf-8")
    os.chmod(env, 0o600)
    assert main(["doctor"]) == 0
    report = capsys.readouterr().out
    assert "nats: pass  set" in report
    assert "secret-token" not in report


def test_custom_endpoint_is_in_the_allowlist(tmp_path: Path) -> None:
    setup_home(tmp_path, endpoint="north")
    document = json.loads((tmp_path / "endpoints.json").read_text(encoding="utf-8"))
    assert document["endpoints"] == ["north", "peer"]
    assert {"from": "north", "to": "peer", "kinds": ["chat"]} in document["allow"]
    setup_home(tmp_path / "peer-home", endpoint="peer")
    peer = json.loads((tmp_path / "peer-home" / "endpoints.json").read_text(encoding="utf-8"))
    assert peer["endpoints"] == ["peer", "other"]
