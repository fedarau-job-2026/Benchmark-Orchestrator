"""End-to-end over real HTTP against the contract fake: every query completes,
the controller respects and discovers the limits, the report has every
assignment-mandated field.
"""

import asyncio
import json
import socket

import pytest

from orchestrator.client import InferenceClient
from orchestrator.controller import AdaptiveController
from orchestrator.jobs import expand_all, parse_queue
from orchestrator.metrics import RunMetrics
from orchestrator.report import write_report
from orchestrator.runner import Runner, RunnerEvents

from .fake_server import FakeInferenceServer, serve
from .test_units import write_fixtures


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_queue(tmp_path, server: FakeInferenceServer, rows=5):
    """Boot the fake server, push a 2-benchmark queue through it, return artifacts."""
    port = free_port()
    uv, task = await serve(server, port)
    try:
        queue_path = write_fixtures(tmp_path, rows=rows)
        specs = parse_queue(queue_path)
        by_bench = expand_all(specs, queue_path.parent)
        all_tasks = [t for ts in by_bench.values() for t in ts]

        client = InferenceClient(f"http://127.0.0.1:{port}")
        assert await client.healthy()

        controller = AdaptiveController()
        events = RunnerEvents()
        metrics = RunMetrics()
        for bid, ts in by_bench.items():
            metrics.benchmark(bid, len(ts))
        events.on_attempt.append(
            lambda t: (metrics.record_attempt(), metrics.mark_benchmark_started(t.benchmark_id))
        )
        events.on_429.append(lambda t, o: metrics.record_429())
        events.on_result.append(
            lambda r: metrics.record_result(r.task.benchmark_id, r.latency_s, r.correct)
            if r.ok
            else metrics.record_failure(r.task.benchmark_id)
        )

        results = await Runner(client, controller, events).run(all_tasks)
        import time as _time

        metrics.finished_at = _time.monotonic()
        await client.close()
        return results, metrics, controller
    finally:
        uv.should_exit = True
        await task


async def test_loose_limits_all_complete(tmp_path):
    server = FakeInferenceServer(rpm=10_000, max_concurrency=8, latency_s=0.02)
    results, metrics, controller = await run_queue(tmp_path, server, rows=10)
    assert len(results) == 20
    assert all(r.ok for r in results)
    assert metrics.completed == 20 and metrics.failed == 0
    # fake answers echo the prompt, so grading finds no expected answers
    assert metrics.accuracy == 0.0
    # server never saw more concurrency than its cap
    assert server.max_observed_inflight <= 8


async def test_tight_concurrency_discovered_and_respected(tmp_path):
    # High latency so the concurrency cap (not the rate) is the binding constraint.
    server = FakeInferenceServer(rpm=10_000, max_concurrency=2, latency_s=0.3)
    results, metrics, controller = await run_queue(tmp_path, server, rows=15)
    assert all(r.ok for r in results)          # 429s retried, never fatal
    assert metrics.failed == 0
    s = controller.stats()
    assert s.concurrency_limit <= 4            # converged near the real cap of 2
    assert server.max_observed_inflight <= 2   # never breached


async def test_tight_rpm_survives_and_completes(tmp_path):
    # 60 rpm with 10 queries: forces at least a brush with the rate limiter
    server = FakeInferenceServer(rpm=8, max_concurrency=8, latency_s=0.01)
    results, metrics, controller = await run_queue(tmp_path, server, rows=5)
    assert len(results) == 10
    assert all(r.ok for r in results)
    assert metrics.http_429_count == server.total_429
    assert controller.stats().total_429_rpm >= 1  # actually hit the rpm limit


async def test_report_contains_all_mandated_fields(tmp_path):
    server = FakeInferenceServer(rpm=10_000, max_concurrency=4, latency_s=0.01)
    results, metrics, controller = await run_queue(tmp_path, server, rows=3)
    path = write_report(
        tmp_path / "out", "testrun", metrics, results, controller,
        queue_path="queue.jsonl", inference_url="http://fake",
    )
    report = json.loads(path.read_text())
    for field in [
        "total_wall_time_s", "per_benchmark", "request_latency_p50_s",
        "request_latency_p95_s", "throughput_rps", "total_http_requests",
        "failure_count", "overall_accuracy",
    ]:
        assert field in report, f"missing mandated field {field}"
    assert len(report["per_benchmark"]) == 2
    for b in report["per_benchmark"]:
        assert {"benchmark_id", "wall_time_s", "queries", "completed", "failed", "accuracy"} <= b.keys()
    assert (tmp_path / "out" / "results_testrun_queries.csv").exists()
