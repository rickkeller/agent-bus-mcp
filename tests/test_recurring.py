from datetime import datetime, timezone
from pathlib import Path

from agent_bus_mcp.queue import DurableQueue
from agent_bus_mcp.recurring import bucket_for, enqueue_due


def test_buckets_and_recurring_idempotency(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path, producer_id="producer", worker_id="worker")
    now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
    first = enqueue_due(queue, "weekly", now)
    assert first == enqueue_due(queue, "weekly", now)
    assert bucket_for("daily", now) == "2026-09-13"
    assert bucket_for("weekly", now) == "2026-W37"
    assert bucket_for("biweekly", now) == "2026-BW19"
    assert bucket_for("monthly", now) == "2026-09"
