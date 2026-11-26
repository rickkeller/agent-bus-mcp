"""Opt-in webhook wake for an endpoint that cannot hold a listener open.

The config file may carry a top-level ``webhooks`` list. Each entry names one
endpoint, one URL, the events that wake it, and where to read the signing
secret. After a board post or a promotion is written, the bus queues a small
signed JSON wake for every matching entry and sends it from a background
thread. The wake names the event; it never carries post text. Sending never
blocks the write and never turns a write into a refusal.

No ``webhooks`` key means no thread, no request, and no behaviour change.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .queue import QueueRefused, _identity

_LOG = logging.getLogger("agent_bus_mcp.webhook")

EVENTS = ("board_post", "handoff")
_ENTRY_KEYS = {"endpoint", "url", "events", "secret_file", "secret_env", "bearer_file", "bearer_env", "allow_http", "signature_format", "signature_header"}
MAX_URL_BYTES = 2048
MAX_PENDING = 256

# Delivery tuning. Module constants so tests can shorten them.
TIMEOUT_SECONDS = 5.0
BACKOFF_SECONDS = (1.0, 2.0, 4.0)  # a first try plus three retries
COALESCE_SECONDS = 0.05

SIGNATURE_HEADER = "X-Agent-Bus-Signature"
SIGNATURE_FORMATS = ("agent-bus", "stripe")
TIMESTAMP_HEADER = "X-Agent-Bus-Timestamp"
EVENT_HEADER = "X-Agent-Bus-Event"
DELIVERY_HEADER = "X-Agent-Bus-Delivery"


class Webhook:
    __slots__ = ("endpoint", "url", "events", "secret_file", "secret_env", "bearer_file", "bearer_env",
                 "signature_format", "signature_header")

    def __init__(self, endpoint: str, url: str, events: tuple[str, ...], secret_file: str | None,
                 secret_env: str | None, bearer_file: str | None, bearer_env: str | None,
                 signature_format: str = "agent-bus", signature_header: str = SIGNATURE_HEADER) -> None:
        self.endpoint = endpoint
        self.url = url
        self.events = events
        self.secret_file = secret_file
        self.secret_env = secret_env
        self.bearer_file = bearer_file
        self.bearer_env = bearer_env
        self.signature_format = signature_format
        self.signature_header = signature_header


def _loopback(host: str) -> bool:
    host = host.rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url_ok(url: object, allow_http: bool) -> bool:
    if type(url) is not str or not url or len(url.encode()) > MAX_URL_BYTES:
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        return False
    try:
        parsed = urlsplit(url)
        parsed.port  # raises on a bad port
    except ValueError:
        return False
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and (allow_http or _loopback(parsed.hostname))


def _source_ok(entry: dict, file_key: str, env_key: str, required: bool) -> bool:
    chosen = [key for key in (file_key, env_key) if key in entry]
    if len(chosen) > 1 or (required and not chosen):
        return False
    for key in chosen:
        value = entry[key]
        if type(value) is not str or not value or "\x00" in value:
            return False
        if key == env_key and not all(char.isalnum() or char == "_" for char in value):
            return False
    return True


def webhooks_from_config(document: object) -> tuple[Webhook, ...]:
    """Parse the optional ``webhooks`` list. Absent means none."""
    if type(document) is not dict:
        raise QueueRefused("config is invalid")
    raw = document.get("webhooks")
    if raw is None:
        return ()
    endpoints = document.get("endpoints")
    if type(raw) is not list or type(endpoints) is not list:
        raise QueueRefused("webhooks is invalid")
    found: list[Webhook] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        where = "webhooks[%d]" % index
        if type(entry) is not dict or not set(entry) <= _ENTRY_KEYS or not {"endpoint", "url", "events"} <= set(entry):
            raise QueueRefused("%s has missing or unknown keys" % where)
        endpoint = entry["endpoint"]
        if not _identity(endpoint) or endpoint not in endpoints:
            raise QueueRefused("%s endpoint is not a configured endpoint" % where)
        if endpoint in seen:
            raise QueueRefused("%s endpoint already has a webhook" % where)
        allow_http = entry.get("allow_http", False)
        if type(allow_http) is not bool:
            raise QueueRefused("%s allow_http must be true or false" % where)
        if not _url_ok(entry["url"], allow_http):
            raise QueueRefused("%s url must be https, or http on loopback or with allow_http" % where)
        events = entry["events"]
        if (
            type(events) is not list
            or not events
            or len(set(events)) != len(events)
            or not all(event in EVENTS for event in events)
        ):
            raise QueueRefused("%s events must name board_post and or handoff" % where)
        if not _source_ok(entry, "secret_file", "secret_env", True):
            raise QueueRefused("%s needs exactly one of secret_file or secret_env" % where)
        if not _source_ok(entry, "bearer_file", "bearer_env", False):
            raise QueueRefused("%s may set at most one of bearer_file or bearer_env" % where)
        signature_format = entry.get("signature_format", "agent-bus")
        if signature_format not in SIGNATURE_FORMATS:
            raise QueueRefused("%s signature_format must be agent-bus or stripe" % where)
        signature_header = entry.get("signature_header", SIGNATURE_HEADER)
        if not _header_name(signature_header):
            raise QueueRefused("%s signature_header is not a valid header name" % where)
        seen.add(endpoint)
        found.append(
            Webhook(
                endpoint,
                entry["url"],
                tuple(events),
                entry.get("secret_file"),
                entry.get("secret_env"),
                entry.get("bearer_file"),
                entry.get("bearer_env"),
                signature_format,
                signature_header,
            )
        )
    return tuple(found)


def load_webhooks(config: Path) -> tuple[Webhook, ...]:
    try:
        document = json.loads(Path(config).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueRefused("config is unreadable") from exc
    return webhooks_from_config(document)


def check_config(config: Path) -> None:
    """Refuse a bad ``webhooks`` block at start-up, and refuse an unreadable secret."""
    for hook in load_webhooks(config):
        if _read_source(hook.secret_file, hook.secret_env) is None:
            raise QueueRefused("webhook for %s has no readable secret" % hook.endpoint)
        if (hook.bearer_file or hook.bearer_env) and _read_source(hook.bearer_file, hook.bearer_env) is None:
            raise QueueRefused("webhook for %s has no readable bearer token" % hook.endpoint)


_RESERVED_HEADERS = {"authorization", "content-type", "content-length", "host", "user-agent",
                     "x-agent-bus-timestamp",
                     "x-agent-bus-event", "x-agent-bus-delivery"}


def _header_name(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 64
        and all(char.isalnum() or char == "-" for char in value)
        and value.lower() not in _RESERVED_HEADERS
    )


def _read_source(path: str | None, env: str | None) -> str | None:
    try:
        if path:
            value = Path(path).read_text(encoding="utf-8").strip()
        elif env:
            value = os.environ.get(env, "").strip()
        else:
            return None
    except (OSError, UnicodeError):
        return None
    return value or None


def _redacted(url: str) -> str:
    """Scheme and host only. Paths and query strings often hold tokens."""
    try:
        parsed = urlsplit(url)
        return "%s://%s" % (parsed.scheme, parsed.hostname or "?")
    except ValueError:
        return "?"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sign(secret: str, body: bytes) -> str:
    """Return the header value: sha256=<hex HMAC-SHA256 of the exact body>."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def sign_stripe(secret: str, body: bytes, unix_seconds: int) -> str:
    """Return t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">, the Stripe-style value."""
    signed = str(unix_seconds).encode("ascii") + b"." + body
    return "t=%d,v1=%s" % (unix_seconds, hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest())


def verify_stripe(secret: str, body: bytes, header: str, max_age_s: int = 300) -> bool:
    """Receiver-side check of the Stripe-style value, also used by the tests."""
    try:
        parts = dict(item.split("=", 1) for item in (header or "").split(","))
        stamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - stamp) > max_age_s:
        return False
    return hmac.compare_digest(sign_stripe(secret, body, stamp), "t=%d,v1=%s" % (stamp, parts.get("v1", "")))


def verify(secret: str, body: bytes, header: str) -> bool:
    """Receiver-side check, also used by the tests."""
    return hmac.compare_digest(sign(secret, body), header or "")


# ---------------------------------------------------------------- dispatch


def fire_board_post(config: Path, record: dict, members: tuple[str, ...]) -> int:
    """Queue a board_post wake for every member webhook except the author's.

    Returns the number of wakes queued. Never raises.
    """
    try:
        hooks = load_webhooks(config)
    except Exception as exc:  # a bad block was caught at start-up; stay quiet but say so
        _LOG.warning("webhook config skipped: %s", exc)
        return 0
    queued = 0
    for hook in hooks:
        if "board_post" not in hook.events or hook.endpoint == record.get("author") or hook.endpoint not in members:
            continue
        event = {
            "event": "board_post",
            "endpoint": hook.endpoint,
            "board": record["board"],
            "post_id": record["post_id"],
            "seq": record["seq"],
            "author": record["author"],
        }
        queued += _DISPATCHER.submit(hook, event)
    return queued


def fire_handoff(config: Path, *, board: str, post_id: str, task_id: str, author: str, destination: str) -> int:
    """Queue a handoff wake for the destination's webhook. Never raises."""
    try:
        hooks = load_webhooks(config)
    except Exception as exc:
        _LOG.warning("webhook config skipped: %s", exc)
        return 0
    queued = 0
    for hook in hooks:
        if "handoff" not in hook.events or hook.endpoint != destination or hook.endpoint == author:
            continue
        event = {
            "event": "handoff",
            "endpoint": hook.endpoint,
            "board": board,
            "post_id": post_id,
            "task_id": task_id,
            "author": author,
        }
        queued += _DISPATCHER.submit(hook, event)
    return queued


def flush(timeout: float = 20.0) -> bool:
    """Wait until queued wakes are sent or given up. For one-shot commands."""
    return _DISPATCHER.flush(timeout)


class _Dispatcher:
    """One worker thread per target endpoint, started on first use.

    Wakes that arrive while a send is pending for the same endpoint are
    coalesced: the newest wake is sent and ``coalesced`` counts how many
    events it stands for. A receiver should treat any wake as "read your
    unread posts and claimable tasks", not as the full list.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending: dict[str, list[tuple[Webhook, dict]]] = {}
        self._running: set[str] = set()

    def submit(self, hook: Webhook, event: dict) -> int:
        with self._lock:
            queue = self._pending.setdefault(hook.endpoint, [])
            if len(queue) >= MAX_PENDING:
                queue.pop(0)
                _LOG.warning("webhook backlog full for %s; oldest wake dropped", hook.endpoint)
            queue.append((hook, event))
            if hook.endpoint not in self._running:
                self._running.add(hook.endpoint)
                threading.Thread(
                    target=self._drain, args=(hook.endpoint,), name="agent-bus-webhook", daemon=True
                ).start()
        return 1

    def flush(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._running:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._idle.wait(left)
        return True

    def _drain(self, endpoint: str) -> None:
        try:
            while True:
                time.sleep(COALESCE_SECONDS)
                with self._lock:
                    batch = self._pending.get(endpoint) or []
                    if not batch:
                        self._pending.pop(endpoint, None)
                        self._running.discard(endpoint)
                        self._idle.notify_all()
                        return
                    self._pending[endpoint] = []
                hook, event = batch[-1]
                event = dict(event, coalesced=len(batch))
                try:
                    _deliver(hook, event)
                except Exception as exc:  # never let a bug kill the worker silently
                    _LOG.warning("webhook to %s for %s failed: %s", _redacted(hook.url), endpoint, type(exc).__name__)
        except BaseException:
            with self._lock:
                self._running.discard(endpoint)
                self._idle.notify_all()
            raise


_DISPATCHER = _Dispatcher()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


def _deliver(hook: Webhook, event: dict) -> bool:
    secret = _read_source(hook.secret_file, hook.secret_env)
    if secret is None:
        _LOG.warning("webhook for %s skipped: secret is unreadable", hook.endpoint)
        return False
    bearer = _read_source(hook.bearer_file, hook.bearer_env) if (hook.bearer_file or hook.bearer_env) else None
    target = _redacted(hook.url)
    attempts = 1 + len(BACKOFF_SECONDS)
    delivery_id = "whd_" + secrets.token_hex(12)  # the same on every retry, so a receiver can dedupe
    for attempt in range(attempts):
        timestamp = _stamp()
        payload = dict(event, timestamp=timestamp, delivery_id=delivery_id)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "agent-bus-webhook/1",
            hook.signature_header: (
                sign_stripe(secret, body, int(time.time()))
                if hook.signature_format == "stripe"
                else sign(secret, body)
            ),
            TIMESTAMP_HEADER: timestamp,
            EVENT_HEADER: payload["event"],
            DELIVERY_HEADER: payload["delivery_id"],
        }
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        request = urllib.request.Request(hook.url, data=body, headers=headers, method="POST")
        status = 0
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:
            _LOG.warning(
                "webhook to %s for %s attempt %d/%d failed: %s",
                target, hook.endpoint, attempt + 1, attempts, type(exc).__name__,
            )
        if 200 <= status < 300:
            _LOG.info("webhook to %s for %s sent %s (status %d)", target, hook.endpoint, payload["event"], status)
            return True
        if status:
            _LOG.warning(
                "webhook to %s for %s attempt %d/%d got status %d", target, hook.endpoint, attempt + 1, attempts, status
            )
            if not _retryable(status):
                return False
        if attempt < len(BACKOFF_SECONDS):
            time.sleep(BACKOFF_SECONDS[attempt])
    _LOG.warning("webhook to %s for %s gave up after %d attempts", target, hook.endpoint, attempts)
    return False
