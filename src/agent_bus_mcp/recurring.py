"""Deterministic producer: it enqueues public-reference research, and nothing else."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .queue import DurableQueue, QueueRefused

CADENCES = frozenset(("daily", "weekly", "biweekly", "monthly"))


def bucket_for(cadence: str, now: datetime) -> str:
    if cadence not in CADENCES or now.tzinfo is None:
        raise QueueRefused()
    instant = now.astimezone(timezone.utc)
    if cadence == "daily":
        return instant.date().isoformat()
    if cadence == "monthly":
        return f"{instant.year:04d}-{instant.month:02d}"
    year, week, _ = instant.isocalendar()
    return f"{year:04d}-W{week:02d}" if cadence == "weekly" else f"{year:04d}-BW{(week + 1) // 2:02d}"


def manifest() -> list[dict[str, str]]:
    value = json.loads(Path(__file__).with_name("research_manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(value, list) or not all(isinstance(item, dict) and
            set(item) == {"topic", "cadence", "focus", "reference"} and
            all(isinstance(field, str) and field for field in item.values())
            for item in value)):
        raise QueueRefused()
    return value


def enqueue_due(queue: DurableQueue, cadence: str, now: datetime, *, producer_id: str | None = None) -> tuple[str, ...]:
    bucket = bucket_for(cadence, now)
    tickets: list[str] = []
    for item in manifest():
        if item["cadence"] not in CADENCES:
            raise QueueRefused()
        if item["cadence"] != cadence:
            continue
        goal = f"PUBLIC_RESEARCH_V1\ntopic: {item['topic']}\nscope: {item['focus']}\nUse only public sources. Return source, date, relevance, confidence, limits, and a concise NO_ACTION result when appropriate. Do not run tools, change systems, access local data, or perform outreach."
        tickets.append(queue.enqueue(goal=goal, references=[item["reference"]], idempotency_key=f"public-research-v1-{item['topic']}-{bucket}", producer_id=producer_id))
    return tuple(tickets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="enqueue deterministic public-research tasks")
    parser.add_argument("--cadence", choices=sorted(CADENCES), required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--producer-id", required=True)
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args(argv)
    queue = DurableQueue(Path(args.state_dir), producer_id=args.producer_id, worker_id=args.worker_id)
    print(json.dumps({"tasks": enqueue_due(queue, args.cadence, datetime.now(timezone.utc), producer_id=args.producer_id)}, separators=(",", ":")))
    return 0
