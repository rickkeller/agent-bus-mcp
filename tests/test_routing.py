from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path

import pytest

import agent_bus_mcp
from agent_bus_mcp.queue import DurableQueue, QueueRefused


def _policy(agents: set[str], routes: dict[tuple[str, str], set[str]]):
    policy_type = getattr(agent_bus_mcp, "RoutePolicy", None)
    assert policy_type is not None, "RoutePolicy must be public"
    return policy_type(agents=agents, routes=routes)


def _write_parent_record(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )


def _create_parent_format_state(
    root: Path, *, include_task: bool = True, include_consultation: bool = True
) -> tuple[str | None, str | None]:
    for directory in ("tasks", "idempotency", "consultations", "consultation_idempotency"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / ".queue.lock").write_text("", encoding="utf-8")

    task_id = "task_" + "1" * 32 if include_task else None
    if task_id:
        _write_parent_record(
            root / "tasks" / f"{task_id}.json",
            {
                "task_id": task_id,
                "producer_id": "producer",
                "worker_id": "worker",
                "goal": "legacy task",
                "references": [],
                "status": "pending",
                "lease_id": None,
                "lease_expires_at": None,
                "result": None,
            },
        )
        _write_parent_record(root / "idempotency" / "task-once", {"task_id": task_id})

    consultation_id = "consultation_" + "2" * 32 if include_consultation else None
    if consultation_id:
        _write_parent_record(
            root / "consultations" / f"{consultation_id}.json",
            {
                "consultation_id": consultation_id,
                "producer_id": "producer",
                "worker_id": "worker",
                "origin_ref": "legacy-origin",
                "idempotency_key": "question-once",
                "question": "Legacy question?",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-01T01:00:00Z",
                "lease_id": None,
                "lease_expires_at": None,
                "claimed_at": None,
                "answer": None,
                "answered_at": None,
            },
        )
        _write_parent_record(
            root / "consultation_idempotency" / "question-once",
            {"consultation_id": consultation_id},
        )
    return task_id, consultation_id


def _parent_record_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for directory in ("tasks", "idempotency", "consultations", "consultation_idempotency")
        for path in sorted((root / directory).rglob("*"))
        if path.is_file()
    }


def test_authority_identities_use_ascii_grammar() -> None:
    valid = _policy(
        {"producer", "worker_1"},
        {("producer", "worker_1"): {"task"}},
    )
    assert valid.allows("producer", "worker_1", "task")

    confusable_worker = "w\u043erker"
    with pytest.raises(QueueRefused, match="^request refused$"):
        _policy(
            {"producer", confusable_worker},
            {("producer", confusable_worker): {"task"}},
        )


@pytest.mark.parametrize("route_part", ("source", "destination", "mode"))
def test_route_policy_requires_exact_builtin_route_scalars(route_part: str) -> None:
    class TextSubclass(str):
        pass

    source = TextSubclass("producer") if route_part == "source" else "producer"
    destination = TextSubclass("worker") if route_part == "destination" else "worker"
    mode = TextSubclass("task") if route_part == "mode" else "task"

    with pytest.raises(QueueRefused, match="^request refused$"):
        _policy(
            {"producer", "worker"},
            {(source, destination): {mode}},
        )


def test_constructor_identities_refuse_non_ascii_before_state_creation(tmp_path: Path) -> None:
    confusable_worker = "w\u043erker"
    legacy_root = tmp_path / "legacy"
    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(legacy_root, producer_id="producer", worker_id=confusable_worker)
    assert not legacy_root.exists()

    policy = _policy(
        {"producer", "worker_1"},
        {("producer", "worker_1"): {"task"}},
    )
    bound_root = tmp_path / "bound"
    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(bound_root, policy=policy, agent_id=confusable_worker)
    assert not bound_root.exists()


def test_route_policy_cannot_be_reassigned_after_construction() -> None:
    policy = _policy(
        {"producer", "worker"},
        {("producer", "worker"): {"task"}},
    )
    with pytest.raises(AttributeError):
        policy.agents = frozenset({"producer", "worker", "intruder"})
    with pytest.raises(AttributeError):
        policy.routes = {("intruder", "worker"): frozenset({"task"})}
    with pytest.raises(AttributeError):
        del policy.routes


def test_one_controller_assigns_tasks_to_two_workers_on_one_state_root(tmp_path: Path) -> None:
    policy = _policy(
        {"controller", "worker_a", "worker_b"},
        {
            ("controller", "worker_a"): {"task"},
            ("controller", "worker_b"): {"task"},
        },
    )
    controller = DurableQueue(tmp_path, policy=policy, agent_id="controller")
    worker_a = DurableQueue(tmp_path, policy=policy, agent_id="worker_a")
    worker_b = DurableQueue(tmp_path, policy=policy, agent_id="worker_b")

    task_a = controller.enqueue(
        destination_id="worker_a", goal="prepare A", references=[], idempotency_key="assignment"
    )
    task_b = controller.enqueue(
        destination_id="worker_b", goal="prepare B", references=[], idempotency_key="assignment"
    )

    claim_a = worker_a.claim()
    claim_b = worker_b.claim()
    assert claim_a and claim_a["task_id"] == task_a
    assert claim_b and claim_b["task_id"] == task_b
    assert worker_a.claim() is None
    assert worker_b.claim() is None

    assert worker_a.finish(
        task_id=task_a, lease_id=claim_a["lease_id"], result="A done", failed=False
    ) == "completed"
    assert worker_b.finish(
        task_id=task_b, lease_id=claim_b["lease_id"], result="B done", failed=False
    ) == "completed"
    assert controller.read_task(task_a)["result"] == "A done"
    assert controller.read_task(task_b)["result"] == "B done"


def test_many_to_many_routes_bind_principals_and_isolate_workers(tmp_path: Path) -> None:
    for method in (DurableQueue.enqueue, DurableQueue.ask_question, DurableQueue.read_answer):
        assert "producer_id" not in inspect.signature(method).parameters
    for method in (
        DurableQueue.claim,
        DurableQueue.finish,
        DurableQueue.claim_question,
        DurableQueue.answer_question,
    ):
        assert "worker_id" not in inspect.signature(method).parameters

    policy = _policy(
        {"controller", "producer_b", "worker_a", "worker_b"},
        {
            ("controller", "worker_a"): {"task"},
            ("controller", "worker_b"): {"task"},
            ("producer_b", "worker_b"): {"task"},
        },
    )
    controller = DurableQueue(tmp_path, policy=policy, agent_id="controller")
    producer_b = DurableQueue(tmp_path, policy=policy, agent_id="producer_b")
    worker_a = DurableQueue(tmp_path, policy=policy, agent_id="worker_a")
    worker_b = DurableQueue(tmp_path, policy=policy, agent_id="worker_b")

    from_controller = controller.enqueue(
        destination_id="worker_b", goal="controller work", references=[], idempotency_key="shared"
    )
    from_producer_b = producer_b.enqueue(
        destination_id="worker_b", goal="producer B work", references=[], idempotency_key="shared"
    )
    assert from_producer_b != from_controller
    assert producer_b.enqueue(
        destination_id="worker_b", goal="retry cannot replace", references=[], idempotency_key="shared"
    ) == from_producer_b

    with pytest.raises(QueueRefused, match="^request refused$"):
        producer_b.enqueue(
            destination_id="worker_a", goal="forbidden", references=[], idempotency_key="shared"
        )
    assert worker_a.claim() is None

    claims = [worker_b.claim(), worker_b.claim()]
    assert all(claims)
    assert {claim["task_id"] for claim in claims if claim} == {from_controller, from_producer_b}
    claim_by_task = {claim["task_id"]: claim for claim in claims if claim}
    with pytest.raises(QueueRefused, match="^request refused$"):
        worker_a.finish(
            task_id=from_controller,
            lease_id=claim_by_task[from_controller]["lease_id"],
            result="stolen",
            failed=False,
        )
    with pytest.raises(QueueRefused, match="^request refused$"):
        worker_a.read_task(from_controller)
    with pytest.raises(QueueRefused, match="^request refused$"):
        producer_b.read_task(from_controller)

    assert worker_b.finish(
        task_id=from_controller,
        lease_id=claim_by_task[from_controller]["lease_id"],
        result="returned to controller",
        failed=False,
    ) == "completed"
    record = controller.read_task(from_controller)
    assert (record["producer_id"], record["worker_id"], record["result"]) == (
        "controller",
        "worker_b",
        "returned to controller",
    )


def test_route_modes_keep_tasks_and_consultations_separate(tmp_path: Path) -> None:
    policy = _policy(
        {"controller", "task_worker", "advisor"},
        {
            ("controller", "task_worker"): {"task"},
            ("controller", "advisor"): {"consultation"},
        },
    )
    controller = DurableQueue(tmp_path, policy=policy, agent_id="controller")
    task_worker = DurableQueue(tmp_path, policy=policy, agent_id="task_worker")
    advisor = DurableQueue(tmp_path, policy=policy, agent_id="advisor")

    task_id = controller.enqueue(
        destination_id="task_worker",
        goal="bounded task",
        references=[],
        idempotency_key="shared-mode-key",
    )
    question = controller.ask_question(
        destination_id="advisor",
        question="Bounded advice?",
        origin_ref="routing-test",
        idempotency_key="shared-mode-key",
        expires_in_seconds=60,
    )
    task_claim = task_worker.claim()
    question_claim = advisor.claim_question()
    assert task_claim and task_claim["task_id"] == task_id
    assert question_claim and question_claim["consultation_id"] == question["consultation_id"]

    with pytest.raises(QueueRefused, match="^request refused$"):
        controller.ask_question(
            destination_id="task_worker",
            question="Wrong mode",
            origin_ref="routing-test",
            idempotency_key="wrong-consultation-mode",
            expires_in_seconds=60,
        )
    with pytest.raises(QueueRefused, match="^request refused$"):
        controller.enqueue(
            destination_id="advisor",
            goal="wrong mode",
            references=[],
            idempotency_key="wrong-task-mode",
        )


def test_terminal_task_replay_remains_bound_to_destination_worker(tmp_path: Path) -> None:
    policy = _policy(
        {"producer", "worker", "other_worker"},
        {("producer", "worker"): {"task"}},
    )
    producer = DurableQueue(tmp_path, policy=policy, agent_id="producer")
    worker = DurableQueue(tmp_path, policy=policy, agent_id="worker")
    other_worker = DurableQueue(tmp_path, policy=policy, agent_id="other_worker")
    task_id = producer.enqueue(
        destination_id="worker", goal="bounded", references=[], idempotency_key="terminal"
    )
    claimed = worker.claim()
    assert claimed
    assert worker.finish(
        task_id=task_id,
        lease_id=claimed["lease_id"],
        result="done",
        failed=False,
    ) == "completed"
    with pytest.raises(QueueRefused, match="^request refused$"):
        other_worker.finish(
            task_id=task_id,
            lease_id=claimed["lease_id"],
            result="done",
            failed=False,
        )
    assert worker.finish(
        task_id=task_id,
        lease_id=claimed["lease_id"],
        result="ignored exact replay",
        failed=False,
    ) == "already_completed"


def test_graph_is_primary_and_explicit_two_agent_pair_is_compatible(tmp_path: Path) -> None:
    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(tmp_path / "implicit")

    queue = DurableQueue(
        tmp_path / "pair", producer_id="producer", worker_id="worker"
    )
    assert queue.policy.as_record() == {
        "agents": ["producer", "worker"],
        "routes": [
            {
                "source": "producer",
                "destination": "worker",
                "modes": ["consultation", "task"],
            }
        ],
    }
    task_id = queue.enqueue(goal="legacy", references=[], idempotency_key="one-edge")
    claimed = queue.claim()
    assert claimed and claimed["task_id"] == task_id


def test_matching_policy_admits_parent_format_records_and_preserves_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    task_id, consultation_id = _create_parent_format_state(root)
    assert task_id and consultation_id
    policy = _policy(
        {"producer", "worker"},
        {("producer", "worker"): {"task", "consultation"}},
    )

    producer = DurableQueue(root, policy=policy, agent_id="producer")
    worker = DurableQueue(root, policy=policy, agent_id="worker")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert producer.read_task(task_id)["status"] == "pending"
    task_claim = worker.claim(now)
    assert task_claim and task_claim["task_id"] == task_id
    assert worker.finish(
        task_id=task_id,
        lease_id=task_claim["lease_id"],
        result="legacy task done",
        failed=False,
        now=now,
    ) == "completed"
    assert producer.read_task(task_id)["result"] == "legacy task done"

    assert producer.read_answer(consultation_id, now=now)["status"] == "pending"
    question_claim = worker.claim_question(now)
    assert question_claim and question_claim["consultation_id"] == consultation_id
    assert worker.answer_question(
        consultation_id=consultation_id,
        lease_id=question_claim["lease_id"],
        answer="legacy answer",
        now=now,
    ) == "answered"
    assert producer.read_answer(consultation_id, now=now)["answer"] == "legacy answer"


@pytest.mark.parametrize(
    ("routes", "include_task", "include_consultation"),
    [
        ({("producer", "other"): {"task", "consultation"}}, True, True),
        ({("producer", "worker"): {"consultation"}}, True, False),
        ({("producer", "worker"): {"task"}}, False, True),
    ],
    ids=("absent-edge", "consultation-only-rejects-task", "task-only-rejects-consultation"),
)
def test_incompatible_policy_refuses_parent_format_before_mutation(
    tmp_path: Path,
    routes: dict[tuple[str, str], set[str]],
    include_task: bool,
    include_consultation: bool,
) -> None:
    root = tmp_path / "parent"
    _create_parent_format_state(
        root,
        include_task=include_task,
        include_consultation=include_consultation,
    )
    before = _parent_record_bytes(root)
    policy = _policy({"producer", "worker", "other"}, routes)

    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(root, policy=policy, agent_id="producer")

    assert not (root / "policy.json").exists()
    assert _parent_record_bytes(root) == before


@pytest.mark.parametrize(
    ("directory", "record_id"),
    (
        ("tasks", "task_" + "1" * 32),
        ("consultations", "consultation_" + "2" * 32),
    ),
    ids=("task", "consultation"),
)
def test_non_object_parent_record_is_refused_before_mutation(
    tmp_path: Path, directory: str, record_id: str
) -> None:
    root = tmp_path / "parent"
    _create_parent_format_state(root)
    (root / directory / f"{record_id}.json").write_text("[]", encoding="utf-8")
    before = _parent_record_bytes(root)
    policy = _policy(
        {"producer", "worker"},
        {("producer", "worker"): {"task", "consultation"}},
    )

    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(root, policy=policy, agent_id="producer")

    assert not (root / "policy.json").exists()
    assert _parent_record_bytes(root) == before


@pytest.mark.parametrize(
    ("directory", "record_id", "field"),
    (
        *(
            ("tasks", "task_" + "1" * 32, field)
            for field in (
                "task_id",
                "goal",
                "status",
                "lease_id",
                "lease_expires_at",
                "result",
            )
        ),
        *(
            ("consultations", "consultation_" + "2" * 32, field)
            for field in (
                "consultation_id",
                "origin_ref",
                "idempotency_key",
                "question",
                "status",
                "created_at",
                "expires_at",
                "lease_id",
                "lease_expires_at",
                "claimed_at",
                "answer",
                "answered_at",
            )
        ),
    ),
)
def test_parent_record_requires_expected_scalar_types_before_policy_write(
    tmp_path: Path, directory: str, record_id: str, field: str
) -> None:
    root = tmp_path / "parent"
    _create_parent_format_state(root)
    record_path = root / directory / f"{record_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record[field] = 7
    _write_parent_record(record_path, record)
    before = _parent_record_bytes(root)
    policy = _policy(
        {"producer", "worker"},
        {("producer", "worker"): {"task", "consultation"}},
    )

    with pytest.raises(QueueRefused, match="^request refused$"):
        DurableQueue(root, policy=policy, agent_id="producer")

    assert not (root / "policy.json").exists()
    assert _parent_record_bytes(root) == before
