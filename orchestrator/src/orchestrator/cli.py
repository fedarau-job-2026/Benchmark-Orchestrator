"""CLI: orchestrator <queue.jsonl> [--out DIR] [--url URL] [--no-tui]"""

import argparse
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

from .client import InferenceClient
from .controller import AdaptiveController
from .jobs import expand_all, parse_queue
from .metrics import RunMetrics
from .report import write_report
from .runner import Runner, RunnerEvents
from .tui import Dashboard


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Run a benchmark job queue against a rate-limited inference service, "
        "discovering its limits automatically.",
    )
    parser.add_argument("queue", type=Path, help="path to the queue .jsonl file")
    parser.add_argument(
        "--url",
        default=os.environ.get("INFERENCE_URL", "http://localhost:8080"),
        help="inference service base URL (env: INFERENCE_URL, default: %(default)s)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("results"), help="output directory (default: %(default)s)"
    )
    parser.add_argument("--no-tui", action="store_true", help="plain log lines instead of the live TUI")
    args = parser.parse_args()

    if not args.queue.exists():
        parser.error(f"queue file not found: {args.queue}")

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted — partial results were not written", file=sys.stderr)
        sys.exit(130)


async def run(args) -> None:
    specs = parse_queue(args.queue)
    tasks_by_bench = expand_all(specs, args.queue.resolve().parent)
    all_tasks = [t for tasks in tasks_by_bench.values() for t in tasks]
    if not all_tasks:
        print("queue expanded to zero query tasks — nothing to do", file=sys.stderr)
        sys.exit(1)

    client = InferenceClient(args.url)
    # Tolerate a service that is still booting (e.g. compose starting both sides).
    deadline = time.monotonic() + 30.0
    waited = False
    while not await client.healthy():
        if time.monotonic() >= deadline:
            print(
                f"error: inference service at {args.url} did not answer /healthz within 30s\n"
                f"       start it first (see README) or pass --url",
                file=sys.stderr,
            )
            await client.close()
            sys.exit(2)
        if not waited:
            print(f"waiting for inference service at {args.url} ...", file=sys.stderr)
            waited = True
        await asyncio.sleep(1.0)

    controller = AdaptiveController()
    events = RunnerEvents()
    metrics = RunMetrics()
    for bench_id, tasks in tasks_by_bench.items():
        metrics.benchmark(bench_id, len(tasks))

    dashboard = Dashboard(
        metrics, controller, events,
        total_queries=len(all_tasks),
        use_tui=(not args.no_tui) and sys.stdout.isatty(),
    )
    for bench_id, tasks in tasks_by_bench.items():
        dashboard.register_benchmark(bench_id, len(tasks))

    # Wire metrics into runner events
    events.on_attempt.append(lambda task: (metrics.record_attempt(), metrics.mark_benchmark_started(task.benchmark_id)))
    events.on_result.append(
        lambda r: metrics.record_result(r.task.benchmark_id, r.latency_s, r.correct)
        if r.ok
        else metrics.record_failure(r.task.benchmark_id)
    )

    runner = Runner(client, controller, events)
    run_id = time.strftime("%Y%m%d_%H%M%S")

    print(f"run {run_id}: {len(specs)} benchmarks, {len(all_tasks)} queries -> {args.url}")
    ctx = dashboard.live() if dashboard.use_tui else contextlib.nullcontext()
    with ctx:
        results = await runner.run(all_tasks)
    metrics.finished_at = time.monotonic()
    await client.close()

    summary_path = write_report(
        args.out, run_id, metrics, results, controller, str(args.queue), args.url
    )
    s = controller.stats()
    rate_note = (
        f"rpm≈{s.last_learned_rpm}"
        if s.last_learned_rpm
        else "rpm limit never hit (endpoint was model-bound)"
    )
    print(
        f"\ndone in {metrics.wall_time_s:.1f}s | "
        f"{metrics.completed} completed, {metrics.failed} failed | "
        f"throughput {metrics.throughput_rps:.2f} req/s | "
        f"p50 {metrics.p50_s:.2f}s p95 {metrics.p95_s:.2f}s | "
        f"accuracy {metrics.accuracy*100:.1f}% | "
        f"429s {metrics.http_429_count}\n"
        f"discovered: concurrency≈{int(s.concurrency_limit)}, {rate_note}\n"
        f"report: {summary_path}"
    )


if __name__ == "__main__":
    main()
