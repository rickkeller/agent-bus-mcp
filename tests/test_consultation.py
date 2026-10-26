from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

import pytest

from agent_bus_mcp.mcp import MCPApplication
from agent_bus_mcp.queue import DurableQueue, QueueRefused, RoutePolicy


def _ask(queue: DurableQueue, now: datetime, key: str = "question") -> dict:
    return queue.ask_question(
        question="Which heading is clearer?",
        origin_ref="draft-1",
        idempotency_key=key,
        expires_in_seconds=3600,
        now=now,
    )


def test_local_ask_is_durable_idempotent_and_separate_from_tasks(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="requester", worker_id="advisor")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    first = queue.ask_question(
        question="Should this report use a table?",
        origin_ref="report-17",
        idempotency_key="question-once",
        expires_in_seconds=3600,
        now=now,
    )
    duplicate = queue.ask_question(
        question="A retry must not replace the original question",
        origin_ref="other-origin",
        idempotency_key="question-once",
        expires_in_seconds=60,
        now=now,
    )

    assert duplicate == first
    assert first == {
        "consultation_id": first["consultation_id"],
        "producer_id": "requester",
        "worker_id": "advisor",
        "origin_ref": "report-17",
        "idempotency_key": "question-once",
        "question": "Should this report use a table?",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-01-01T01:00:00Z",
        "lease_id": None,
        "lease_expires_at": None,
        "claimed_at": None,
        "answer": None,
        "answered_at": None,
    }
    assert queue.read_answer(first["consultation_id"], now=now) == first
    assert queue.claim(now) is None
    assert not list((tmp_path / "tasks").glob("*.json"))


def test_ask_mcp_claim_mcp_answer_local_read(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="requester", worker_id="advisor")
    asked = queue.ask_question(
        question="Is a short heading clearer?",
        origin_ref="draft-4",
        idempotency_key="e2e-question",
        expires_in_seconds=3600,
    )
    app = MCPApplication(queue, "synthetic-secret")

    claimed_response = app._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "claim_question", "arguments": {}},
        }
    )
    claimed = claimed_response["result"]["structuredContent"]
    assert claimed["status"] == "claimed"
    assert claimed["consultation_id"] == asked["consultation_id"]
    assert claimed["question"] == "Is a short heading clearer?"
    assert claimed["origin_ref"] == "draft-4"

    answered_response = app._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "answer_question",
                "arguments": {
                    "consultation_id": asked["consultation_id"],
                    "lease_id": claimed["lease_id"],
                    "answer": "Yes; it makes the section easier to scan.",
                },
            },
        }
    )
    answered = answered_response["result"]["structuredContent"]
    assert answered == {"status": "answered", "consultation_id": asked["consultation_id"]}

    final = queue.read_answer(asked["consultation_id"])
    assert final["status"] == "answered"
    assert final["answer"] == "Yes; it makes the section easier to scan."
    assert final["answered_at"]
    assert json.loads(answered_response["result"]["content"][0]["text"]) == answered
    assert queue.claim() is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question", "é" * 1025),
        ("question", "unsafe\x00question"),
        ("origin_ref", "x" * 257),
        ("origin_ref", "unsafe\x00origin"),
        ("idempotency_key", "../escape"),
        ("expires_in_seconds", 0),
        ("expires_in_seconds", 86401),
        ("expires_in_seconds", True),
    ],
)
def test_ask_refuses_malformed_or_oversize_values(tmp_path: Path, field: str, value: object) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    arguments: dict[str, object] = {
        "question": "Is this bounded?",
        "origin_ref": "draft-2",
        "idempotency_key": "bounded",
        "expires_in_seconds": 60,
    }
    arguments[field] = value
    with pytest.raises(QueueRefused, match="^request refused$"):
        queue.ask_question(**arguments)  # type: ignore[arg-type]


def test_ask_refuses_malformed_time(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    with pytest.raises(QueueRefused, match="^request refused$"):
        queue.ask_question(
            question="Is this bounded?",
            origin_ref="draft-2",
            idempotency_key="bounded",
            expires_in_seconds=60,
            now="not-a-time",  # type: ignore[arg-type]
        )


def test_expired_question_cannot_be_claimed_or_answered(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    asked = queue.ask_question(
        question="Will this expire?",
        origin_ref="draft-3",
        idempotency_key="expires",
        expires_in_seconds=60,
        now=base,
    )
    assert queue.claim_question(base + timedelta(seconds=61)) is None
    expired = queue.read_answer(asked["consultation_id"], now=base + timedelta(seconds=61))
    assert expired["status"] == "expired"
    assert expired["answer"] is None


def test_expired_lease_rotates_fence_without_extending_question(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    asked = _ask(queue, base)
    first = queue.claim_question(base)
    second = queue.claim_question(base + timedelta(seconds=901))
    assert first and second
    assert first["lease_id"] != second["lease_id"]
    assert second["lease_expires_at"] <= asked["expires_at"]
    with pytest.raises(QueueRefused):
        queue.answer_question(
            consultation_id=asked["consultation_id"],
            lease_id=first["lease_id"],
            answer="stale",
            now=base + timedelta(seconds=901),
        )


def test_configured_identities_and_lease_are_authoritative(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    policy = RoutePolicy(
        agents={"requester", "advisor", "other"},
        routes={("requester", "advisor"): {"consultation"}},
    )
    requester = DurableQueue(tmp_path, policy=policy, agent_id="requester")
    advisor = DurableQueue(tmp_path, policy=policy, agent_id="advisor")
    other = DurableQueue(tmp_path, policy=policy, agent_id="other")
    asked = _ask(requester, base)
    with pytest.raises(QueueRefused):
        other.read_answer(asked["consultation_id"], now=base)
    assert other.claim_question(base) is None
    claimed = advisor.claim_question(base)
    assert claimed
    for queue, lease in ((other, claimed["lease_id"]), (advisor, "lease_" + "0" * 32)):
        with pytest.raises(QueueRefused):
            queue.answer_question(
                consultation_id=asked["consultation_id"],
                lease_id=lease,
                answer="No",
                now=base,
            )


@pytest.mark.parametrize("answer", ["é" * 1025, "unsafe\x00answer", ""])
def test_answer_refuses_malformed_or_oversize_text(tmp_path: Path, answer: str) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    asked = _ask(queue, base)
    claimed = queue.claim_question(base)
    assert claimed
    with pytest.raises(QueueRefused):
        queue.answer_question(
            consultation_id=asked["consultation_id"],
            lease_id=claimed["lease_id"],
            answer=answer,
            now=base,
        )


def test_competing_answers_allow_exactly_one_and_preserve_binding(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    queue = DurableQueue(tmp_path, producer_id="requester", worker_id="advisor")
    asked = _ask(queue, base)
    claimed = queue.claim_question(base)
    assert claimed
    outcomes: list[str] = []

    def submit(answer: str) -> None:
        try:
            outcomes.append(
                queue.answer_question(
                    consultation_id=asked["consultation_id"],
                    lease_id=claimed["lease_id"],
                    answer=answer,
                    now=base + timedelta(seconds=1),
                )
            )
        except QueueRefused:
            outcomes.append("refused")

    workers = [threading.Thread(target=submit, args=(answer,)) for answer in ("Use A.", "Use B.")]
    [worker.start() for worker in workers]
    [worker.join() for worker in workers]
    assert sorted(outcomes) == ["answered", "refused"]

    final = queue.read_answer(asked["consultation_id"], now=base)
    for field in ("consultation_id", "producer_id", "worker_id", "origin_ref", "idempotency_key", "question", "created_at", "expires_at"):
        assert final[field] == asked[field]
    with pytest.raises(QueueRefused):
        queue.answer_question(
            consultation_id=asked["consultation_id"],
            lease_id=claimed["lease_id"],
            answer=final["answer"],
            now=base + timedelta(seconds=2),
        )
