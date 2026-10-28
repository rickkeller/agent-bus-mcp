from pathlib import Path

import pytest

from agent_bus_mcp.mcp import TOOLS
from agent_bus_mcp.queue import DurableQueue, QueueRefused


def test_coding_request_is_a_local_task_not_a_question(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    task_id = queue.submit_coding_request(
        goal="Fix the lost update.",
        references=[],
        idempotency_key="review-1",
    )
    again = queue.submit_coding_request(
        goal="A different goal must not replace the first.",
        references=[],
        idempotency_key="review-1",
    )
    assert again == task_id
    record = queue.read_coding_request("review-1")
    assert record["task_id"] == task_id
    assert record["status"] == "pending"
    assert record["goal"] == "Fix the lost update."
    assert list((tmp_path / "consultations").glob("consultation_*.json")) == []
    assert [tool["name"] for tool in TOOLS] == [
        "claim_task",
        "complete_task",
        "fail_task",
        "claim_question",
        "answer_question",
    ]
    with pytest.raises(QueueRefused):
        queue.read_coding_request("missing")
