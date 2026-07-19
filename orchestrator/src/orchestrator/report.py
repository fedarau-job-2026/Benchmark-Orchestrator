"""Results report: one JSON summary + one per-query detail CSV per run."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .controller import AdaptiveController
from .metrics import RunMetrics
from .runner import QueryResult


def write_report(
    out_dir: Path,
    run_id: str,
    metrics: RunMetrics,
    results: list[QueryResult],
    controller: AdaptiveController,
    queue_path: str,
    inference_url: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = controller.stats()

    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue_file": queue_path,
        "inference_url": inference_url,
        # --- assignment-mandated minimum ---
        "total_wall_time_s": round(metrics.wall_time_s, 3),
        "per_benchmark": [
            {
                "benchmark_id": b.benchmark_id,
                "wall_time_s": round(b.wall_time_s, 3),
                "queries": b.total_queries,
                "completed": b.completed,
                "failed": b.failed,
                "accuracy": round(b.correct / max(1, b.completed - b.failed), 4)
                if b.completed > b.failed
                else 0.0,
            }
            for b in metrics.benchmarks.values()
        ],
        "request_latency_p50_s": round(metrics.p50_s, 3),
        "request_latency_p95_s": round(metrics.p95_s, 3),
        "throughput_rps": round(metrics.throughput_rps, 3),
        "total_http_requests": metrics.total_http_requests,
        "failure_count": metrics.failed,
        "overall_accuracy": round(metrics.accuracy, 4),
        # --- extras that make the run explainable ---
        "completed_queries": metrics.completed,
        "http_429_count": metrics.http_429_count,
        "discovered_limits": {
            "concurrency_window": stats.concurrency_limit,
            # Only meaningful if the RPM limit was actually hit; otherwise the
            # endpoint was slower than its own rate limit (model-bound).
            "rpm_limit_observed": stats.total_429_rpm > 0,
            # Evidence captured at the last rpm-429, not the end-of-run creep value.
            "learned_rpm": stats.last_learned_rpm,
            "final_pacer_rps": round(stats.rate, 3),
            "429s_rpm": stats.total_429_rpm,
            "429s_concurrency": stats.total_429_concurrency,
            "429s_unknown": stats.total_429_unknown,
        },
    }

    summary_path = out_dir / f"results_{run_id}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    detail_path = out_dir / f"results_{run_id}_queries.csv"
    with open(detail_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["benchmark_id", "query_id", "ok", "correct", "latency_s", "attempts", "response", "error"]
        )
        for r in results:
            w.writerow(
                [
                    r.task.benchmark_id,
                    r.task.query_id,
                    r.ok,
                    r.correct,
                    f"{r.latency_s:.3f}",
                    r.attempts,
                    r.response[:200].replace("\n", " "),
                    r.error,
                ]
            )
    return summary_path
