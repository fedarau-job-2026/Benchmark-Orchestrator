"""Run metrics: latency percentiles, throughput, per-benchmark aggregation."""

import math
import time
from dataclasses import dataclass, field


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile (1-based ceil); 0.0 for an empty list."""
    if not sorted_values:
        return 0.0
    k = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[min(k, len(sorted_values)) - 1]


@dataclass
class BenchmarkMetrics:
    benchmark_id: str
    total_queries: int = 0
    completed: int = 0
    correct: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def wall_time_s(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


@dataclass
class RunMetrics:
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    latencies_s: list[float] = field(default_factory=list)  # successful requests only
    total_http_requests: int = 0    # every attempt, including 429s and errors
    http_429_count: int = 0
    completed: int = 0              # queries with a final answer
    correct: int = 0
    failed: int = 0                 # queries abandoned after retries
    benchmarks: dict[str, BenchmarkMetrics] = field(default_factory=dict)

    # --- recording ---

    def benchmark(self, benchmark_id: str, total_queries: int) -> BenchmarkMetrics:
        if benchmark_id not in self.benchmarks:
            self.benchmarks[benchmark_id] = BenchmarkMetrics(
                benchmark_id=benchmark_id, total_queries=total_queries
            )
        return self.benchmarks[benchmark_id]

    def record_attempt(self) -> None:
        self.total_http_requests += 1

    def record_429(self) -> None:
        self.http_429_count += 1

    def record_result(self, benchmark_id: str, latency_s: float, correct: bool) -> None:
        self.latencies_s.append(latency_s)
        self.completed += 1
        b = self.benchmarks[benchmark_id]
        b.completed += 1
        if correct:
            self.correct += 1
            b.correct += 1
        self._maybe_finish_benchmark(b)

    def record_failure(self, benchmark_id: str) -> None:
        self.failed += 1
        b = self.benchmarks[benchmark_id]
        b.completed += 1
        b.failed += 1
        self._maybe_finish_benchmark(b)

    def mark_benchmark_started(self, benchmark_id: str) -> None:
        b = self.benchmarks[benchmark_id]
        if b.started_at is None:
            b.started_at = time.monotonic()

    def _maybe_finish_benchmark(self, b: BenchmarkMetrics) -> None:
        if b.completed >= b.total_queries and b.finished_at is None:
            b.finished_at = time.monotonic()

    # --- derived ---

    @property
    def wall_time_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    @property
    def p50_s(self) -> float:
        return percentile(sorted(self.latencies_s), 50)

    @property
    def p95_s(self) -> float:
        return percentile(sorted(self.latencies_s), 95)

    @property
    def throughput_rps(self) -> float:
        wall = self.wall_time_s
        return self.completed / wall if wall > 0 else 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.completed if self.completed else 0.0
