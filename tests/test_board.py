import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent_bus_mcp.endpoint import main
from agent_bus_mcp.endpoints import open_endpoint, post_board, promote_board, read_board
from agent_bus_mcp.queue import DurableQueue, QueueRefused

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "two-endpoints.json"
_SRC = str(Path(__file__).resolve().parents[1] / "src")
_POLL_TRIALS = 24
_KEY_TRIALS = 8


def _write(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _pair(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["alfred", "core", "jig", "stranger"],
            "allow": [
                {"from": "alfred", "to": "core", "kinds": ["chat", "handoff"]},
                {"from": "core", "to": "alfred", "kinds": ["chat"]},
            ],
            "boards": [{"id": "pair", "members": ["alfred", "core"]}],
        },
    )


def test_pair_talk_does_not_own_work(tmp_path: Path) -> None:
    config = _pair(tmp_path)
    state = tmp_path / "state"
    alfred = open_endpoint(state, config, "alfred")
    core = open_endpoint(state, config, "core")
    posted = post_board(alfred, config=config, board="pair", text="what should own this", key="once")
    read = read_board(core, config=config, board="pair")
    assert read[0]["post_id"] == posted["post_id"]
    assert read[0]["text"] == "what should own this"
    assert read[0]["handoff_id"] is None
    assert list((state / "tasks").glob("task_*.json")) == []
    with pytest.raises(QueueRefused):
        read_board(open_endpoint(state, config, "stranger"), config=config, board="pair")
    with pytest.raises(QueueRefused):
        post_board(open_endpoint(state, config, "jig"), config=config, board="pair", text="no", key="no")


def test_promote_names_the_owner_and_links_both_ways(tmp_path: Path) -> None:
    config = _pair(tmp_path)
    state = tmp_path / "state"
    alfred = open_endpoint(state, config, "alfred")
    posted = post_board(alfred, config=config, board="pair", text="core owns the fix", key="talk")
    task_id = promote_board(alfred, config=config, board="pair", post_id=posted["post_id"], key="own")
    assert promote_board(alfred, config=config, board="pair", post_id=posted["post_id"], key="own") == task_id
    with pytest.raises(QueueRefused):
        promote_board(alfred, config=config, board="pair", post_id=posted["post_id"], key="again")
    claimed = open_endpoint(state, config, "core").claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert posted["post_id"] in claimed["goal"]
    assert open_endpoint(state, config, "alfred").claim() is None
    assert read_board(alfred, config=config, board="pair")[0]["handoff_id"] == task_id
    link = json.loads((state / "promotions" / f"{task_id}.json").read_text(encoding="utf-8"))
    assert link == {"board": "pair", "post_id": posted["post_id"], "to": "core"}


def test_only_the_promoting_endpoint_reads_a_result(tmp_path: Path) -> None:
    config = _pair(tmp_path)
    state = tmp_path / "state"
    alfred = open_endpoint(state, config, "alfred")
    core = open_endpoint(state, config, "core")
    posted = post_board(alfred, config=config, board="pair", text="core owns this", key="once")
    task_id = promote_board(alfred, config=config, board="pair", post_id=posted["post_id"], key="own")
    assert alfred.read_result(task_id) == {"task_id": task_id, "status": "pending", "result": None}
    claimed = core.claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    core.finish(task_id=task_id, lease_id=claimed["lease_id"], result="the fix is in", failed=False)
    assert alfred.read_result(task_id) == {"task_id": task_id, "status": "completed", "result": "the fix is in"}
    with pytest.raises(QueueRefused):
        core.read_result(task_id)
    with pytest.raises(QueueRefused):
        open_endpoint(state, config, "jig").read_result(task_id)
    with pytest.raises(QueueRefused):
        open_endpoint(state, config, "stranger").read_result(task_id)
    with pytest.raises(QueueRefused):
        alfred.read_result("task_" + "0" * 32)
    failed_post = post_board(alfred, config=config, board="pair", text="core owns this too", key="twice")
    failed_task = promote_board(alfred, config=config, board="pair", post_id=failed_post["post_id"], key="own2")
    claimed_failed = core.claim()
    assert claimed_failed is not None
    assert claimed_failed["task_id"] == failed_task
    core.finish(task_id=failed_task, lease_id=claimed_failed["lease_id"], result=None, failed=True)
    assert alfred.read_result(failed_task) == {"task_id": failed_task, "status": "failed", "result": None}
    ordinary = alfred.enqueue(goal="plain work", references=[], idempotency_key="plain")
    claimed_plain = core.claim()
    assert claimed_plain is not None
    assert claimed_plain["task_id"] == ordinary
    core.finish(task_id=ordinary, lease_id=claimed_plain["lease_id"], result="plain done", failed=False)
    with pytest.raises(QueueRefused):
        alfred.read_result(ordinary)


def test_a_missing_or_second_owner_is_refused(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["alfred", "core", "jig"],
            "allow": [
                {"from": "alfred", "to": "core", "kinds": ["handoff"]},
                {"from": "alfred", "to": "jig", "kinds": ["handoff"]},
            ],
            "boards": [{"id": "room", "members": ["alfred", "core", "jig"]}],
        },
    )
    state = tmp_path / "state"
    alfred = open_endpoint(state, config, "alfred")
    posted = post_board(alfred, config=config, board="room", text="who owns it", key="talk")
    with pytest.raises(QueueRefused):
        promote_board(alfred, config=config, board="room", post_id=posted["post_id"], key="own")
    named = promote_board(
        alfred, config=config, board="room", post_id=posted["post_id"], key="own", to="core"
    )
    assert open_endpoint(state, config, "core").claim()["task_id"] == named
    assert open_endpoint(state, config, "jig").claim() is None
    assert read_board(open_endpoint(state, config, "jig"), config=config, board="room")[0]["text"] == "who owns it"


def test_a_board_needs_two_known_members() -> None:
    from agent_bus_mcp.endpoints import boards_from_config

    with pytest.raises(QueueRefused):
        boards_from_config(
            {"endpoints": ["alfred"], "boards": [{"id": "pair", "members": ["alfred"]}]},
        )
    with pytest.raises(QueueRefused):
        boards_from_config(
            {
                "endpoints": ["alfred", "core"],
                "boards": [{"id": "pair", "members": ["alfred", "missing"]}],
            },
        )


def test_cli_promotes_the_alfred_core_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _pair(tmp_path)
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "alfred")
    assert main(["post-board", "--board", "pair", "--text", "core owns the fix", "--key", "talk"]) == 0
    posted = json.loads(capsys.readouterr().out)
    assert main(["promote-board", "--board", "pair", "--post", posted["post_id"], "--key", "own"]) == 0
    task_id = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "core")
    assert main(["read-board", "--board", "pair"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["handoff_id"] == task_id
    assert main(["claim-handoff"]) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["task_id"] == task_id
    assert claimed["board"] == "pair"
    assert claimed["post_id"] == posted["post_id"]


def test_two_random_endpoints_connect_without_a_house_name(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["north", "south"],
            "allow": [
                {"from": "north", "to": "south", "kinds": ["chat", "handoff"]},
                {"from": "south", "to": "north", "kinds": ["chat"]},
            ],
            "boards": [{"id": "room", "members": ["north", "south"]}],
        },
    )
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    posted = post_board(north, config=config, board="room", text="south owns the fix", key="talk")
    task_id = promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own")
    claimed = open_endpoint(state, config, "south").claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert claimed["board"] == "room"
    assert claimed["post_id"] == posted["post_id"]
    assert "south owns the fix" not in claimed["goal"]
    assert open_endpoint(state, config, "north").claim() is None


def test_two_promote_keys_leave_one_handoff(tmp_path: Path) -> None:
    import threading

    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["north", "south"],
            "allow": [{"from": "north", "to": "south", "kinds": ["handoff"]}],
            "boards": [{"id": "room", "members": ["north", "south"]}],
        },
    )
    state = tmp_path / "state"
    posted = post_board(
        open_endpoint(state, config, "north"),
        config=config,
        board="room",
        text="one owner",
        key="talk",
    )
    results: list[object] = []

    def promote(key: str) -> None:
        try:
            results.append(
                promote_board(
                    open_endpoint(state, config, "north"),
                    config=config,
                    board="room",
                    post_id=posted["post_id"],
                    key=key,
                )
            )
        except QueueRefused:
            results.append("refused")

    threads = [threading.Thread(target=promote, args=(key,)) for key in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("refused") == 1
    assert len({item for item in results if item != "refused"}) == 1
    assert len(list((state / "tasks").glob("task_*.json"))) == 1


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _SRC + (os.pathsep + current if current else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("AGENT_BUS_PRODUCER_ID", "AGENT_BUS_WORKER_ID", "AGENT_BUS_CONFIG", "AGENT_BUS_ENDPOINT_ID"):
        env.pop(name, None)
    return env


def _run_child(tmp_path: Path, script: str, args: list[str]) -> subprocess.Popen[str]:
    path = tmp_path / script
    return subprocess.Popen(
        [sys.executable, str(path), *args],
        cwd=tmp_path,
        env=_child_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.kill()
    _stdout, stderr = process.communicate(timeout=5)
    return stderr


def test_cross_process_polling_claims_the_link(tmp_path: Path) -> None:
    script = tmp_path / "poll_claim.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys, time",
                "from pathlib import Path",
                "from agent_bus_mcp.endpoints import open_endpoint",
                "state, config, endpoint, output = sys.argv[1:]",
                "queue = open_endpoint(Path(state), Path(config), endpoint)",
                "deadline = time.monotonic() + 5",
                "while time.monotonic() < deadline:",
                "    claimed = queue.claim()",
                "    if claimed is not None:",
                "        Path(output).write_text(json.dumps(claimed), encoding='utf-8')",
                "        raise SystemExit(0)",
                "Path(output).write_text('null', encoding='utf-8')",
                "raise SystemExit(2)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = _write(tmp_path / "endpoints.json", json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    for index in range(_POLL_TRIALS):
        state = tmp_path / f"state-{index}"
        posted = post_board(
            open_endpoint(state, config, "north"),
            config=config,
            board="room",
            text="south owns the fix",
            key="talk",
        )
        output = tmp_path / f"claim-{index}.json"
        process = _run_child(tmp_path, "poll_claim.py", [str(state), str(config), "south", str(output)])
        try:
            task_id = promote_board(
                open_endpoint(state, config, "north"),
                config=config,
                board="room",
                post_id=posted["post_id"],
                key="own",
            )
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
            _stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        claimed = json.loads(output.read_text(encoding="utf-8"))
        assert claimed["task_id"] == task_id
        assert claimed["board"] == "room"
        assert claimed["post_id"] == posted["post_id"]
        assert "south owns the fix" not in claimed["goal"]
        stored = json.loads((state / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
        assert stored["board"] == "room"
        assert stored["post_id"] == posted["post_id"]
        assert len(list((state / "tasks").glob("task_*.json"))) == 1


def test_cross_process_promote_keys_leave_one_owner(tmp_path: Path) -> None:
    script = tmp_path / "promote_one.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "from agent_bus_mcp.endpoints import open_endpoint, promote_board",
                "from agent_bus_mcp.queue import QueueRefused",
                "state, config, post_id, key, destination, output = sys.argv[1:]",
                "try:",
                "    task_id = promote_board(",
                "        open_endpoint(Path(state), Path(config), 'north'),",
                "        config=Path(config), board='room', post_id=post_id, key=key, to=destination,",
                "    )",
                "    Path(output).write_text(json.dumps({'ok': task_id}), encoding='utf-8')",
                "except QueueRefused:",
                "    Path(output).write_text(json.dumps({'refused': True}), encoding='utf-8')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["north", "south", "east"],
            "allow": [
                {"from": "north", "to": "south", "kinds": ["handoff"]},
                {"from": "north", "to": "east", "kinds": ["handoff"]},
            ],
            "boards": [{"id": "room", "members": ["north", "south", "east"]}],
        },
    )
    for index in range(_KEY_TRIALS):
        state = tmp_path / f"state-{index}"
        posted = post_board(
            open_endpoint(state, config, "north"),
            config=config,
            board="room",
            text="one owner",
            key="talk",
        )
        processes = []
        outputs = []
        for key, destination in (("one", "south"), ("two", "east")):
            output = tmp_path / f"out-{index}-{key}.json"
            outputs.append(output)
            processes.append(
                _run_child(
                    tmp_path,
                    "promote_one.py",
                    [str(state), str(config), posted["post_id"], key, destination, str(output)],
                )
            )
        try:
            for process in processes:
                process.wait(timeout=5)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
        results = [json.loads(path.read_text(encoding="utf-8")) for path in outputs]
        assert sum("refused" in item for item in results) == 1
        ids = {item["ok"] for item in results if "ok" in item}
        assert len(ids) == 1
        tasks = list((state / "tasks").glob("task_*.json"))
        assert len(tasks) == 1
        stored = json.loads(tasks[0].read_text(encoding="utf-8"))
        assert stored["task_id"] in ids
        assert stored["worker_id"] in ("south", "east")
        assert stored["board"] == "room"
        assert stored["post_id"] == posted["post_id"]


def test_completed_unrelated_task_does_not_become_the_handoff(tmp_path: Path) -> None:
    config = _write(tmp_path / "endpoints.json", json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    posted = post_board(north, config=config, board="room", text="real request", key="talk")
    ordinary_key = "promote-room-%s" % posted["post_id"]
    existing = north.enqueue(
        goal="unrelated pointer work",
        references=["https://example.com/notes"],
        idempotency_key=ordinary_key,
        destination_id="south",
    )
    claimed = open_endpoint(state, config, "south").claim()
    assert claimed is not None and claimed["task_id"] == existing
    assert "board" not in claimed
    finished = open_endpoint(state, config, "south").finish(
        task_id=existing,
        lease_id=claimed["lease_id"],
        result="already done",
        failed=False,
    )
    promoted = promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own")
    assert finished == "completed"
    assert promoted != existing
    tasks = list((state / "tasks").glob("task_*.json"))
    assert len(tasks) == 2
    old = json.loads((state / "tasks" / f"{existing}.json").read_text(encoding="utf-8"))
    new = json.loads((state / "tasks" / f"{promoted}.json").read_text(encoding="utf-8"))
    assert old["status"] == "completed"
    assert old["goal"] == "unrelated pointer work"
    assert "board" not in old
    assert new["board"] == "room"
    assert new["post_id"] == posted["post_id"]
    assert new["status"] == "pending"
    ordinary = json.loads((state / "idempotency" / "north" / "south" / ordinary_key).read_text(encoding="utf-8"))
    promotion = json.loads(
        (state / "promotion_idempotency" / "north" / "south" / ordinary_key).read_text(encoding="utf-8")
    )
    assert ordinary["task_id"] == existing
    assert promotion["task_id"] == promoted
    assert promotion["promote_key"] == "own"
    second = open_endpoint(state, config, "south").claim()
    assert second is not None
    assert second["task_id"] == promoted
    assert second["board"] == "room"
    assert second["post_id"] == posted["post_id"]


def test_only_the_author_can_name_one_member_owner(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["north", "south", "east", "witness"],
            "allow": [
                {"from": "north", "to": "south", "kinds": ["handoff"]},
                {"from": "north", "to": "witness", "kinds": ["handoff"]},
                {"from": "east", "to": "south", "kinds": ["handoff"]},
            ],
            "boards": [{"id": "room", "members": ["north", "south", "east"]}],
        },
    )
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    posted = post_board(north, config=config, board="room", text="please leave this with south", key="talk")
    with pytest.raises(QueueRefused):
        promote_board(
            open_endpoint(state, config, "east"),
            config=config,
            board="room",
            post_id=posted["post_id"],
            key="seize",
        )
    with pytest.raises(QueueRefused):
        promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="witness")
    assert list((state / "tasks").glob("task_*.json")) == []
    task_id = promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="south")
    claimed = open_endpoint(state, config, "south").claim()
    assert claimed is not None
    assert claimed["task_id"] == task_id
    assert claimed["board"] == "room"
    assert claimed["post_id"] == posted["post_id"]
    assert read_board(open_endpoint(state, config, "south"), config=config, board="room")[0]["text"] == "please leave this with south"
    with pytest.raises(QueueRefused):
        read_board(open_endpoint(state, config, "witness"), config=config, board="room")
    with pytest.raises(QueueRefused):
        promote_board(
            open_endpoint(state, config, "east"),
            config=config,
            board="room",
            post_id=posted["post_id"],
            key="late",
            to="south",
        )
    assert len(list((state / "tasks").glob("task_*.json"))) == 1

    solo_config = _write(
        tmp_path / "solo.json",
        {
            "endpoints": ["north", "south", "witness"],
            "allow": [{"from": "north", "to": "witness", "kinds": ["handoff"]}],
            "boards": [{"id": "room", "members": ["north", "south"]}],
        },
    )
    solo = tmp_path / "solo"
    author = open_endpoint(solo, solo_config, "north")
    solo_post = post_board(author, config=solo_config, board="room", text="no outside owner", key="talk")
    with pytest.raises(QueueRefused):
        promote_board(author, config=solo_config, board="room", post_id=solo_post["post_id"], key="own")
    with pytest.raises(QueueRefused):
        promote_board(
            author,
            config=solo_config,
            board="room",
            post_id=solo_post["post_id"],
            key="own",
            to="witness",
        )
    assert list((solo / "tasks").glob("task_*.json")) == []


def test_replay_refuses_a_different_destination_and_repairs_the_sidecar(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "endpoints.json",
        {
            "endpoints": ["north", "south", "east"],
            "allow": [
                {"from": "north", "to": "south", "kinds": ["handoff"]},
                {"from": "north", "to": "east", "kinds": ["handoff"]},
            ],
            "boards": [{"id": "room", "members": ["north", "south", "east"]}],
        },
    )
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    posted = post_board(north, config=config, board="room", text="south owns it", key="talk")
    task_id = promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="south")
    with pytest.raises(QueueRefused):
        promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="east")
    assert promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="south") == task_id
    sidecar = state / "promotions" / f"{task_id}.json"
    sidecar.unlink()
    assert promote_board(north, config=config, board="room", post_id=posted["post_id"], key="own", to="south") == task_id
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "board": "room",
        "post_id": posted["post_id"],
        "to": "south",
    }
    stored = json.loads((state / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
    assert stored["worker_id"] == "south"
    assert stored["board"] == "room"
    assert len(list((state / "tasks").glob("task_*.json"))) == 1


def test_a_failed_reservation_recovers_only_for_the_same_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write(tmp_path / "endpoints.json", json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    posted = post_board(north, config=config, board="room", text="hold", key="talk")
    real = DurableQueue._write_promoted_task

    def fail_once(self: DurableQueue, **kwargs: object) -> str:
        monkeypatch.setattr(DurableQueue, "_write_promoted_task", real)
        raise QueueRefused()

    monkeypatch.setattr(DurableQueue, "_write_promoted_task", fail_once)
    with pytest.raises(QueueRefused):
        promote_board(north, config=config, board="room", post_id=posted["post_id"], key="one")
    assert list((state / "tasks").glob("task_*.json")) == []
    with pytest.raises(QueueRefused):
        promote_board(north, config=config, board="room", post_id=posted["post_id"], key="two")
    task_id = promote_board(north, config=config, board="room", post_id=posted["post_id"], key="one")
    assert len(list((state / "tasks").glob("task_*.json"))) == 1
    assert (state / "promotions" / f"{task_id}.json").is_file()
    claimed = open_endpoint(state, config, "south").claim()
    assert claimed is not None and claimed["board"] == "room" and claimed["post_id"] == posted["post_id"]


def test_ordinary_claim_omits_the_board_link(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="north", worker_id="south")
    queue.enqueue(goal="plain work", references=[], idempotency_key="plain")
    claimed = DurableQueue(tmp_path, producer_id="north", worker_id="south").claim()
    assert claimed is not None
    assert "board" not in claimed
    assert "post_id" not in claimed


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_documented_example_server_starts_and_claims_the_handoff(tmp_path: Path) -> None:
    state = tmp_path / "state"
    north = open_endpoint(state, _EXAMPLE, "north")
    posted = post_board(north, config=_EXAMPLE, board="room", text="south owns the fix", key="talk")
    task_id = promote_board(north, config=_EXAMPLE, board="room", post_id=posted["post_id"], key="own")
    with pytest.raises(QueueRefused):
        DurableQueue(state, producer_id="north", worker_id="south")
    port = _free_port()
    env = _child_env()
    env.update(
        {
            "AGENT_BUS_STATE_DIR": str(state),
            "AGENT_BUS_CONFIG": str(_EXAMPLE),
            "AGENT_BUS_ENDPOINT_ID": "south",
            "AGENT_BUS_BEARER_SECRET": "synthetic-review-secret",
            "AGENT_BUS_PORT": str(port),
            "AGENT_BUS_BIND_HOST": "loopback",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_bus_mcp.mcp"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claim_task", "arguments": {}}}
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=payload,
        headers={
            "Authorization": "Bearer synthetic-review-secret",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    body = ""
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    body = response.read().decode()
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.05)
        else:
            stderr = _stop(process)
            raise AssertionError(stderr)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    claimed = json.loads(body)["result"]["structuredContent"]
    assert claimed["status"] == "claimed"
    assert claimed["task_id"] == task_id
    assert claimed["board"] == "room"
    assert claimed["post_id"] == posted["post_id"]
