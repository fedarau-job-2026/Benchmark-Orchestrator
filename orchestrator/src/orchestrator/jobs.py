"""Queue parsing and the job-type registry.

A queue is a JSONL file; each line is a job spec with an optional "type"
(default "benchmark_csv"). A job type is a handler that expands a spec into
QueryTasks and grades responses — adding a new job type is one registry entry.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .grading import is_correct


@dataclass(frozen=True)
class JobSpec:
    benchmark_id: str
    type: str
    payload: dict  # the raw JSONL object, handler-specific


@dataclass(frozen=True)
class QueryTask:
    benchmark_id: str
    query_id: str
    prompt: str
    expected: str
    job_type: str = "benchmark_csv"  # which registry handler grades this task


class JobHandler(Protocol):
    def expand(self, spec: JobSpec, base_dir: Path) -> list[QueryTask]: ...
    def grade(self, task: QueryTask, response: str) -> bool: ...


class BenchmarkCsvHandler:
    """Job type "benchmark_csv": run every row of a QA CSV through the model."""

    def expand(self, spec: JobSpec, base_dir: Path) -> list[QueryTask]:
        csv_path = Path(spec.payload["csv_path"])
        if not csv_path.is_absolute():
            csv_path = base_dir / csv_path  # relative to the queue file
        tasks = []
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                tasks.append(
                    QueryTask(
                        benchmark_id=spec.benchmark_id,
                        query_id=row["id"],
                        prompt=row["question"],
                        expected=row["expected_answer"],
                        job_type=spec.type,
                    )
                )
        return tasks

    def grade(self, task: QueryTask, response: str) -> bool:
        return is_correct(task.expected, response)


REGISTRY: dict[str, JobHandler] = {
    "benchmark_csv": BenchmarkCsvHandler(),
}


def parse_queue(queue_path: Path) -> list[JobSpec]:
    specs = []
    with open(queue_path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            job_type = obj.get("type", "benchmark_csv")
            if job_type not in REGISTRY:
                raise ValueError(f"{queue_path}:{n}: unknown job type {job_type!r}")
            if "benchmark_id" not in obj:
                raise ValueError(f"{queue_path}:{n}: missing benchmark_id")
            specs.append(JobSpec(benchmark_id=obj["benchmark_id"], type=job_type, payload=obj))
    return specs


def expand_all(specs: list[JobSpec], base_dir: Path) -> dict[str, list[QueryTask]]:
    """benchmark_id -> its query tasks, preserving queue order."""
    return {s.benchmark_id: REGISTRY[s.type].expand(s, base_dir) for s in specs}
