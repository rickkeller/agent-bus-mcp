import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_bus_mcp.endpoints import open_endpoint, post_board
from agent_bus_mcp.mcp import TOOLS
from agent_bus_mcp.queue import QueueRefused
from agent_bus_mcp.remote import _mutate, _once, load_enrollment


def _src() -> str:
    return str(Path(__file__).resolve().parents[1] / "src")


def _env(tmp: Path) -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _src() + (os.pathsep + current if current else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["AGENT_BUS_REMOTE_CA"] = str(tmp / "ca.pem")
    return env


def _openssl(directory: Path, *args: str) -> None:
    completed = subprocess.run(
        ["openssl", *args],
        cwd=directory,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _ca(directory: Path, name: str = "ca", *, strict: bool = True) -> None:
    command = [
        "req", "-x509", "-newkey", "rsa:2048", "-keyout", f"{name}.key", "-out", f"{name}.pem",
        "-days", "2", "-nodes", "-subj", f"/CN={name}",
    ]
    if strict:
        command.extend(
            [
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-addext", "subjectKeyIdentifier=hash",
            ]
        )
    _openssl(directory, *command)


def _leaf(directory: Path, name: str, *, server: bool, ca: str = "ca") -> None:
    _openssl(directory, "req", "-newkey", "rsa:2048", "-keyout", f"{name}.key", "-out", f"{name}.csr", "-nodes", "-subj", f"/CN={name}")
    if server:
        text = "\n".join(
            [
                "basicConstraints=critical,CA:FALSE",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "extendedKeyUsage=serverAuth",
                "subjectAltName=IP:127.0.0.1,DNS:localhost",
                "subjectKeyIdentifier=hash",
                "authorityKeyIdentifier=keyid,issuer",
                "",
            ]
        )
    else:
        text = "\n".join(
            [
                "basicConstraints=critical,CA:FALSE",
                "keyUsage=critical,digitalSignature",
                "extendedKeyUsage=clientAuth",
                "subjectKeyIdentifier=hash",
                "authorityKeyIdentifier=keyid,issuer",
                "",
            ]
        )
    (directory / f"{name}.ext").write_text(text, encoding="utf-8")
    _openssl(
        directory,
        "x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}.pem", "-CAkey", f"{ca}.key", "-CAcreateserial",
        "-out", f"{name}.pem", "-days", "2", "-extfile", f"{name}.ext",
    )


def _certs(directory: Path) -> None:
    _ca(directory)
    _leaf(directory, "server", server=True)
    for name in ("north", "south", "witness", "intruder"):
        _leaf(directory, name, server=False)


def _graph(directory: Path) -> Path:
    path = directory / "endpoints.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": ["north", "south", "witness"],
                "allow": [
                    {"from": "north", "to": "south", "kinds": ["handoff"]},
                    {"from": "south", "to": "north", "kinds": ["chat"]},
                ],
                "boards": [{"id": "room", "members": ["north", "south"]}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _enroll(env: dict[str, str], directory: Path, endpoint: str) -> None:
    completed = subprocess.run(
        [
            sys.executable, "-m", "agent_bus_mcp.enroll", "add",
            "--file", str(directory / "enrollment.json"),
            "--endpoint", endpoint,
            "--cert", str(directory / f"{endpoint}.pem"),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server(
    env: dict[str, str],
    directory: Path,
    state: Path,
    config: Path,
    port: int,
    extra: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    child = env.copy()
    child.update(
        {
            "AGENT_BUS_STATE_DIR": str(state),
            "AGENT_BUS_CONFIG": str(config),
            "AGENT_BUS_ENROLLMENT": str(directory / "enrollment.json"),
            "AGENT_BUS_REMOTE_PORT": str(port),
            "AGENT_BUS_REMOTE_HOST": "127.0.0.1",
            "AGENT_BUS_TLS_CERT": str(directory / "server.pem"),
            "AGENT_BUS_TLS_KEY": str(directory / "server.key"),
            "AGENT_BUS_TLS_CLIENT_CA": str(directory / "ca.pem"),
        }
    )
    if extra:
        child.update(extra)
    return subprocess.Popen(
        [sys.executable, "-m", "agent_bus_mcp.remote"],
        cwd=directory,
        env=child,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    _stdout, stderr = process.communicate()
    return stderr or ""


def _call(env: dict[str, str], directory: Path, port: int, endpoint: str, tool: str, request_id: str, **flags: object) -> subprocess.CompletedProcess[str]:
    child = env.copy()
    child["AGENT_BUS_REMOTE_URL"] = f"https://127.0.0.1:{port}"
    child["AGENT_BUS_CLIENT_CERT"] = str(directory / f"{endpoint}.pem")
    child["AGENT_BUS_CLIENT_KEY"] = str(directory / f"{endpoint}.key")
    command = [sys.executable, "-m", "agent_bus_mcp.remote_call", "--tool", tool, "--request-id", request_id, "--timeout", "5"]
    for name, value in flags.items():
        command.extend(["--" + name.replace("_", "-"), str(value)])
    return subprocess.run(command, env=child, text=True, capture_output=True, check=False)


def _wait_ready(env: dict[str, str], directory: Path, port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process.poll() is None:
        probe = _call(env, directory, port, "north", "resume_leases", "ready-probe")
        if probe.returncode == 0:
            return
        time.sleep(0.05)
    raise AssertionError(_stop(process))


def test_tls_host_serves_one_directory_and_retries_safely(tmp_path: Path) -> None:
    _certs(tmp_path)
    config = _graph(tmp_path)
    env = _env(tmp_path)
    for endpoint in ("north", "south", "witness"):
        _enroll(env, tmp_path, endpoint)
    state = tmp_path / "state"
    port = _port()
    server = _server(env, tmp_path, state, config, port)
    try:
        _wait_ready(env, tmp_path, port, server)
        posted = _call(env, tmp_path, port, "north", "post_board", "post-1", board="room", text="south owns the fix", key="talk")
        assert posted.returncode == 0, posted.stderr
        body = json.loads(posted.stdout)
        assert body["author"] == "north"
        assert body["handoff_id"] is None
        replay = _call(env, tmp_path, port, "north", "post_board", "post-1", board="room", text="south owns the fix", key="talk")
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout)["post_id"] == body["post_id"]
        same_key = _call(env, tmp_path, port, "north", "post_board", "post-2", board="room", text="different", key="talk")
        assert json.loads(same_key.stdout)["post_id"] == body["post_id"]
        assert json.loads(same_key.stdout)["text"] == "south owns the fix"
        assert len(list((state / "boards" / "room" / "posts").glob("post_*.json"))) == 1
        assert list((state / "tasks").glob("task_*.json")) == []
        south_unread = json.loads(_call(env, tmp_path, port, "south", "poll_unread", "poll-1", board="room").stdout)
        assert [item["post_id"] for item in south_unread["posts"]] == [body["post_id"]]
        north_unread = json.loads(_call(env, tmp_path, port, "north", "poll_unread", "poll-n", board="room").stdout)
        assert north_unread["posts"] == []
        read = json.loads(_call(env, tmp_path, port, "south", "read_board", "read-1", board="room").stdout)
        assert read["posts"][0]["text"] == "south owns the fix"
        assert json.loads(_call(env, tmp_path, port, "south", "poll_unread", "poll-2", board="room").stdout)["posts"]
        witness = _call(env, tmp_path, port, "witness", "post_board", "nope", board="room", text="no", key="nope")
        assert witness.returncode == 2
        assert witness.stderr.strip() == "request refused"
        intruder = _call(env, tmp_path, port, "intruder", "poll_unread", "intrude", board="room")
        assert intruder.returncode == 2
        promoted = _call(env, tmp_path, port, "north", "promote_board", "own-1", board="room", post=body["post_id"], key="own")
        assert promoted.returncode == 0, promoted.stderr
        task_id = json.loads(promoted.stdout)["task_id"]
        again = _call(env, tmp_path, port, "north", "promote_board", "own-1", board="room", post=body["post_id"], key="own")
        assert json.loads(again.stdout)["task_id"] == task_id
        wrong = _call(env, tmp_path, port, "north", "promote_board", "own-1", board="room", post=body["post_id"], key="other")
        assert wrong.returncode == 2
        south_promote = _call(env, tmp_path, port, "south", "promote_board", "steal", board="room", post=body["post_id"], key="steal")
        assert south_promote.returncode == 2
        assert len(list((state / "tasks").glob("task_*.json"))) == 1
        claimed = json.loads(_call(env, tmp_path, port, "south", "claim_task", "claim-1").stdout)
        lost = json.loads(_call(env, tmp_path, port, "south", "claim_task", "claim-1").stdout)
        assert claimed["status"] == "claimed"
        assert lost == claimed
        resumed = json.loads(_call(env, tmp_path, port, "south", "resume_leases", "resume-1").stdout)
        assert resumed["leases"][0]["lease_id"] == claimed["lease_id"]
        assert resumed["leases"][0]["task_id"] == task_id
        empty = json.loads(_call(env, tmp_path, port, "south", "claim_task", "claim-2").stdout)
        assert empty["status"] == "empty"
        north_claim = json.loads(_call(env, tmp_path, port, "north", "claim_task", "claim-n").stdout)
        assert north_claim["status"] == "empty"
        stolen = _call(
            env, tmp_path, port, "north", "complete_task", "finish-n",
            task_id=task_id, lease=claimed["lease_id"], result="no",
        )
        assert stolen.returncode == 2
        finished = json.loads(
            _call(
                env, tmp_path, port, "south", "complete_task", "finish-1",
                task_id=task_id, lease=claimed["lease_id"], result="done",
            ).stdout
        )
        assert finished["status"] == "completed"
        repeated = json.loads(
            _call(
                env, tmp_path, port, "south", "complete_task", "finish-1",
                task_id=task_id, lease=claimed["lease_id"], result="done",
            ).stdout
        )
        assert repeated["status"] == "completed"
        acked = json.loads(_call(env, tmp_path, port, "south", "ack_board", "ack-1", board="room", posts=body["post_id"]).stdout)
        assert acked["posts"] == [body["post_id"]]
        assert json.loads(_call(env, tmp_path, port, "south", "poll_unread", "poll-3", board="room").stdout)["posts"] == []
        assert json.loads(_call(env, tmp_path, port, "south", "read_board", "read-2", board="room").stdout)["posts"][0]["post_id"] == body["post_id"]
    finally:
        stderr = _stop(server)
    assert "south owns the fix" not in stderr
    assert "PRIVATE KEY" not in stderr


def test_remote_read_result_reads_only_for_the_promoter(tmp_path: Path) -> None:
    _certs(tmp_path)
    config = _graph(tmp_path)
    env = _env(tmp_path)
    for endpoint in ("north", "south", "witness"):
        _enroll(env, tmp_path, endpoint)
    state = tmp_path / "state"
    port = _port()
    server = _server(env, tmp_path, state, config, port)
    try:
        _wait_ready(env, tmp_path, port, server)
        posted = json.loads(_call(env, tmp_path, port, "north", "post_board", "post-1", board="room", text="south owns the fix", key="talk").stdout)
        task_id = json.loads(_call(env, tmp_path, port, "north", "promote_board", "own-1", board="room", post=posted["post_id"], key="own").stdout)["task_id"]
        claimed = json.loads(_call(env, tmp_path, port, "south", "claim_task", "claim-1").stdout)
        assert claimed["task_id"] == task_id
        finished = json.loads(_call(env, tmp_path, port, "south", "complete_task", "finish-1", task_id=task_id, lease=claimed["lease_id"], result="done").stdout)
        assert finished["status"] == "completed"
        read = json.loads(_call(env, tmp_path, port, "north", "read_result", "read-1", task_id=task_id).stdout)
        assert read == {"task_id": task_id, "status": "completed", "result": "done"}
        replay = json.loads(_call(env, tmp_path, port, "north", "read_result", "read-1", task_id=task_id).stdout)
        assert replay == read
        fresh = json.loads(_call(env, tmp_path, port, "north", "read_result", "read-2", task_id=task_id).stdout)
        assert fresh == read
        worker = _call(env, tmp_path, port, "south", "read_result", "read-s", task_id=task_id)
        assert worker.returncode == 2
        assert worker.stderr.strip() == "request refused"
        witness = _call(env, tmp_path, port, "witness", "read_result", "read-w", task_id=task_id)
        assert witness.returncode == 2
        mismatch = _call(env, tmp_path, port, "north", "read_result", "read-1", task_id="task_" + "0" * 32)
        assert mismatch.returncode == 2
        assert mismatch.stderr.strip() == "request refused"
    finally:
        stderr = _stop(server)
    assert "the fix" not in stderr


def test_concurrent_post_and_ack_and_revocation(tmp_path: Path) -> None:
    _certs(tmp_path)
    config = _graph(tmp_path)
    env = _env(tmp_path)
    for endpoint in ("north", "south", "witness"):
        _enroll(env, tmp_path, endpoint)
    state = tmp_path / "state"
    port = _port()
    server = _server(env, tmp_path, state, config, port)
    try:
        _wait_ready(env, tmp_path, port, server)
        first = subprocess.Popen(
            _command(env, tmp_path, port, "north", "post_board", "c-post-a", board="room", text="alpha", key="alpha"),
            env=_client_env(env, tmp_path, port, "north"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        posted = json.loads(_call(env, tmp_path, port, "north", "post_board", "c-post-b", board="room", text="beta", key="beta").stdout)
        first.wait(timeout=5)
        assert first.returncode == 0
        both = json.loads(_call(env, tmp_path, port, "south", "poll_unread", "c-poll", board="room").stdout)["posts"]
        assert {item["text"] for item in both} == {"alpha", "beta"}
        ack = subprocess.Popen(
            _command(env, tmp_path, port, "south", "ack_board", "c-ack", board="room", posts=posted["post_id"]),
            env=_client_env(env, tmp_path, port, "south"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        during = json.loads(_call(env, tmp_path, port, "north", "post_board", "c-post-c", board="room", text="during", key="during").stdout)
        ack.wait(timeout=5)
        assert ack.returncode == 0
        remaining = json.loads(_call(env, tmp_path, port, "south", "poll_unread", "c-left", board="room").stdout)["posts"]
        assert during["post_id"] in {item["post_id"] for item in remaining}
        assert posted["post_id"] not in {item["post_id"] for item in remaining}
        revoked = subprocess.run(
            [sys.executable, "-m", "agent_bus_mcp.enroll", "revoke", "--file", str(tmp_path / "enrollment.json"), "--endpoint", "north"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert revoked.returncode == 0, revoked.stderr
        assert _call(env, tmp_path, port, "north", "poll_unread", "after-revoke", board="room").returncode == 2
        assert _call(env, tmp_path, port, "south", "poll_unread", "south-stays", board="room").returncode == 0
    finally:
        _stop(server)
    restarted = _server(env, tmp_path, state, config, port)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and restarted.poll() is None:
            if _call(env, tmp_path, port, "south", "resume_leases", "restart-ready").returncode == 0:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(_stop(restarted))
        assert _call(env, tmp_path, port, "north", "poll_unread", "still-revoked", board="room").returncode == 2
        assert _call(env, tmp_path, port, "south", "read_board", "south-after-restart", board="room").returncode == 0
    finally:
        _stop(restarted)


def test_remote_refuses_plaintext_impersonation_and_a_second_writer(tmp_path: Path) -> None:
    assert "resume_leases" not in {tool["name"] for tool in TOOLS}
    state = tmp_path / "local"
    config = _graph(tmp_path)
    posted = post_board(open_endpoint(state, config, "north"), config=config, board="room", text="local", key="local")
    assert posted["handoff_id"] is None
    env = _env(tmp_path)
    env["AGENT_BUS_REMOTE_URL"] = "http://127.0.0.1:9"
    env["AGENT_BUS_CLIENT_CERT"] = str(tmp_path / "north.pem")
    env["AGENT_BUS_CLIENT_KEY"] = str(tmp_path / "north.key")
    _certs(tmp_path)
    plaintext = subprocess.run(
        [sys.executable, "-m", "agent_bus_mcp.remote_call", "--tool", "resume_leases", "--request-id", "plain"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert plaintext.returncode != 0
    assert "https" in plaintext.stderr
    _enroll(env, tmp_path, "north")
    port = _port()
    server = _server(env, tmp_path, tmp_path / "remote-state", config, port)
    try:
        _wait_ready(env, tmp_path, port, server)
        child = _client_env(env, tmp_path, port, "north")
        impersonated = subprocess.run(
            [
                sys.executable, "-c",
                "import os, sys\n"
                "from pathlib import Path\n"
                "from agent_bus_mcp.remote import call_remote\n"
                "from agent_bus_mcp.queue import QueueRefused\n"
                "try:\n"
                "    call_remote(url=os.environ['AGENT_BUS_REMOTE_URL'], ca=Path(os.environ['AGENT_BUS_REMOTE_CA']), certificate=Path(os.environ['AGENT_BUS_CLIENT_CERT']), key=Path(os.environ['AGENT_BUS_CLIENT_KEY']), tool='post_board', arguments={'board':'room','text':'x','key':'k','endpoint_id':'south'}, request_id='spoof', timeout=5)\n"
                "except QueueRefused:\n"
                "    sys.exit(2)\n",
            ],
            env=child,
            text=True,
            capture_output=True,
            check=False,
        )
        assert impersonated.returncode == 2
        second = _server(env, tmp_path, tmp_path / "remote-state", config, _port())
        try:
            second.wait(timeout=5)
        except subprocess.TimeoutExpired:
            second.kill()
        _stdout, stderr = second.communicate()
        assert "already running" in (stderr or "")
    finally:
        _stop(server)


def test_same_request_id_is_atomic_per_endpoint(tmp_path: Path) -> None:
    config = _graph(tmp_path)
    state = tmp_path / "state"
    north = open_endpoint(state, config, "north")
    south = open_endpoint(state, config, "south")
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def conflict(text: str, key: str) -> None:
        barrier.wait(timeout=5)
        try:
            outcomes.append(
                _once(
                    north,
                    "same-id",
                    "post_board",
                    {"board": "room", "text": text, "key": key},
                    lambda text=text, key=key: _mutate(
                        north,
                        config,
                        "post_board",
                        {"board": "room", "text": text, "key": key},
                    ),
                )
            )
        except QueueRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=conflict, args=pair) for pair in (("one", "alpha"), ("two", "beta"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    posts = list((state / "boards" / "room" / "posts").glob("post_*.json"))
    assert len(posts) == 1
    assert outcomes.count("refused") == 1
    assert len(outcomes) == 2

    same: list[str] = []

    def agree() -> None:
        result = _once(
            north,
            "repeat-id",
            "post_board",
            {"board": "room", "text": "same", "key": "same"},
            lambda: _mutate(north, config, "post_board", {"board": "room", "text": "same", "key": "same"}),
        )
        same.append(result["post_id"])

    pair = [threading.Thread(target=agree) for _ in range(2)]
    for thread in pair:
        thread.start()
    for thread in pair:
        thread.join(timeout=5)
    assert len(set(same)) == 1
    assert len(list((state / "boards" / "room" / "posts").glob("post_*.json"))) == 2

    foreign: list[str] = []

    def other(queue, key: str) -> None:
        foreign.append(
            _once(
                queue,
                "shared-label",
                "post_board",
                {"board": "room", "text": key, "key": key},
                lambda queue=queue, key=key: _mutate(
                    queue, config, "post_board", {"board": "room", "text": key, "key": key}
                ),
            )["post_id"]
        )

    ends = [
        threading.Thread(target=other, args=(north, "north-post")),
        threading.Thread(target=other, args=(south, "south-post")),
    ]
    for thread in ends:
        thread.start()
    for thread in ends:
        thread.join(timeout=5)
    assert len(set(foreign)) == 2
    worker = tmp_path / "once_worker.py"
    worker.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                "from agent_bus_mcp.endpoints import open_endpoint",
                "from agent_bus_mcp.queue import QueueRefused",
                "from agent_bus_mcp.remote import _mutate, _once",
                "state, config, text, key = sys.argv[1:]",
                "queue = open_endpoint(Path(state), Path(config), 'north')",
                "arguments = {'board': 'room', 'text': text, 'key': key}",
                "try:",
                "    result = _once(queue, 'proc-id', 'post_board', arguments, lambda: _mutate(queue, Path(config), 'post_board', arguments))",
                "    print(result['post_id'])",
                "except QueueRefused:",
                "    raise SystemExit(2)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.Popen(
            [sys.executable, str(worker), str(state), str(config), text, key],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for text, key in (("left", "left-key"), ("right", "right-key"))
    ]
    codes = [process.wait(timeout=5) for process in processes]
    assert sorted(codes) == [0, 2]
    assert len(list((state / "boards" / "room" / "posts").glob("post_*.json"))) == 5


def test_content_length_and_enrollment_and_ca_extensions(tmp_path: Path) -> None:
    _certs(tmp_path)
    config = _graph(tmp_path)
    env = _env(tmp_path)
    _enroll(env, tmp_path, "north")
    original = (tmp_path / "enrollment.json").read_bytes()
    (tmp_path / "enrollment.json").write_bytes(original + b"\n{")
    corrupt = subprocess.run(
        [sys.executable, "-m", "agent_bus_mcp.enroll", "add", "--file", str(tmp_path / "enrollment.json"), "--endpoint", "south", "--cert", str(tmp_path / "south.pem")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert corrupt.returncode != 0
    assert (tmp_path / "enrollment.json").read_bytes() == original + b"\n{"
    assert not list(tmp_path.glob(".enrollment.json.*.tmp"))
    (tmp_path / "enrollment.json").write_bytes(original)
    mixed = json.loads(original)
    mixed["credentials"].append({"endpoint": "south", "sha256": "ZZ"})
    (tmp_path / "enrollment.json").write_text(json.dumps(mixed), encoding="utf-8")
    try:
        load_enrollment(tmp_path / "enrollment.json")
        raise AssertionError("bad row was accepted")
    except QueueRefused:
        pass
    (tmp_path / "enrollment.json").write_bytes(original)
    checked = subprocess.run(
        [sys.executable, "-m", "agent_bus_mcp.enroll", "check", "--file", str(tmp_path / "enrollment.json")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0
    assert checked.stdout.strip() == "enrollment valid: 1"
    port = _port()
    server = _server(env, tmp_path, tmp_path / "state", config, port)
    try:
        _wait_ready(env, tmp_path, port, server)
        context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=str(tmp_path / "ca.pem"))
        context.load_cert_chain(str(tmp_path / "north.pem"), str(tmp_path / "north.key"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if flag:
            context.verify_flags |= flag
        sock = context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5), server_hostname="127.0.0.1")
        sock.sendall(b"POST /v1/call HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: nope\r\nConnection: close\r\n\r\n")
        sock.settimeout(3)
        payload = sock.recv(300)
        sock.close()
        assert payload.startswith(b"HTTP/1.1 400")
        assert _call(env, tmp_path, port, "north", "resume_leases", "after-bad-length").returncode == 0
    finally:
        _stop(server)
    weak = tmp_path / "weak"
    weak.mkdir()
    _ca(weak, "ca", strict=False)
    _openssl(weak, "req", "-newkey", "rsa:2048", "-keyout", "client.key", "-out", "client.csr", "-nodes", "-subj", "/CN=client")
    (weak / "client.ext").write_text("basicConstraints=CA:FALSE\nextendedKeyUsage=clientAuth\n", encoding="utf-8")
    _openssl(
        weak,
        "x509", "-req", "-in", "client.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-out", "client.pem", "-days", "2", "-extfile", "client.ext",
    )
    if not getattr(ssl, "VERIFY_X509_STRICT", 0):
        return
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(str(weak / "ca.pem"))
    context.load_cert_chain(str(weak / "client.pem"), str(weak / "client.key"))
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    # The strict flag is on the client. A server presented by this weak CA is not what we need.
    # Build a one-shot server from the weak CA and require the handshake to fail closed.
    weak_port = _port()
    weak_env = _env(weak)
    _openssl(
        weak,
        "req", "-newkey", "rsa:2048", "-keyout", "server.key", "-out", "server.csr", "-nodes", "-subj", "/CN=localhost",
    )
    (weak / "server.ext").write_text(
        "basicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n",
        encoding="utf-8",
    )
    _openssl(
        weak,
        "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-out", "server.pem", "-days", "2", "-extfile", "server.ext",
    )
    (weak / "endpoints.json").write_text((tmp_path / "endpoints.json").read_text(encoding="utf-8"), encoding="utf-8")
    enrolled = subprocess.run(
        [sys.executable, "-m", "agent_bus_mcp.enroll", "add", "--file", str(weak / "enrollment.json"), "--endpoint", "north", "--cert", str(weak / "client.pem")],
        env=weak_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert enrolled.returncode == 0, enrolled.stderr
    weak_server = _server(weak_env, weak, weak / "state", config, weak_port)
    try:
        deadline = time.monotonic() + 5
        listening = False
        while time.monotonic() < deadline and weak_server.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", weak_port), timeout=0.2):
                    listening = True
                    break
            except OSError:
                time.sleep(0.05)
        assert listening
        child = weak_env.copy()
        child["AGENT_BUS_REMOTE_URL"] = f"https://127.0.0.1:{weak_port}"
        child["AGENT_BUS_CLIENT_CERT"] = str(weak / "client.pem")
        child["AGENT_BUS_CLIENT_KEY"] = str(weak / "client.key")
        probe = subprocess.run(
            [sys.executable, "-m", "agent_bus_mcp.remote_call", "--tool", "resume_leases", "--request-id", "weak", "--timeout", "3"],
            env=child,
            text=True,
            capture_output=True,
            check=False,
        )
        assert probe.returncode != 0
        assert weak_server.poll() is None
    finally:
        _stop(weak_server)


def test_script_stop_kills_the_listener_and_leaves_other_processes(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "two-machine" / "two-machine.sh"
    directory = tmp_path / "pair"
    port = _port()
    prepared = subprocess.run(
        ["sh", str(script), "prepare-host", str(directory), "127.0.0.1", str(port)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    host = subprocess.Popen(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 8
        pid_text = ""
        while time.monotonic() < deadline:
            pid_path = directory / "host.pid"
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip():
                pid_text = pid_path.read_text(encoding="utf-8").strip()
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    pass
            if host.poll() is not None:
                raise AssertionError(host.stderr.read() if host.stderr else "host script exited")
            time.sleep(0.05)
        else:
            raise AssertionError("listener did not open the port")
        assert pid_text != str(host.pid)
        command = subprocess.run(["ps", "-p", pid_text, "-o", "args="], text=True, capture_output=True, check=False)
        assert "agent_bus_mcp.remote" in command.stdout or "agent-bus-remote" in command.stdout
        stopped = subprocess.run(["sh", str(script), "stop", str(directory)], text=True, capture_output=True, check=False)
        assert stopped.returncode == 0, stopped.stderr
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    time.sleep(0.05)
                    continue
            except OSError:
                break
        else:
            raise AssertionError("port stayed open")
        alive = subprocess.run(["kill", "-0", pid_text], check=False)
        assert alive.returncode != 0
        again = subprocess.run(["sh", str(script), "stop", str(directory)], text=True, capture_output=True, check=False)
        assert again.returncode == 0, again.stderr
    finally:
        if host.poll() is None:
            host.terminate()
            try:
                host.wait(timeout=3)
            except subprocess.TimeoutExpired:
                host.kill()
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        (directory / "host.pid").write_text("%s\n" % unrelated.pid, encoding="utf-8")
        refused = subprocess.run(["sh", str(script), "stop", str(directory)], text=True, capture_output=True, check=False)
        assert refused.returncode != 0
        assert "not killing" in refused.stderr
        still = subprocess.run(["kill", "-0", str(unrelated.pid)], check=False)
        assert still.returncode == 0
    finally:
        unrelated.kill()
        unrelated.wait(timeout=3)


def test_second_host_keeps_the_running_listener_pid(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "two-machine" / "two-machine.sh"
    directory = tmp_path / "pair"
    port = _port()
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prepared = subprocess.run(
        ["sh", str(script), "prepare-host", str(directory), "127.0.0.1", str(port)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    first = subprocess.Popen(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        tracked = _wait_for_listener(directory, port, first)
        second = subprocess.Popen(
            ["sh", str(script), "host", str(directory)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            second.wait(timeout=8)
        except subprocess.TimeoutExpired:
            second.kill()
            second.wait(timeout=3)
            raise AssertionError("second host did not exit")
        _out, second_err = second.communicate()
        assert second.returncode != 0
        assert "already running" in (second_err or "")
        assert (directory / "host.pid").read_text(encoding="utf-8").strip() == tracked
        assert subprocess.run(["kill", "-0", tracked], check=False).returncode == 0
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        stopped = subprocess.run(
            ["sh", str(script), "stop", str(directory)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert stopped.returncode == 0, stopped.stderr
        _wait_until_closed(port)
        assert subprocess.run(["kill", "-0", tracked], check=False).returncode != 0
        repeated = subprocess.run(
            ["sh", str(script), "stop", str(directory)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert repeated.returncode == 0, repeated.stderr
    finally:
        if first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=3)
            except subprocess.TimeoutExpired:
                first.kill()
    missing = directory / "certs" / "server.pem"
    missing.unlink()
    failed = subprocess.run(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0
    assert not (directory / "host.pid").exists()
    assert subprocess.run(["sh", str(script), "stop", str(directory)], env=env, text=True, capture_output=True, check=False).returncode == 0
    missing.write_bytes(b"")
    # restore a usable certificate by preparing a fresh directory for restart
    restart_dir = tmp_path / "restart"
    restart_port = _port()
    assert subprocess.run(
        ["sh", str(script), "prepare-host", str(restart_dir), "127.0.0.1", str(restart_port)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0
    restarted = subprocess.Popen(
        ["sh", str(script), "host", str(restart_dir)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        new_pid = _wait_for_listener(restart_dir, restart_port, restarted)
        assert new_pid != tracked
        assert subprocess.run(["sh", str(script), "stop", str(restart_dir)], env=env, text=True, capture_output=True, check=False).returncode == 0
        _wait_until_closed(restart_port)
    finally:
        if restarted.poll() is None:
            restarted.terminate()
            restarted.wait(timeout=3)


def _wait_for_listener(directory: Path, port: int, process: subprocess.Popen[str]) -> str:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        pid_path = directory / "host.pid"
        if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip():
            pid_text = pid_path.read_text(encoding="utf-8").strip()
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return pid_text
            except OSError:
                pass
        if process.poll() is not None:
            raise AssertionError(process.stderr.read() if process.stderr else "host script exited")
        time.sleep(0.05)
    raise AssertionError("listener did not open the port")


def _wait_until_closed(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                time.sleep(0.05)
        except OSError:
            return
    raise AssertionError("port stayed open")


def test_host_leaves_a_live_unrelated_pid_and_replaces_a_dead_one(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "two-machine" / "two-machine.sh"
    directory = tmp_path / "pair"
    port = _port()
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prepared = subprocess.run(
        ["sh", str(script), "prepare-host", str(directory), "127.0.0.1", str(port)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    unrelated = subprocess.Popen(["sleep", "30"])
    (directory / "host.pid").write_text("%s\n" % unrelated.pid, encoding="utf-8")
    refused = subprocess.run(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode != 0
    assert "not this host" in refused.stderr
    assert (directory / "host.pid").read_text(encoding="utf-8").strip() == str(unrelated.pid)
    assert subprocess.run(["kill", "-0", str(unrelated.pid)], check=False).returncode == 0
    unrelated.kill()
    unrelated.wait(timeout=3)
    started = subprocess.Popen(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        tracked = _wait_for_listener(directory, port, started)
        assert tracked != str(unrelated.pid)
        stopped = subprocess.run(
            ["sh", str(script), "stop", str(directory)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert stopped.returncode == 0, stopped.stderr
        _wait_until_closed(port)
    finally:
        if started.poll() is None:
            started.terminate()
            started.wait(timeout=3)


def test_runtime_contexts_enable_strict_verification_when_the_flag_exists(tmp_path: Path) -> None:
    from agent_bus_mcp.remote import client_tls_context, server_tls_context, strict_verification_supported

    _certs(tmp_path)
    if not strict_verification_supported():
        raise AssertionError("VERIFY_X509_STRICT is absent in this Python; do not treat an openssl text dump as proof")
    flag = ssl.VERIFY_X509_STRICT
    default = ssl.create_default_context()
    client = client_tls_context(tmp_path / "ca.pem", tmp_path / "north.pem", tmp_path / "north.key")
    server = server_tls_context(tmp_path / "server.pem", tmp_path / "server.key", tmp_path / "ca.pem")
    assert client.verify_flags & flag, "north client context left VERIFY_X509_STRICT off; default is %s" % bool(default.verify_flags & flag)
    assert server.verify_flags & flag
    assert client.verify_mode == ssl.CERT_REQUIRED
    assert server.verify_mode == ssl.CERT_REQUIRED
    assert client.check_hostname is True
    assert client is not default


def test_script_tailscale_cgnat_requires_exact_opt_in(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "two-machine" / "two-machine.sh"
    reserved = {8443, 9443}

    def prepare(ip: str, flag: str | None, name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("AGENT_BUS_ALLOW_TAILSCALE", None)
        if flag is not None:
            env["AGENT_BUS_ALLOW_TAILSCALE"] = flag
        return subprocess.run(
            ["sh", str(script), "prepare-host", str(tmp_path / name), ip, "9443"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_refused(ip: str, flag: str | None, name: str, message: str) -> None:
        result = prepare(ip, flag, name)
        assert result.returncode == 2, result.stderr
        assert message in result.stderr
        assert not (tmp_path / name).exists()

    def assert_allowed(ip: str, flag: str | None, name: str) -> None:
        result = prepare(ip, flag, name)
        assert result.returncode == 0, result.stderr
        certificate = tmp_path / name / "certs" / "server.pem"
        san = subprocess.run(
            ["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert san.returncode == 0, san.stderr
        assert "IP Address:%s" % ip in san.stdout
        assert (tmp_path / name / "certs" / "ca.key").is_file()
        assert (tmp_path / name / "certs" / "server.key").is_file()
        assert not (tmp_path / name / "certs" / "alpha.key").exists()

    for ip in ("100.64.0.0", "100.100.1.1", "100.127.255.255"):
        assert_refused(ip, None, "no-" + ip.replace(".", "-"), "AGENT_BUS_ALLOW_TAILSCALE=1")
        assert_allowed(ip, "1", "yes-" + ip.replace(".", "-"))
    for flag in ("1", "yes", "true", ""):
        for ip in ("100.63.255.255", "100.128.0.0", "8.8.8.8", "0.0.0.0", "172.15.255.255", "172.32.0.0", "169.254.1.1", "203.0.113.5"):
            assert_refused(ip, flag if flag != "" else None, "bad-%s-%s" % (flag or "unset", ip.replace(".", "-")), "refusing non-private HOST_IP")
    for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1"):
        assert_allowed(ip, None, "private-" + ip.replace(".", "-"))
    assert_refused("100.100.1.1", "true", "word-true", "AGENT_BUS_ALLOW_TAILSCALE=1")
    assert_refused("not-an-address", "1", "hostname", "refusing non-private HOST_IP")

    directory = tmp_path / "host-gate"
    port = _port()
    assert port not in reserved
    prepared = subprocess.run(
        ["sh", str(script), "prepare-host", str(directory), "127.0.0.1", str(port)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    (directory / "host_ip").write_text("8.8.8.8\n", encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_BUS_ALLOW_TAILSCALE"] = "1"
    refused = subprocess.run(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "refusing non-private HOST_IP" in refused.stderr
    assert not (directory / "host.pid").exists()
    (directory / "host_ip").write_text("100.100.1.1\n", encoding="utf-8")
    env.pop("AGENT_BUS_ALLOW_TAILSCALE")
    tailnet = subprocess.run(
        ["sh", str(script), "host", str(directory)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert tailnet.returncode == 2
    assert "100.64.0.0/10 only" in tailnet.stderr
    assert not (directory / "host.pid").exists()


def _client_env(env: dict[str, str], directory: Path, port: int, endpoint: str) -> dict[str, str]:
    child = env.copy()
    child["AGENT_BUS_REMOTE_URL"] = f"https://127.0.0.1:{port}"
    child["AGENT_BUS_CLIENT_CERT"] = str(directory / f"{endpoint}.pem")
    child["AGENT_BUS_CLIENT_KEY"] = str(directory / f"{endpoint}.key")
    return child


def _command(env: dict[str, str], directory: Path, port: int, endpoint: str, tool: str, request_id: str, **flags: object) -> list[str]:
    command = [sys.executable, "-m", "agent_bus_mcp.remote_call", "--tool", tool, "--request-id", request_id, "--timeout", "5"]
    for name, value in flags.items():
        command.extend(["--" + name.replace("_", "-"), str(value)])
    return command
