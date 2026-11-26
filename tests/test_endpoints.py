import json
from pathlib import Path

import pytest

from agent_bus_mcp.endpoint import main
from agent_bus_mcp.endpoints import open_endpoint, policy_from_config, post_chat, post_handoff
from agent_bus_mcp.queue import QueueRefused


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "endpoints.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": ["alice", "bob", "carol", "eve", "dave"],
                "allow": [
                    {"from": "bob", "to": "alice", "kinds": ["chat"]},
                    {"from": "alice", "to": "bob", "kinds": ["chat", "handoff"]},
                    {"from": "dave", "to": "carol", "kinds": ["chat"]},
                    {"from": "carol", "to": "eve", "kinds": ["handoff"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_config_refuses_unknown_kind_and_self_edge() -> None:
    with pytest.raises(QueueRefused):
        policy_from_config(
            {
                "endpoints": ["alice", "bob"],
                "allow": [{"from": "alice", "to": "alice", "kinds": ["chat"]}],
            }
        )
    with pytest.raises(QueueRefused):
        policy_from_config(
            {
                "endpoints": ["alice", "bob"],
                "allow": [{"from": "alice", "to": "bob", "kinds": ["job"]}],
            }
        )


def test_two_endpoints_chat_without_becoming_a_task(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    sender = open_endpoint(state, config, "bob")
    receiver = open_endpoint(state, config, "alice")
    sent = post_chat(sender, to="alice", text="what version are you on", key="once")
    claimed = receiver.claim_question()
    assert claimed is not None
    assert claimed["question"] == "what version are you on"
    assert receiver.answer_question(
        consultation_id=claimed["consultation_id"],
        lease_id=claimed["lease_id"],
        answer="0.1.0",
    ) == "answered"
    assert sender.read_answer(sent["consultation_id"])["answer"] == "0.1.0"
    assert list((state / "tasks").glob("task_*.json")) == []
    with pytest.raises(QueueRefused):
        post_chat(open_endpoint(state, config, "dave"), to="alice", text="no", key="no")


def test_handoff_is_a_pointer_not_file_bytes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    sender = open_endpoint(state, config, "alice")
    pointer = "https://example.invalid/note"
    task_id = post_handoff(sender, to="bob", pointer=pointer, key="file-1")
    claimed = open_endpoint(state, config, "bob").claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert claimed["references"] == [pointer]
    with pytest.raises(QueueRefused):
        post_handoff(sender, to="bob", pointer="file:///tmp/note", key="file-2")


def test_alice_bob_example_is_the_first_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    example = Path(__file__).parents[1] / "examples" / "alice-bob.json"
    config = tmp_path / "endpoints.json"
    config.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "bob")
    assert main(["post-chat", "--to", "alice", "--text", "what version are you on", "--key", "once"]) == 0
    sent = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "alice")
    assert main(["claim-chat"]) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["question"] == "what version are you on"
    assert main(["answer-chat", "--id", claimed["consultation_id"], "--lease", claimed["lease_id"], "--text", "0.1.0"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "bob")
    assert main(["read-chat", sent["consultation_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["answer"] == "0.1.0"


def test_cli_uses_the_process_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "bob")
    assert main(["post-chat", "--to", "alice", "--text", "hello", "--key", "cli-1"]) == 0
    sent = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "alice")
    assert main(["claim-chat"]) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert main(["answer-chat", "--id", claimed["consultation_id"], "--lease", claimed["lease_id"], "--text", "hi"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "bob")
    assert main(["read-chat", sent["consultation_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["answer"] == "hi"
    assert main(["post-chat", "--to", "dave", "--text", "no", "--key", "cli-2"]) == 2


def test_file_accepted_is_work_and_refuses_a_spoofed_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "endpoints.json"
    config.write_text(
        json.dumps(
            {
                "endpoints": ["alice", "bob"],
                "allow": [{"from": "alice", "to": "bob", "kinds": ["handoff"]}],
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    alice = open_endpoint(state, config, "alice")
    from agent_bus_mcp.endpoints import file_accepted, read_accepted

    first = file_accepted(alice, to="bob", text="develop the intake", caller_id="alice", key="once")
    assert file_accepted(alice, to="bob", text="develop the intake", caller_id="alice", key="once") == first
    assert file_accepted(alice, to="bob", text="update the secretary note", caller_id="alice", key="note") != first
    assert list((state / "consultations").glob("consultation_*.json")) == []
    assert file_accepted(alice, to="bob", text="restart the gateway", caller_id="alice", key="blocked")
    assert file_accepted(alice, to="bob", text="do not spend on this", caller_id="alice", key="spend")
    with pytest.raises(QueueRefused, match=r"^request refused: contains a control character at position 6$"):
        file_accepted(alice, to="bob", text="unsafe\x00text", caller_id="alice", key="control")
    with pytest.raises(QueueRefused, match=r"^request refused: caller is not this endpoint$"):
        file_accepted(alice, to="bob", text="develop the intake", caller_id="bob", key="spoof")
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "alice")
    assert main(["read-accepted", "--to", "bob", "--key", "once"]) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["task_id"] == first
    assert read["status"] == "pending"
    assert read["result"] is None
    assert "develop the intake" not in capsys.readouterr().out
    assert read_accepted(alice, to="bob", key="once")["status"] == "pending"
