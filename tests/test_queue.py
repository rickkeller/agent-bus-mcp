from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest

from agent_bus_mcp.queue import DurableQueue, QueueRefused


def test_idempotency_and_public_https_only(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    public_reference = "https" + "://" + "example" + ".invalid/a"
    first = queue.enqueue(goal="read", references=[public_reference], idempotency_key="once")
    assert queue.enqueue(goal="different retry", references=[public_reference], idempotency_key="once") == first
    numeric_host = "https://" + ".".join(("192", "168", "1", "1")) + "/x"
    loopback_name = "local" + "host"
    for url in ("file:///tmp/x", "https" + "://" + loopback_name + "/x", numeric_host):
        with pytest.raises(QueueRefused):
            queue.enqueue(goal="read", references=[url], idempotency_key="bad" + str(len(url)))
    for key in (".", ".."):
        with pytest.raises(QueueRefused):
            queue.enqueue(goal="read", references=[], idempotency_key=key)


def test_existing_state_directory_is_private(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    DurableQueue(tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_atomic_claim_lease_fence_and_completion(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    task = queue.enqueue(goal="read", references=[], idempotency_key="one")
    results: list[dict | None] = []
    workers = [threading.Thread(target=lambda: results.append(queue.claim())) for _ in range(4)]
    [worker.start() for worker in workers]
    [worker.join() for worker in workers]
    claimed = [item for item in results if item]
    assert len(claimed) == 1 and claimed[0]["task_id"] == task
    now = datetime.now(timezone.utc)
    with pytest.raises(QueueRefused):
        queue.finish(task_id=task, lease_id="lease_wrong", result="no", failed=False, now=now)
    assert queue.finish(task_id=task, lease_id=claimed[0]["lease_id"], result="done", failed=False, now=now) == "completed"
    assert queue.finish(task_id=task, lease_id=claimed[0]["lease_id"], result="retry", failed=False, now=now) == "already_completed"


def test_expired_lease_rotates_fence(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    queue.enqueue(goal="read", references=[], idempotency_key="one")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = queue.claim(base)
    second = queue.claim(base + timedelta(minutes=16))
    assert first and second and first["lease_id"] != second["lease_id"]
    with pytest.raises(QueueRefused):
        queue.finish(task_id=first["task_id"], lease_id=first["lease_id"], result="late", failed=False, now=base + timedelta(minutes=16))


def test_finish_refuses_path_syntax(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    with pytest.raises(QueueRefused):
        queue.finish(task_id="../outside", lease_id="lease_" + "0" * 32, result="no", failed=False)


def test_configured_route_rejects_other_identities(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="scheduled", worker_id="consumer")
    with pytest.raises(QueueRefused):
        queue.enqueue(goal="read", references=[], idempotency_key="route", producer_id="other")
    task = queue.enqueue(goal="read", references=[], idempotency_key="route", producer_id="scheduled")
    with pytest.raises(QueueRefused):
        queue.claim(worker_id="other")
    claim = queue.claim(worker_id="consumer")
    assert claim and claim["task_id"] == task
    with pytest.raises(QueueRefused):
        queue.finish(task_id=task, lease_id=claim["lease_id"], result="done", failed=False, worker_id="other")


def test_text_bounds_and_control_characters_are_refused(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path)
    with pytest.raises(QueueRefused):
        queue.enqueue(goal="unsafe\x00text", references=[], idempotency_key="unsafe")
    task = queue.enqueue(goal="safe", references=[], idempotency_key="safe")
    claim = queue.claim()
    assert claim and claim["task_id"] == task
    with pytest.raises(QueueRefused):
        queue.finish(task_id=task, lease_id=claim["lease_id"], result="unsafe\x00text", failed=False)
