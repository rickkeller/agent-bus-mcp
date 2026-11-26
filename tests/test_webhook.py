"""Opt-in webhook wake. A local HTTP server stands in for the remote agent."""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_bus_mcp import webhook
from agent_bus_mcp.endpoint import main as endpoint_main
from agent_bus_mcp.endpoints import open_endpoint, post_board, promote_board
from agent_bus_mcp.queue import QueueRefused

SECRET = "test-signing-secret-not-real"


class _Receiver:
    def __init__(self, status: int = 200, delay: float = 0.0) -> None:
        self.status = status
        self.delay = delay
        self.calls: list[dict] = []
        self.arrived = threading.Event()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.calls.append(
                    {"path": self.path, "headers": dict(self.headers), "body": body, "at": time.perf_counter()}
                )
                if receiver.delay:
                    time.sleep(receiver.delay)
                try:
                    self.send_response(receiver.status)
                    self.end_headers()
                except OSError:
                    pass
                receiver.arrived.set()

            def log_message(self, *args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/hook?token=query-secret" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, count: int = 1, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.calls) >= count:
                return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def receiver():
    made = _Receiver()
    yield made
    made.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(webhook, "BACKOFF_SECONDS", (0.05, 0.05, 0.05))
    monkeypatch.setattr(webhook, "TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(webhook, "COALESCE_SECONDS", 0.0)
    yield
    assert webhook.flush(10)


def _config(tmp_path: Path, hooks: list[dict] | None) -> Path:
    secret = tmp_path / "hook.secret"
    secret.write_text(SECRET + "\n", encoding="utf-8")
    document = {
        "endpoints": ["poster", "sleeper", "outsider"],
        "allow": [
            {"from": "poster", "to": "sleeper", "kinds": ["chat", "handoff"]},
            {"from": "sleeper", "to": "poster", "kinds": ["chat", "handoff"]},
        ],
        "boards": [{"id": "pair", "members": ["poster", "sleeper"]}],
    }
    if hooks is not None:
        for hook in hooks:
            hook.setdefault("secret_file", str(secret))
        document["webhooks"] = hooks
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _hook(endpoint: str, url: str, events=("board_post", "handoff")) -> dict:
    return {"endpoint": endpoint, "url": url, "events": list(events)}


def test_no_webhooks_key_means_no_request(tmp_path: Path, receiver: _Receiver) -> None:
    config = _config(tmp_path, None)
    poster = open_endpoint(tmp_path / "s", config, "poster")
    post_board(poster, config=config, board="pair", text="hello", key="k1")
    assert webhook.flush(5)
    assert receiver.calls == []


def test_post_wakes_the_member_signed_and_without_text(tmp_path: Path, receiver: _Receiver) -> None:
    config = _config(tmp_path, [_hook("sleeper", receiver.url)])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    started = time.perf_counter()
    posted = post_board(poster, config=config, board="pair", text="very private words", key="k1")
    returned = time.perf_counter()
    assert receiver.wait(1)
    call = receiver.calls[0]
    assert returned - started < 0.5
    body = call["body"]
    payload = json.loads(body)
    assert set(payload) == {
        "event", "endpoint", "board", "post_id", "seq", "author", "timestamp", "delivery_id", "coalesced"
    }
    assert payload["event"] == "board_post"
    assert payload["endpoint"] == "sleeper"
    assert payload["board"] == "pair"
    assert payload["post_id"] == posted["post_id"]
    assert payload["seq"] == posted["seq"]
    assert payload["author"] == "poster"
    assert payload["coalesced"] == 1
    assert b"very private words" not in body
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert webhook.verify(SECRET, body, headers["x-agent-bus-signature"])
    assert not webhook.verify("wrong", body, headers["x-agent-bus-signature"])
    assert not webhook.verify(SECRET, body + b" ", headers["x-agent-bus-signature"])
    assert headers["x-agent-bus-timestamp"] == payload["timestamp"]
    assert headers["x-agent-bus-event"] == "board_post"
    assert headers["x-agent-bus-delivery"] == payload["delivery_id"]
    assert "authorization" not in headers
    assert call["path"] == "/hook?token=query-secret"


def test_author_and_non_members_are_not_woken(tmp_path: Path, receiver: _Receiver) -> None:
    other = _Receiver()
    try:
        config = _config(tmp_path, [_hook("poster", receiver.url), _hook("outsider", other.url)])
        poster = open_endpoint(tmp_path / "s", config, "poster")
        post_board(poster, config=config, board="pair", text="mine", key="k1")
        assert webhook.flush(5)
        assert receiver.calls == []
        assert other.calls == []
    finally:
        other.close()


def test_events_filter_and_handoff_wakes_only_the_destination(tmp_path: Path, receiver: _Receiver) -> None:
    config = _config(tmp_path, [_hook("sleeper", receiver.url, events=["handoff"])])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    posted = post_board(poster, config=config, board="pair", text="please own this", key="k1")
    assert webhook.flush(5)
    assert receiver.calls == []
    task_id = promote_board(poster, config=config, board="pair", post_id=posted["post_id"], key="p1", to="sleeper")
    assert receiver.wait(1)
    payload = json.loads(receiver.calls[0]["body"])
    assert payload["event"] == "handoff"
    assert payload["task_id"] == task_id
    assert payload["post_id"] == posted["post_id"]
    assert payload["author"] == "poster"
    assert payload["endpoint"] == "sleeper"
    assert "text" not in payload and "goal" not in payload
    # The same promote key does not wake again.
    promote_board(poster, config=config, board="pair", post_id=posted["post_id"], key="p1", to="sleeper")
    assert webhook.flush(5)
    assert len(receiver.calls) == 1


def test_same_post_key_does_not_wake_twice(tmp_path: Path, receiver: _Receiver) -> None:
    config = _config(tmp_path, [_hook("sleeper", receiver.url)])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    post_board(poster, config=config, board="pair", text="once", key="k1")
    assert receiver.wait(1)
    post_board(poster, config=config, board="pair", text="once", key="k1")
    assert webhook.flush(5)
    assert len(receiver.calls) == 1


def test_bearer_header_is_optional(tmp_path: Path, receiver: _Receiver, monkeypatch) -> None:
    monkeypatch.setenv("TEST_HOOK_BEARER", "static-bearer")
    hook = _hook("sleeper", receiver.url)
    hook["bearer_env"] = "TEST_HOOK_BEARER"
    config = _config(tmp_path, [hook])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    post_board(poster, config=config, board="pair", text="x", key="k1")
    assert receiver.wait(1)
    headers = {k.lower(): v for k, v in receiver.calls[0]["headers"].items()}
    assert headers["authorization"] == "Bearer static-bearer"


def test_a_dead_url_does_not_break_or_slow_the_post(tmp_path: Path, caplog) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = "http://127.0.0.1:%d/hook?token=query-secret" % port
    config = _config(tmp_path, [_hook("sleeper", url)])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    caplog.set_level(logging.INFO, logger="agent_bus_mcp.webhook")
    started = time.perf_counter()
    posted = post_board(poster, config=config, board="pair", text="still written", key="k1")
    assert time.perf_counter() - started < 0.5
    assert posted["seq"] == 1
    assert webhook.flush(5)
    text = caplog.text
    assert "gave up after 4 attempts" in text
    assert "query-secret" not in text and "/hook" not in text and SECRET not in text


def test_a_slow_receiver_times_out_and_retries_server_errors(tmp_path: Path, caplog) -> None:
    slow = _Receiver(delay=1.0)
    failing = _Receiver(status=503)
    try:
        caplog.set_level(logging.INFO, logger="agent_bus_mcp.webhook")
        config = _config(tmp_path, [_hook("sleeper", slow.url)])
        poster = open_endpoint(tmp_path / "s", config, "poster")
        started = time.perf_counter()
        post_board(poster, config=config, board="pair", text="a", key="k1")
        assert time.perf_counter() - started < 0.5
        assert webhook.flush(10)
        assert len(slow.calls) == 4  # first try plus three retries, each timed out
        config = _config(tmp_path, [_hook("sleeper", failing.url)])
        post_board(poster, config=config, board="pair", text="b", key="k2")
        assert webhook.flush(10)
        assert len(failing.calls) == 4
        ids = {json.loads(call["body"])["delivery_id"] for call in failing.calls}
        assert len(ids) == 1  # retries keep one delivery id
    finally:
        slow.close()
        failing.close()


def test_a_client_error_is_not_retried(tmp_path: Path) -> None:
    refused = _Receiver(status=401)
    try:
        config = _config(tmp_path, [_hook("sleeper", refused.url)])
        poster = open_endpoint(tmp_path / "s", config, "poster")
        post_board(poster, config=config, board="pair", text="a", key="k1")
        assert webhook.flush(5)
        assert len(refused.calls) == 1
    finally:
        refused.close()


def test_a_burst_is_coalesced(tmp_path: Path, monkeypatch) -> None:
    slow = _Receiver(delay=0.3)
    try:
        config = _config(tmp_path, [_hook("sleeper", slow.url)])
        poster = open_endpoint(tmp_path / "s", config, "poster")
        for index in range(6):
            post_board(poster, config=config, board="pair", text="n%d" % index, key="k%d" % index)
        assert webhook.flush(10)
        assert 1 <= len(slow.calls) < 6
        payloads = [json.loads(call["body"]) for call in slow.calls]
        assert sum(item["coalesced"] for item in payloads) == 6
        assert payloads[-1]["seq"] == 6
    finally:
        slow.close()


@pytest.mark.parametrize(
    "hook, reason",
    [
        ({"endpoint": "sleeper", "url": "http://hooks.example.com/x", "events": ["board_post"]}, "url must be https"),
        ({"endpoint": "nobody", "url": "https://hooks.example.com/x", "events": ["board_post"]}, "not a configured"),
        ({"endpoint": "sleeper", "url": "https://hooks.example.com/x", "events": ["chat"]}, "events must name"),
        ({"endpoint": "sleeper", "url": "https://u:p@hooks.example.com/x", "events": ["board_post"]}, "url must be https"),
        ({"endpoint": "sleeper", "url": "https://hooks.example.com/x", "events": ["handoff"], "extra": 1}, "unknown keys"),
    ],
)
def test_bad_webhook_config_is_refused_at_start(tmp_path: Path, hook: dict, reason: str) -> None:
    config = _config(tmp_path, [hook])
    with pytest.raises(QueueRefused, match=reason):
        webhook.check_config(config)


def test_http_needs_loopback_or_allow_http_and_a_readable_secret(tmp_path: Path) -> None:
    hook = {"endpoint": "sleeper", "url": "http://10.0.0.5/x", "events": ["board_post"], "allow_http": True}
    webhook.check_config(_config(tmp_path, [hook]))
    webhook.check_config(_config(tmp_path, [{"endpoint": "sleeper", "url": "http://localhost:9/x", "events": ["handoff"]}]))
    missing = {"endpoint": "sleeper", "url": "https://hooks.example.com/x", "events": ["handoff"], "secret_env": "UNSET_SECRET_FOR_TEST"}
    document = json.loads(_config(tmp_path, None).read_text())
    document["webhooks"] = [missing]
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(document))
    with pytest.raises(QueueRefused, match="no readable secret"):
        webhook.check_config(path)


def test_one_shot_command_flushes_before_exit(tmp_path: Path, receiver: _Receiver, monkeypatch, capsys) -> None:
    config = _config(tmp_path, [_hook("sleeper", receiver.url)])
    monkeypatch.setenv("AGENT_BUS_STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("AGENT_BUS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_ID", "poster")
    monkeypatch.delenv("AGENT_BUS_NATS_URL", raising=False)
    assert endpoint_main(["post-board", "--board", "pair", "--text", "cli", "--key", "c1"]) == 0
    assert len(receiver.calls) == 1


def test_local_fire_latency(tmp_path: Path, receiver: _Receiver, monkeypatch) -> None:
    monkeypatch.setattr(webhook, "COALESCE_SECONDS", 0.05)  # the shipped default
    config = _config(tmp_path, [_hook("sleeper", receiver.url)])
    poster = open_endpoint(tmp_path / "s", config, "poster")
    samples = []
    for index in range(5):
        before = len(receiver.calls)
        started = time.perf_counter()
        post_board(poster, config=config, board="pair", text="t", key="lat%d" % index)
        assert receiver.wait(before + 1)
        samples.append((receiver.calls[-1]["at"] - started) * 1000)
    print("local webhook latency ms:", ", ".join("%.1f" % value for value in samples))
    assert max(samples) < 1000


def test_doctor_reports_webhooks_without_the_url(tmp_path: Path) -> None:
    from agent_bus_mcp.setup import _webhook_check

    good = json.loads(_config(tmp_path, [_hook("sleeper", "https://hooks.example.com/p/tok?q=secret")]).read_text())
    check = _webhook_check(good)
    assert check.ok and check.detail == "sleeper"
    bad = json.loads(_config(tmp_path, [_hook("sleeper", "http://hooks.example.com/p/tok?q=secret")]).read_text())
    check = _webhook_check(bad)
    assert not check.ok and "tok" not in check.detail and "secret" not in check.detail


def test_stripe_style_signature_with_a_custom_header(tmp_path: Path, receiver: _Receiver) -> None:
    hook = _hook("sleeper", receiver.url)
    hook["signature_format"] = "stripe"
    hook["signature_header"] = "X-Receiver-Signature"
    config = _config(tmp_path, [hook])
    webhook.check_config(config)
    poster = open_endpoint(tmp_path / "s", config, "poster")
    post_board(poster, config=config, board="pair", text="secret words", key="k1")
    assert receiver.wait(1)
    call = receiver.calls[0]
    body = call["body"]
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert "x-agent-bus-signature" not in headers
    value = headers["x-receiver-signature"]
    stamp, mac = value.split(",")
    assert stamp.startswith("t=") and mac.startswith("v1=")
    assert abs(int(stamp[2:]) - time.time()) < 30
    # Independent recomputation of HMAC-SHA256 over "<t>.<body>".
    import hashlib
    import hmac as hmac_module

    expected = hmac_module.new(SECRET.encode(), stamp[2:].encode() + b"." + body, hashlib.sha256).hexdigest()
    assert mac[3:] == expected
    assert webhook.verify_stripe(SECRET, body, value)
    assert not webhook.verify_stripe(SECRET, body + b" ", value)
    assert not webhook.verify_stripe("wrong", body, value)
    assert b"secret words" not in body


@pytest.mark.parametrize(
    "extra, reason",
    [
        ({"signature_format": "github"}, "signature_format must be"),
        ({"signature_header": "Bad Header"}, "signature_header is not"),
        ({"signature_header": "Authorization"}, "signature_header is not"),
        ({"signature_header": "X-Agent-Bus-Delivery"}, "signature_header is not"),
    ],
)
def test_bad_signature_settings_are_refused(tmp_path: Path, extra: dict, reason: str) -> None:
    hook = {"endpoint": "sleeper", "url": "https://hooks.example.com/x", "events": ["board_post"], **extra}
    with pytest.raises(QueueRefused, match=reason):
        webhook.check_config(_config(tmp_path, [hook]))
