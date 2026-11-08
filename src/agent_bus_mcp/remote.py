"""TLS host for one state directory. Each client certificate is one endpoint.

Plaintext HTTP is refused. Enrollment is a local file, not a remote tool.
"""

from __future__ import annotations

import argparse
import fcntl
import select
import sys
import hashlib
import ipaddress
import json
import logging
import os
import socket
import ssl
import time
from collections.abc import Callable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from .availability import release, try_reserve, wait_for_change
from .endpoints import (
    ack_board,
    availability_hint,
    open_endpoint,
    poll_unread,
    post_board,
    promote_board,
    read_board_page,
)
from .queue import DurableQueue, QueueRefused, _caller_key

MAX_BODY_BYTES = 16 * 1024
_LOG = logging.getLogger("agent_bus_mcp.remote")
_IDENTITY_FIELDS = {
    "agent_id",
    "author",
    "destination_id",
    "endpoint",
    "endpoint_id",
    "from",
    "producer_id",
    "source",
    "source_id",
    "worker_id",
}
_OPTIONAL = {
    "read_board": {"after", "limit"},
    "poll_unread": {"after", "limit"},
    "promote_board": {"to"},
    "wait_available": {"board", "timeout_seconds"},
}
_REQUIRED = {
    "post_board": {"board", "text", "key"},
    "read_board": {"board"},
    "poll_unread": {"board"},
    "ack_board": {"board", "posts"},
    "promote_board": {"board", "post", "key"},
    "claim_task": set(),
    "complete_task": {"task_id", "lease_id", "result"},
    "fail_task": {"task_id", "lease_id"},
    "read_result": {"task_id"},
    "resume_leases": set(),
    "wait_available": set(),
}
_RECHECK_SECONDS = 5


def certificate_sha256(pem: str) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def load_enrollment(path: Path) -> dict[str, str]:
    """Map a certificate fingerprint to exactly one endpoint. A bad file refuses everyone."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueRefused() from exc
    rows = document.get("credentials") if type(document) is dict else None
    if type(rows) is not list:
        raise QueueRefused()
    found: dict[str, str] = {}
    seen_endpoints: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"endpoint", "sha256"}:
            raise QueueRefused()
        endpoint, fingerprint = row["endpoint"], row["sha256"]
        if (
            not _caller_key(endpoint)
            or type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
            or fingerprint in found
            or endpoint in seen_endpoints
        ):
            raise QueueRefused()
        found[fingerprint] = endpoint
        seen_endpoints.add(endpoint)
    return found


def _canonical(arguments: dict) -> dict:
    return json.loads(json.dumps(arguments, sort_keys=True, separators=(",", ":")))


def _reject_identity(arguments: dict) -> None:
    if _IDENTITY_FIELDS.intersection(arguments):
        raise QueueRefused()


def _accept_fields(tool: str, arguments: dict) -> None:
    required = _REQUIRED[tool]
    optional = _OPTIONAL.get(tool, set())
    if not required <= set(arguments) <= required | optional:
        raise QueueRefused()


def _page(arguments: dict) -> tuple[str | None, int | None]:
    after = arguments.get("after")
    limit = arguments.get("limit")
    if after is not None and not isinstance(after, str):
        raise QueueRefused()
    if limit is not None and type(limit) is not int:
        raise QueueRefused()
    return after, limit


def perform(
    queue: DurableQueue,
    config: Path,
    tool: str,
    arguments: dict,
    request_id: str,
    *,
    still_enrolled: Callable[[], bool] | None = None,
    peer_closed: Callable[[], bool] | None = None,
) -> dict:
    """Run one remote tool as the queue's bound endpoint."""
    if tool not in _REQUIRED:
        raise QueueRefused()
    _reject_identity(arguments)
    _accept_fields(tool, arguments)
    if tool == "wait_available":
        return wait_available(
            queue,
            config,
            arguments,
            still_enrolled=still_enrolled or (lambda: True),
            peer_closed=peer_closed or (lambda: False),
        )
    if tool == "claim_task":
        return queue.claim_for_request(request_id)
    if tool == "resume_leases":
        return {"status": "ok", "leases": queue.active_leases()}
    return _once(queue, request_id, tool, arguments, lambda: _mutate(queue, config, tool, arguments))


def wait_available(
    queue: DurableQueue,
    config: Path,
    arguments: dict,
    *,
    still_enrolled: Callable[[], bool],
    peer_closed: Callable[[], bool],
) -> dict:
    """Hold until this endpoint's hint is ready or the deadline passes.

    The result is a hint. It is not stored, acknowledged, or claimed. A ready
    hint stays ready until the client acks, claims, or the lease expires, so
    repeating the call without that work returns immediately. The deadline is
    fixed when the call starts. Rechecks are at most five seconds and never
    move that deadline. The queue lock is not held while this sleeps.
    """
    board, timeout = _wait_limit(arguments)
    deadline = time.monotonic() + timeout
    reserved = False
    endpoint = queue.agent_id
    if not isinstance(endpoint, str):
        raise QueueRefused()
    try:
        while True:
            if not still_enrolled() or peer_closed():
                raise QueueRefused()
            _ticket, snap = availability_hint(queue, config=config, board=board)
            ready = bool(snap["unread_boards"] or snap["claimable"] or snap["holding_lease"])
            if ready:
                return {"status": "ready", **snap}
            if time.monotonic() >= deadline:
                return {"status": "timeout", **snap}
            if not reserved:
                if not try_reserve(endpoint):
                    raise QueueRefused()
                reserved = True
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            wait_for_change(endpoint, _ticket, min(_RECHECK_SECONDS, remaining))
    finally:
        if reserved:
            release(endpoint)


def _wait_limit(arguments: dict) -> tuple[str | None, int]:
    board = arguments.get("board")
    if board is not None and not isinstance(board, str):
        raise QueueRefused()
    timeout = arguments.get("timeout_seconds", 25)
    if type(timeout) is not int or not 1 <= timeout <= 25:
        raise QueueRefused()
    return board, timeout


def _mutate(queue: DurableQueue, config: Path, tool: str, arguments: dict) -> dict:
    if tool == "post_board":
        posted = post_board(
            queue,
            config=config,
            board=arguments["board"],
            text=arguments["text"],
            key=arguments["key"],
        )
        return {
            "status": "posted",
            "post_id": posted["post_id"],
            "board": posted["board"],
            "author": posted["author"],
            "text": posted["text"],
            "seq": posted["seq"],
            "handoff_id": posted["handoff_id"],
        }
    if tool == "read_board":
        after, limit = _page(arguments)
        page = read_board_page(queue, config=config, board=arguments["board"], after=after, limit=20 if limit is None else limit)
        return {"status": "ok", "posts": page["posts"], "next_after": page["next_after"]}
    if tool == "poll_unread":
        after, limit = _page(arguments)
        page = poll_unread(queue, config=config, board=arguments["board"], after=after, limit=20 if limit is None else limit)
        return {"status": "ok", "posts": page["posts"], "next_after": page["next_after"]}
    if tool == "ack_board":
        return {"status": "acked", "posts": ack_board(queue, config=config, board=arguments["board"], post_ids=arguments["posts"])}
    if tool == "promote_board":
        task_id = promote_board(
            queue,
            config=config,
            board=arguments["board"],
            post_id=arguments["post"],
            key=arguments["key"],
            to=arguments.get("to"),
        )
        return {"status": "promoted", "task_id": task_id}
    if tool == "complete_task":
        status = queue.finish(
            task_id=arguments["task_id"],
            lease_id=arguments["lease_id"],
            result=arguments["result"],
            failed=False,
        )
        return {"status": status, "task_id": arguments["task_id"]}
    if tool == "fail_task":
        status = queue.finish(
            task_id=arguments["task_id"],
            lease_id=arguments["lease_id"],
            result=None,
            failed=True,
        )
        return {"status": status, "task_id": arguments["task_id"]}
    if tool == "read_result":
        return queue.read_result(arguments["task_id"])
    raise QueueRefused()


@contextmanager
def _call_lock(queue: DurableQueue) -> Iterator[None]:
    """Serialize one endpoint's remote calls without holding the queue lock."""
    if not isinstance(queue.agent_id, str):
        raise QueueRefused()
    directory = queue.root / "remote_calls" / queue.agent_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = (directory / ".lock").open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _once(queue: DurableQueue, request_id: str, tool: str, arguments: dict, perform_call) -> dict:
    """Run the mutation once for this endpoint and request id.

    The endpoint lock is held across the check, the call, and the store.
    A second caller with the same arguments waits and receives the stored
    result. A second caller with different arguments is refused before it
    mutates. A crash before the store releases the lock; the retry runs the
    same idempotent operation again.
    """
    if not _caller_key(request_id):
        raise QueueRefused()
    canonical = _canonical(arguments)
    path = queue.root / "remote_calls" / str(queue.agent_id) / "results" / request_id
    with _call_lock(queue):
        if path.exists():
            record = queue._read(path)
            if record.get("tool") != tool or record.get("arguments") != canonical or type(record.get("result")) is not dict:
                raise QueueRefused()
            return record["result"]
        result = perform_call()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        queue._write(path, {"tool": tool, "arguments": canonical, "result": result})
        return result


class RemoteHost:
    """One process, one state directory, client certificates enrolled by an operator."""

    def __init__(self, state: Path, config: Path, enrollment: Path, certificate: Path, key: Path, client_ca: Path) -> None:
        self.state = Path(state)
        self.config = Path(config)
        self.enrollment = Path(enrollment)
        self.certificate = Path(certificate)
        self.key = Path(key)
        self.client_ca = Path(client_ca)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_handle = (self.state / ".remote.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("remote host already running") from exc

    def endpoint_for(self, der_certificate: bytes) -> str:
        fingerprint = hashlib.sha256(der_certificate).hexdigest()
        enrolled = load_enrollment(self.enrollment)
        endpoint = enrolled.get(fingerprint)
        if endpoint is None:
            raise QueueRefused()
        return endpoint

    def serve(self, host: str, port: int) -> None:
        if not _bind_host(host):
            raise SystemExit("remote bind address is invalid")
        context = server_tls_context(self.certificate, self.key, self.client_ca)
        server = _TLSServer((host, port), _Handler, context, self)
        server.timeout = 10
        _LOG.info("remote host ready")
        server.serve_forever()


class _TLSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type, context: ssl.SSLContext, host: RemoteHost) -> None:
        self.ssl_context = context
        self.remote = host
        super().__init__(address, handler)

    def get_request(self):  # noqa: ANN201
        sock, address = self.socket.accept()
        try:
            return self.ssl_context.wrap_socket(sock, server_side=True), address
        except ssl.SSLError:
            sock.close()
            raise

    def handle_error(self, request: Any, client_address: Any) -> None:
        _LOG.info("status=refused")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 10

    def handle(self) -> None:
        self.request.settimeout(10)
        super().handle()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/call":
            self._text(404)
            return
        der = self.connection.getpeercert(binary_form=True)
        if not der:
            self._text(401)
            return
        length = _content_length(self.headers.get_all("Content-Length"))
        if length is None:
            self._text(400)
            return
        raw = self.rfile.read(length)
        host = getattr(self.server, "remote")
        try:
            endpoint = host.endpoint_for(der)
            payload = json.loads(raw.decode("utf-8"))
            if type(payload) is not dict or set(payload) != {"request_id", "tool", "arguments"}:
                raise QueueRefused()
            request_id, tool, arguments = payload["request_id"], payload["tool"], payload["arguments"]
            if not isinstance(tool, str) or type(arguments) is not dict or not isinstance(request_id, str):
                raise QueueRefused()
            queue = open_endpoint(host.state, host.config, endpoint)

            def still_enrolled() -> bool:
                try:
                    return host.endpoint_for(der) == endpoint
                except QueueRefused:
                    return False

            result = perform(
                queue,
                host.config,
                tool,
                arguments,
                request_id,
                still_enrolled=still_enrolled,
                peer_closed=lambda: _peer_closed(self.request),
            )
        except QueueRefused:
            _LOG.info("status=refused")
            self._json(200, {"ok": False, "error": "request refused"})
            return
        except Exception:
            _LOG.info("status=refused")
            self._json(200, {"ok": False, "error": "request refused"})
            return
        _LOG.info("tool=%s status=200", tool if tool in _REQUIRED else "unknown")
        self._json(200, {"ok": True, "result": result})

    def do_GET(self) -> None:  # noqa: N802
        self._text(404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _text(self, status: int) -> None:
        body = b"request refused"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def _peer_closed(connection: Any) -> bool:
    """True when the accepted client has gone away. A quiet socket stays open."""
    try:
        readable, _, _ = select.select([connection], [], [], 0)
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    previous = connection.gettimeout()
    try:
        connection.settimeout(0)
        try:
            data = connection.recv(1, socket.MSG_PEEK)
        except (BlockingIOError, TimeoutError, ssl.SSLWantReadError):
            return False
        except OSError:
            return True
        return data == b""
    finally:
        connection.settimeout(10 if previous is None else previous)


def _content_length(values: list[str] | None) -> int | None:
    if not values or len(values) != 1:
        return None
    text = values[0].strip()
    if not text.isdigit():
        return None
    length = int(text)
    if length <= 0 or length > MAX_BODY_BYTES:
        return None
    return length


def strict_verification_supported() -> bool:
    """True when this Python build can request OpenSSL's strict X.509 checks."""
    return bool(getattr(ssl, "VERIFY_X509_STRICT", 0))


def enable_strict_verification(context: ssl.SSLContext) -> bool:
    """Turn on VERIFY_X509_STRICT when this interpreter has the flag.

    Returns whether the flag is now set on this context. This does not change
    certificate or hostname verification: those stay required.
    """
    flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if not flag:
        return False
    context.verify_flags = context.verify_flags | flag
    return bool(context.verify_flags & flag)


def server_tls_context(certificate: Path, key: Path, client_ca: Path) -> ssl.SSLContext:
    """Server context. Clients must present a certificate we can verify."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    context.load_verify_locations(client_ca)
    context.verify_mode = ssl.CERT_REQUIRED
    enable_strict_verification(context)
    if context.verify_mode != ssl.CERT_REQUIRED:
        raise SystemExit("server certificate verification is not required")
    return context


def client_tls_context(ca: Path, certificate: Path, key: Path) -> ssl.SSLContext:
    """Client context. Hostname and certificate checks stay at their defaults, on."""
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=str(ca))
    context.load_cert_chain(certificate, key)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    enable_strict_verification(context)
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise SystemExit("client certificate verification is not required")
    return context


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_pidfile(path_text: str) -> None:
    """Record this process after it owns the directory lock.

    Exit removes the file only when it still names this process. A live pid
    that is not this process is left in place. A failed start that never
    publishes a pid does not delete another host's record.
    """
    if not path_text:
        return
    path = Path(path_text)
    if path.exists():
        current = path.read_text(encoding="utf-8").strip()
        if current.isdigit() and int(current) != os.getpid() and _pid_alive(int(current)):
            raise SystemExit("listener pid file belongs to another process")
    path.write_text("%s\n" % os.getpid(), encoding="utf-8")

    def cleanup() -> None:
        try:
            if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                path.unlink()
        except OSError:
            return

    import atexit
    atexit.register(cleanup)


def _bind_host(host: str) -> bool:
    if host in {"0.0.0.0", "::", ""}:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4


def call_remote(
    *,
    url: str,
    ca: Path,
    certificate: Path,
    key: Path,
    tool: str,
    arguments: dict,
    request_id: str,
    timeout: float = 5,
) -> dict:
    """Call the host over TLS. HTTP URLs are refused."""
    if not url.startswith("https://") or timeout <= 0 or timeout > 30:
        raise QueueRefused()
    import urllib.request

    context = client_tls_context(ca, certificate, key)
    payload = json.dumps(
        {"request_id": request_id, "tool": tool, "arguments": arguments},
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/call",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise QueueRefused() from exc
    if type(body) is not dict or body.get("ok") is not True or type(body.get("result")) is not dict:
        raise QueueRefused()
    return body["result"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authoritative TLS host. Does not enroll credentials.")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    required = (
        "AGENT_BUS_STATE_DIR",
        "AGENT_BUS_CONFIG",
        "AGENT_BUS_ENROLLMENT",
        "AGENT_BUS_REMOTE_PORT",
        "AGENT_BUS_TLS_CERT",
        "AGENT_BUS_TLS_KEY",
        "AGENT_BUS_TLS_CLIENT_CA",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("required environment is missing: " + ", ".join(missing))
    host = os.environ.get("AGENT_BUS_REMOTE_HOST", "127.0.0.1")
    try:
        port = int(os.environ["AGENT_BUS_REMOTE_PORT"])
    except ValueError as exc:
        raise SystemExit("required environment is invalid") from exc
    if not _bind_host(host) or not 1 <= port <= 65535:
        raise SystemExit("remote bind address is invalid")
    if strict_verification_supported():
        print("tls_strict=on", file=sys.stderr)
    else:
        print("tls_strict=unavailable", file=sys.stderr)
    running = RemoteHost(
        Path(os.environ["AGENT_BUS_STATE_DIR"]),
        Path(os.environ["AGENT_BUS_CONFIG"]),
        Path(os.environ["AGENT_BUS_ENROLLMENT"]),
        Path(os.environ["AGENT_BUS_TLS_CERT"]),
        Path(os.environ["AGENT_BUS_TLS_KEY"]),
        Path(os.environ["AGENT_BUS_TLS_CLIENT_CA"]),
    )
    _write_pidfile(os.environ.get("AGENT_BUS_REMOTE_PIDFILE", ""))
    running.serve(host, port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
