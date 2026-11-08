"""Connected-client wait_available. These are real TLS calls, not a stopped-agent wake."""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_bus_mcp.availability import bump, generation, release, try_reserve, wait_for_change, waiter_cap
from agent_bus_mcp.queue import QueueRefused
from agent_bus_mcp.remote import call_remote

from test_remote import _call, _certs, _env, _enroll, _port, _server, _stop, _wait_ready


def _timing(name: str, started: float) -> float:
    elapsed = time.monotonic() - started
    print("WAIT_TIMING %s %.3f" % (name, elapsed), flush=True)
    return elapsed


def _graph(directory: Path) -> Path:
    path = directory / "endpoints.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": ["north", "south", "witness"],
                "allow": [
                    {"from": "south", "to": "north", "kinds": ["handoff"]},
                    {"from": "north", "to": "south", "kinds": ["handoff"]},
                    {"from": "witness", "to": "north", "kinds": ["handoff"]},
                ],
                "boards": [
                    {"id": "room", "members": ["north", "south"]},
                    {"id": "hall", "members": ["north", "witness"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _host(tmp: Path, cap: int | None = None):
    _certs(tmp)
    config = _graph(tmp)
    env = _env(tmp)
    for endpoint in ("north", "south", "witness"):
        _enroll(env, tmp, endpoint)
    state = tmp / "state"
    port = _port()
    extra = {"AGENT_BUS_WAIT_CAP": str(cap)} if cap is not None else None
    process = _server(env, tmp, state, config, port, extra)
    _wait_ready(env, tmp, port, process)
    return env, state, port, process


def _wait(
    env: dict[str, str],
    directory: Path,
    port: int,
    endpoint: str,
    request_id: str,
    seconds: int,
    board: str | None = None,
    http_timeout: float | None = None,
    background: bool = False,
):
    child = env.copy()
    child["AGENT_BUS_REMOTE_URL"] = "https://127.0.0.1:%s" % port
    child["AGENT_BUS_CLIENT_CERT"] = str(directory / ("%s.pem" % endpoint))
    child["AGENT_BUS_CLIENT_KEY"] = str(directory / ("%s.key" % endpoint))
    command = [
        sys.executable, "-m", "agent_bus_mcp.remote_call",
        "--tool", "wait_available",
        "--request-id", request_id,
        "--wait-seconds", str(seconds),
    ]
    if board is not None:
        command.extend(["--board", board])
    if http_timeout is not None:
        command.extend(["--timeout", str(http_timeout)])
    if background:
        return subprocess.Popen(command, env=child, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.run(command, env=child, text=True, capture_output=True, check=False)


def _finish(process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
    try:
        stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    code = process.returncode
    return subprocess.CompletedProcess(process.args, 1 if code is None else code, stdout, stderr)


def _body(completed: subprocess.CompletedProcess[str]) -> dict:
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert set(result) == {"status", "unread_boards", "claimable", "holding_lease"}
    assert "text" not in result and "goal" not in result and "lease_id" not in result and "task_id" not in result
    return result


def _no_wait_record(state: Path, endpoint: str, request_id: str) -> None:
    path = state / "remote_calls" / endpoint / "results" / request_id
    assert not path.exists()
    claims = state / "remote_claims" / endpoint / request_id
    assert not claims.exists()


def test_cap_is_one_per_endpoint_and_thirty_two_global() -> None:
    assert waiter_cap() == 32
    held: list[str] = []
    try:
        for index in range(32):
            name = "endpoint-%s" % index
            assert try_reserve(name)
            held.append(name)
        assert try_reserve("endpoint-overflow") is False
        release(held.pop())
        assert try_reserve("endpoint-overflow") is True
        held.append("endpoint-overflow")
    finally:
        for name in held:
            release(name)
        assert try_reserve("endpoint-after") is True
        release("endpoint-after")


def test_generation_is_not_lost_before_the_wait() -> None:
    try_reserve("race-endpoint")
    try:
        samples = []
        for _ in range(20):
            ticket = generation("race-endpoint")
            bump("race-endpoint")
            started = time.monotonic()
            wait_for_change("race-endpoint", ticket, 2)
            samples.append(_timing("generation", started))
        assert max(samples) < 0.2
    finally:
        release("race-endpoint")


def test_ready_is_level_triggered_and_a_timeout_does_not_ack(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        posted = _call(env, tmp_path, port, "south", "post_board", "post-1", board="room", text="room-marker-text", key="talk")
        assert posted.returncode == 0, posted.stderr
        started = time.monotonic()
        first = _wait(env, tmp_path, port, "north", "wait-ready-1", 5)
        ready_elapsed = _timing("level-ready", started)
        hint = _body(first)
        assert hint["status"] == "ready" and hint["unread_boards"] == ["room"]
        assert hint["claimable"] is False and hint["holding_lease"] is False
        assert ready_elapsed < 1
        assert "room-marker-text" not in first.stdout
        started = time.monotonic()
        second = _wait(env, tmp_path, port, "north", "wait-ready-2", 5)
        again = _timing("level-again", started)
        assert _body(second)["unread_boards"] == ["room"] and again < 1
        ack = state / "boards" / "room" / "acks" / "north.json"
        assert not ack.exists()
        _no_wait_record(state, "north", "wait-ready-1")
        started = time.monotonic()
        quiet = _wait(env, tmp_path, port, "south", "wait-timeout", 1)
        timeout_elapsed = _timing("timeout-1s", started)
        assert _body(quiet) == {
            "status": "timeout",
            "unread_boards": [],
            "claimable": False,
            "holding_lease": False,
        }
        assert 0.8 <= timeout_elapsed < 2.0
        assert not ack.exists()
        _no_wait_record(state, "south", "wait-timeout")
    finally:
        _stop(server)


def test_board_filter_limits_unread_names_and_not_own_task_flags(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        hall = _call(env, tmp_path, port, "witness", "post_board", "hall-post", board="hall", text="hall-marker-text", key="hall")
        assert hall.returncode == 0, hall.stderr
        room = _call(env, tmp_path, port, "south", "post_board", "room-post", board="room", text="room-marker-text", key="room")
        assert room.returncode == 0, room.stderr
        room_id = json.loads(room.stdout)["post_id"]
        filtered = _body(_wait(env, tmp_path, port, "north", "wait-room", 2, board="room"))
        assert filtered["status"] == "ready" and filtered["unread_boards"] == ["room"]
        assert "hall" not in filtered["unread_boards"]
        assert "hall-marker-text" not in json.dumps(filtered)
        promoted = _call(
            env, tmp_path, port, "south", "promote_board", "promote-1",
            board="room", post=room_id, key="own", to="north",
        )
        assert promoted.returncode == 0, promoted.stderr
        hall_only = _body(_wait(env, tmp_path, port, "north", "wait-hall", 2, board="hall"))
        assert hall_only["unread_boards"] == ["hall"]
        assert hall_only["claimable"] is True
        assert "room-marker-text" not in json.dumps(hall_only)
        stranger = _wait(env, tmp_path, port, "witness", "wait-stranger", 1, board="room")
        assert stranger.returncode == 2
        assert "room-marker-text" not in stranger.stdout + stranger.stderr
        assert "hall-marker-text" not in stranger.stdout + stranger.stderr
        assert "unread_boards" not in stranger.stdout
        _no_wait_record(state, "north", "wait-room")
    finally:
        _stop(server)


def test_post_during_wait_wakes_without_losing_the_signal(tmp_path: Path) -> None:
    env, _state, port, server = _host(tmp_path)
    try:
        samples = []
        for index in range(8):
            waiter = _wait(env, tmp_path, port, "north", "wake-%s" % index, 8, background=True)
            time.sleep(0.15)
            started = time.monotonic()
            posted = _call(
                env, tmp_path, port, "south", "post_board", "wake-post-%s" % index,
                board="room", text="ping-%s" % index, key="ping-%s" % index,
            )
            assert posted.returncode == 0, posted.stderr
            finished = _finish(waiter)
            samples.append(_timing("wake-%s" % index, started))
            hint = _body(finished)
            assert hint["status"] == "ready" and hint["unread_boards"] == ["room"]
            assert "ping-%s" % index not in finished.stdout
            acked = _call(env, tmp_path, port, "north", "ack_board", "ack-%s" % index, board="room", posts=json.loads(posted.stdout)["post_id"])
            assert acked.returncode == 0, acked.stderr
        assert max(samples) < 1.2
    finally:
        _stop(server)


def test_ordinary_calls_and_claim_proceed_while_a_waiter_is_parked(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        waiter = _wait(env, tmp_path, port, "north", "parked", 6, background=True)
        time.sleep(0.2)
        started = time.monotonic()
        polled = _call(env, tmp_path, port, "south", "poll_unread", "poll-while", board="room")
        poll_elapsed = _timing("poll-while-waiting", started)
        assert polled.returncode == 0, polled.stderr
        assert poll_elapsed < 1
        assert waiter.poll() is None
        posted = _call(env, tmp_path, port, "south", "post_board", "work-post", board="room", text="room-marker-text", key="work")
        assert posted.returncode == 0, posted.stderr
        post_id = json.loads(posted.stdout)["post_id"]
        started = time.monotonic()
        promoted = _call(
            env, tmp_path, port, "south", "promote_board", "work-own",
            board="room", post=post_id, key="own", to="north",
        )
        promote_elapsed = _timing("promote-while-waiting", started)
        assert promoted.returncode == 0, promoted.stderr
        assert promote_elapsed < 1.5
        hint = _body(_finish(waiter))
        assert "room" in hint["unread_boards"] or hint["claimable"] is True
        assert "room-marker-text" not in json.dumps(hint)
        started = time.monotonic()
        claimed = _call(env, tmp_path, port, "north", "claim_task", "work-claim")
        claim_elapsed = _timing("claim-after-hint", started)
        assert claimed.returncode == 0 and json.loads(claimed.stdout)["status"] == "claimed"
        assert claim_elapsed < 1.5
        resume = _call(env, tmp_path, port, "south", "resume_leases", "south-leases")
        assert json.loads(resume.stdout)["leases"] == []
        _no_wait_record(state, "north", "parked")
    finally:
        if "waiter" in locals() and waiter.poll() is None:
            waiter.kill()
        _stop(server)


def test_second_wait_is_refused_and_disconnect_releases_the_slot(tmp_path: Path) -> None:
    """A killed client frees its slot on a later look, not by extending the deadline.

    The server sleeps at most five seconds between looks, on its own clock, and
    that sleep is not aligned to the kill. This measures when the next call is
    accepted and, separately, how long that call's own one-second deadline runs.
    Acceptance within 6.5s of the kill is loopback tolerance around one slice.
    It is not a five-second wall-clock promise measured from the kill.
    """
    env, state, port, server = _host(tmp_path)
    parked_at = time.monotonic()
    first = _wait(env, tmp_path, port, "north", "slot-1", 8, background=True)
    try:
        time.sleep(0.25)
        started = time.monotonic()
        refused = _wait(env, tmp_path, port, "north", "slot-2", 8)
        refused_elapsed = _timing("second-wait-refused", started)
        assert refused.returncode == 2
        assert refused_elapsed < 0.5
        assert "unread_boards" not in refused.stdout
        assert first.poll() is None
        killed_at = time.monotonic()
        first.kill()
        accepted = None
        accepted_at = None
        probe_duration = None
        last_refusal = None
        while time.monotonic() - killed_at < 7.5:
            probe_started = time.monotonic()
            probe = _wait(env, tmp_path, port, "north", "slot-3", 1)
            probe_elapsed = time.monotonic() - probe_started
            if probe.returncode == 2 and probe_elapsed < 0.5:
                last_refusal = probe_started - killed_at
                assert "unread_boards" not in probe.stdout
                continue
            if probe.returncode == 0:
                accepted = probe
                accepted_at = probe_started
                probe_duration = probe_elapsed
                break
            raise AssertionError("unexpected wait result %s after %.3fs" % (probe.returncode, probe_elapsed))
        assert accepted is not None and accepted_at is not None and probe_duration is not None
        kill_to_accept = accepted_at - killed_at
        print("WAIT_TIMING kill-to-accept %.3f" % kill_to_accept, flush=True)
        print("WAIT_TIMING followup-1s-deadline %.3f" % probe_duration, flush=True)
        if last_refusal is not None:
            print("WAIT_TIMING last-refusal-after-kill %.3f" % last_refusal, flush=True)
        assert _body(accepted)["status"] == "timeout"
        assert 0.8 <= probe_duration < 2.0
        assert kill_to_accept < 6.5
        assert accepted_at - parked_at < 7.0
        _no_wait_record(state, "north", "slot-1")
    finally:
        if first.poll() is None:
            first.kill()
        _stop(server)


def test_full_cap_does_not_block_a_ready_hint_or_a_normal_call(tmp_path: Path) -> None:
    env, _state, port, server = _host(tmp_path, cap=2)
    south = witness = None
    try:
        posted = _call(env, tmp_path, port, "witness", "post_board", "hall-ready", board="hall", text="hall-marker-text", key="hall")
        assert posted.returncode == 0, posted.stderr
        south = _wait(env, tmp_path, port, "south", "cap-south", 6, background=True)
        witness = _wait(env, tmp_path, port, "witness", "cap-witness", 6, background=True)
        time.sleep(0.25)
        started = time.monotonic()
        ready = _wait(env, tmp_path, port, "north", "cap-ready", 5)
        ready_elapsed = _timing("ready-while-cap-full", started)
        hint = _body(ready)
        assert hint["unread_boards"] == ["hall"] and ready_elapsed < 1
        assert "hall-marker-text" not in ready.stdout
        started = time.monotonic()
        polled = _call(env, tmp_path, port, "north", "poll_unread", "poll-capped", board="hall")
        poll_elapsed = _timing("poll-while-cap-full", started)
        assert polled.returncode == 0 and poll_elapsed < 1
        post_id = json.loads(posted.stdout)["post_id"]
        acked = _call(env, tmp_path, port, "north", "ack_board", "ack-hall", board="hall", posts=post_id)
        assert acked.returncode == 0, acked.stderr
        started = time.monotonic()
        blocked = _wait(env, tmp_path, port, "north", "cap-blocked", 5)
        blocked_elapsed = _timing("third-waiter-refused", started)
        assert blocked.returncode == 2 and blocked_elapsed < 0.5
        assert "hall-marker-text" not in blocked.stdout + blocked.stderr
    finally:
        for process in (south, witness):
            if process is not None and process.poll() is None:
                process.kill()
        _stop(server)


def test_revocation_during_wait_returns_no_hint(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        posted = _call(env, tmp_path, port, "south", "post_board", "hidden-post", board="room", text="room-marker-text", key="hidden")
        assert posted.returncode == 0, posted.stderr
        # South has no unread of its own post, so this wait stays parked.
        waiter = _wait(env, tmp_path, port, "south", "revoke-wait", 8, background=True)
        time.sleep(0.25)
        started = time.monotonic()
        revoked = subprocess.run(
            [
                sys.executable, "-m", "agent_bus_mcp.enroll", "revoke",
                "--file", str(tmp_path / "enrollment.json"),
                "--endpoint", "south",
            ],
            env=env, text=True, capture_output=True, check=False,
        )
        assert revoked.returncode == 0, revoked.stderr
        finished = _finish(waiter)
        elapsed = _timing("revoke-during-wait", started)
        assert finished.returncode == 2
        assert elapsed < 6
        leaked = finished.stdout + finished.stderr
        assert "room-marker-text" not in leaked
        assert "unread_boards" not in leaked
        assert "room" not in leaked
        other = _call(env, tmp_path, port, "north", "poll_unread", "north-still", board="room")
        assert other.returncode == 0 and "room-marker-text" in other.stdout
        _no_wait_record(state, "south", "revoke-wait")
        ack = state / "boards" / "room" / "acks" / "south.json"
        assert not ack.exists()
    finally:
        _stop(server)


def test_recheck_sees_a_lease_expiry_without_a_sweeper(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        waiter = _wait(env, tmp_path, port, "north", "expire-wait", 10, background=True)
        time.sleep(0.4)
        assert waiter.poll() is None
        expires = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
        stamp = expires.isoformat().replace("+00:00", "Z")
        task_id = "task_" + ("ab" * 16)
        record = {
            "task_id": task_id,
            "producer_id": "south",
            "worker_id": "north",
            "goal": "hidden-goal",
            "references": [],
            "status": "leased",
            "lease_id": "lease_" + ("cd" * 16),
            "lease_expires_at": stamp,
            "result": None,
        }
        tasks = state / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / ("%s.json" % task_id)).write_text(json.dumps(record), encoding="utf-8")
        started = time.monotonic()
        finished = _finish(waiter)
        elapsed = _timing("expiry-recheck", started)
        hint = _body(finished)
        assert hint["status"] == "ready" and hint["claimable"] is True and hint["holding_lease"] is False
        assert "hidden-goal" not in finished.stdout
        assert elapsed < 6.5
        _no_wait_record(state, "north", "expire-wait")
    finally:
        _stop(server)


def test_restart_drops_the_wait_and_keeps_durable_unread(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    waiter = _wait(env, tmp_path, port, "north", "restart-wait", 8, background=True)
    try:
        time.sleep(0.2)
        assert waiter.poll() is None
        _stop(server)
        finished = _finish(waiter)
        assert finished.returncode != 0
        assert not (state / "boards" / "room" / "acks" / "north.json").exists()
        _no_wait_record(state, "north", "restart-wait")
    finally:
        if waiter.poll() is None:
            waiter.kill()
    refused = _call(env, tmp_path, port, "south", "post_board", "after-stop", board="room", text="room-marker-text", key="later")
    assert refused.returncode != 0
    server = _server(env, tmp_path, state, tmp_path / "endpoints.json", port)
    try:
        _wait_ready(env, tmp_path, port, server)
        created = _call(env, tmp_path, port, "south", "post_board", "survives", board="room", text="room-marker-text", key="survives")
        assert created.returncode == 0, created.stderr
        _stop(server)
        server = _server(env, tmp_path, state, tmp_path / "endpoints.json", port)
        _wait_ready(env, tmp_path, port, server)
        started = time.monotonic()
        again = _wait(env, tmp_path, port, "north", "after-restart", 5)
        elapsed = _timing("ready-after-restart", started)
        assert _body(again)["unread_boards"] == ["room"] and elapsed < 1
        assert not (state / "boards" / "room" / "acks" / "north.json").exists()
    finally:
        _stop(server)


def test_same_request_id_is_not_a_stored_wait_result(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        first = _body(_wait(env, tmp_path, port, "north", "same-id", 1))
        assert first["status"] == "timeout"
        posted = _call(env, tmp_path, port, "south", "post_board", "same-post", board="room", text="room-marker-text", key="same")
        assert posted.returncode == 0, posted.stderr
        second = _body(_wait(env, tmp_path, port, "north", "same-id", 2))
        assert second["status"] == "ready" and second["unread_boards"] == ["room"]
        _no_wait_record(state, "north", "same-id")
    finally:
        _stop(server)


def test_callback_arguments_are_refused_and_cli_extends_the_http_timeout(tmp_path: Path) -> None:
    env, state, port, server = _host(tmp_path)
    try:
        try:
            call_remote(
                url="https://127.0.0.1:%s" % port,
                ca=tmp_path / "ca.pem",
                certificate=tmp_path / "north.pem",
                key=tmp_path / "north.key",
                tool="wait_available",
                arguments={"callback": "https://example.invalid/hook", "timeout_seconds": 1},
                request_id="callback-1",
                timeout=3,
            )
        except QueueRefused:
            pass
        else:
            raise AssertionError("callback argument was accepted")
        blob = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in state.rglob("*") if path.is_file())
        assert "example.invalid" not in blob
        started = time.monotonic()
        extended = _wait(env, tmp_path, port, "north", "http-extended", 6)
        elapsed = _timing("cli-timeout-extends-past-default-5s", started)
        assert _body(extended)["status"] == "timeout"
        assert elapsed >= 5.5
        assert elapsed < 8
    finally:
        _stop(server)


def test_direct_file_write_expiry_is_claimable_on_recheck_not_only_at_deadline(tmp_path: Path) -> None:
    # Covered by test_recheck_sees_a_lease_expiry_without_a_sweeper. This locks the
    # deadline: a one-second wait must not grow into the five-second recheck slice.
    env, _state, port, server = _host(tmp_path)
    try:
        started = time.monotonic()
        result = _wait(env, tmp_path, port, "witness", "short-deadline", 1, board="hall")
        elapsed = _timing("deadline-not-extended", started)
        assert _body(result)["status"] == "timeout"
        assert elapsed < 2
    finally:
        _stop(server)
