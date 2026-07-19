"""Live observability: Rich TUI when attached to a terminal, log lines otherwise.

What you'd want on screen if a customer were watching over your shoulder:
progress + ETA, live throughput, latency percentiles, what the controller
currently believes about the endpoint's limits, and failures as they happen.
"""

import sys
import time
from collections import deque

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .controller import AdaptiveController
from .metrics import RunMetrics
from .runner import QueryResult, RunnerEvents


class Dashboard:
    """Subscribes to runner events; renders either a live TUI or periodic logs."""

    def __init__(
        self,
        metrics: RunMetrics,
        controller: AdaptiveController,
        events: RunnerEvents,
        total_queries: int,
        use_tui: bool | None = None,
    ):
        self.metrics = metrics
        self.controller = controller
        self.total_queries = total_queries
        self.console = Console()
        self.use_tui = use_tui if use_tui is not None else sys.stdout.isatty()
        self._recent: deque[str] = deque(maxlen=6)
        self._completed_timestamps: deque[float] = deque(maxlen=200)
        self._last_log = 0.0

        self.overall = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        )
        self.overall_task = self.overall.add_task("queue", total=total_queries)
        self.per_bench = Progress(
            TextColumn("{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
        )
        self._bench_tasks: dict[str, int] = {}

        events.on_result.append(self._on_result)
        events.on_429.append(self._on_429)

    def register_benchmark(self, benchmark_id: str, total: int) -> None:
        self._bench_tasks[benchmark_id] = self.per_bench.add_task(benchmark_id, total=total)

    # --- event handlers ---

    def _on_result(self, result: QueryResult) -> None:
        now = time.monotonic()
        self._completed_timestamps.append(now)
        self.overall.advance(self.overall_task)
        self.per_bench.advance(self._bench_tasks[result.task.benchmark_id])
        if not result.ok:
            self._recent.append(
                f"[red]FAIL[/red] {result.task.benchmark_id}/{result.task.query_id}: {result.error[:60]}"
            )
        if not self.use_tui:
            self._maybe_log(now)

    def _on_429(self, task, outcome) -> None:
        self.metrics.record_429()

    def _maybe_log(self, now: float) -> None:
        if now - self._last_log >= 5.0:
            self._last_log = now
            s = self.controller.stats()
            self.console.print(
                f"[{time.strftime('%H:%M:%S')}] "
                f"{self.metrics.completed}/{self.total_queries} done | "
                f"{self._live_rps():.2f} req/s | p50 {self.metrics.p50_s:.2f}s "
                f"p95 {self.metrics.p95_s:.2f}s | 429s {self.metrics.http_429_count} | "
                f"fail {self.metrics.failed} | "
                f"window {int(s.concurrency_limit)} | "
                + (f"learned {s.last_learned_rpm} rpm (pacer {s.rate:.2f}/s)" if s.last_learned_rpm else "no rpm 429 yet")
            )

    # --- rendering ---

    def _live_rps(self) -> float:
        """Throughput over the last 30s — reacts to limit changes, unlike the run average."""
        now = time.monotonic()
        recent = [t for t in self._completed_timestamps if now - t <= 30.0]
        if len(recent) < 2:
            return self.metrics.throughput_rps
        span = now - recent[0]
        return len(recent) / span if span > 0 else 0.0

    def _stats_table(self) -> Table:
        s = self.controller.stats()
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim")
        t.add_column(justify="right")
        t.add_column(style="dim")
        t.add_column(justify="right")
        paused = max(0.0, s.paused_until - time.monotonic())
        t.add_row(
            "live req/s", f"{self._live_rps():.2f}",
            "in-flight / window", f"{s.in_flight} / {int(s.concurrency_limit)}",
        )
        learned = (
            f"{s.last_learned_rpm} rpm (pacer {s.rate:.2f}/s)"
            if s.last_learned_rpm
            else "no rpm limit hit yet"
        )
        t.add_row(
            "p50 / p95 latency", f"{self.metrics.p50_s:.2f}s / {self.metrics.p95_s:.2f}s",
            "learned rate", learned,
        )
        t.add_row(
            "429s (rpm/conc/?)",
            f"{s.total_429_rpm}/{s.total_429_concurrency}/{s.total_429_unknown}",
            "paused", f"{paused:.1f}s" if paused > 0 else "—",
        )
        t.add_row(
            "failures", f"[red]{self.metrics.failed}[/red]" if self.metrics.failed else "0",
            "accuracy so far",
            f"{self.metrics.accuracy*100:.1f}%" if self.metrics.completed else "—",
        )
        return t

    def _render(self) -> Group:
        parts = [
            Panel(self.overall, title="progress", border_style="blue"),
            Panel(self._stats_table(), title="live stats", border_style="cyan"),
            Panel(self.per_bench, title="benchmarks", border_style="dim"),
        ]
        if self._recent:
            parts.append(
                Panel(Group(*[Text.from_markup(m) for m in self._recent]),
                      title="recent failures", border_style="red")
            )
        return Group(*parts)

    def live(self) -> Live:
        return Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
            get_renderable=self._render,
        )
