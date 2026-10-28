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
                "endpoints": ["jig", "grokbot", "openclaw", "hermes", "dots"],
                "allow": [
                    {"from": "grokbot", "to": "jig", "kinds": ["chat"]},
                    {"from": "jig", "to": "grokbot", "kinds": ["chat", "handoff"]},
                    {"from": "dots", "to": "openclaw", "kinds": ["chat"]},
                    {"from": "openclaw", "to": "hermes", "kinds": ["handoff"]},
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
                "endpoints": ["jig", "grokbot"],
                "allow": [{"from": "jig", "to": "jig", "kinds": ["chat"]}],
            }
        )
    with pytest.raises(QueueRefused):
        policy_from_config(
            {
                "endpoints": ["jig", "grokbot"],
                "allow": [{"from": "jig", "to": "grokbot", "kinds": ["job"]}],
            }
        )


def test_two_endpoints_chat_without_becoming_a_task(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    sender = open_endpoint(state, config, "grokbot")
    receiver = open_endpoint(state, config, "jig")
    sent = post_chat(sender, to="jig", text="what version are you on", key="once")
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
        post_chat(open_endpoint(state, config, "dots"), to="jig", text="no", key="no")


def test_handoff_is_a_pointer_not_file_bytes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    sender = open_endpoint(state, config, "jig")
    pointer = "https://example.invalid/note"
    task_id = post_handoff(sender, to="grokbot", pointer=pointer, key="file-1")
    claimed = open_endpoint(state, config, "grokbot").claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert claimed["references"] == [pointer]
    with pytest.raises(QueueRefused):
        post_handoff(sender, to="grokbot", pointer="file:///tmp/note", key="file-2")


def test_cli_uses_the_process_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "grokbot")
    assert main(["post-chat", "--to", "jig", "--text", "hello", "--key", "cli-1"]) == 0
    sent = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "jig")
    assert main(["claim-chat"]) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert main(["answer-chat", "--id", claimed["consultation_id"], "--lease", claimed["lease_id"], "--text", "hi"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "grokbot")
    assert main(["read-chat", sent["consultation_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["answer"] == "hi"
    assert main(["post-chat", "--to", "dots", "--text", "no", "--key", "cli-2"]) == 2
