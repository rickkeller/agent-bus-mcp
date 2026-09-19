from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path

import pytest

import agent_bus_mcp
from agent_bus_mcp.queue import DurableQueue, QueueRefused


def _policy(agents: set[str], routes: dict[tuple[str, str], set[str]]):
    policy_type = getattr(agent_bus_mcp, "RoutePolicy", None)
    assert policy_type is not None, "RoutePolicy must be public"
    return policy_type(agents=agents, routes=routes)


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
