"""The dispatch loop: workers pull query tasks, the controller gates every attempt.

Retry policy (decisions/003-retry-policy.md):
- 429 is expected traffic, not an error: notify the controller, wait Retry-After
  (+ small jitter to avoid thundering herd), retry — up to a generous cap.
- 5xx / transport errors: exponential backoff, few attempts, then the query is
  recorded as failed. The run always completes; failures are counted, not fatal.
"""

import asyncio
import random
from dataclasses import dataclass, field

from .client import InferenceClient, Outcome, Status
from .controller import AdaptiveController
from .jobs import QueryTask, REGISTRY

MAX_RATE_LIMIT_RETRIES = 100   # effectively "never give up" for a well-behaved server
MAX_ERROR_RETRIES = 3
ERROR_BACKOFF_BASE_S = 0.5
WORKER_POOL_SIZE = 64          # upper bound only; real concurrency is the controller's


@dataclass
class QueryResult:
    task: QueryTask
    ok: bool
    correct: bool = False
    response: str = ""
    latency_s: float = 0.0
    attempts: int = 0
    error: str = ""


@dataclass
class RunnerEvents:
    """Callbacks the TUI/metrics subscribe to. All are sync and cheap."""
    on_attempt: list = field(default_factory=list)
    on_429: list = field(default_factory=list)
    on_result: list = field(default_factory=list)   # QueryResult

    def emit(self, listeners: list, *args) -> None:
        for fn in listeners:
            fn(*args)


class Runner:
    def __init__(
        self,
        client: InferenceClient,
        controller: AdaptiveController,
        events: RunnerEvents | None = None,
    ):
        self.client = client
        self.controller = controller
        self.events = events or RunnerEvents()

    async def run(self, tasks: list[QueryTask]) -> list[QueryResult]:
        queue: asyncio.Queue[QueryTask] = asyncio.Queue()
        for t in tasks:
            queue.put_nowait(t)
        results: list[QueryResult] = []

        async def worker() -> None:
            while True:
                try:
                    task = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                results.append(await self._run_query(task))
                queue.task_done()

        pool = min(WORKER_POOL_SIZE, len(tasks)) or 1
        await asyncio.gather(*[worker() for _ in range(pool)])
        return results

    async def _run_query(self, task: QueryTask) -> QueryResult:
        handler = REGISTRY[task.job_type]
        attempts = 0
        error_retries = 0
        rate_limit_retries = 0

        while True:
            await self.controller.acquire()
            attempts += 1
            self.events.emit(self.events.on_attempt, task)
            outcome: Outcome = await self.client.generate(task.prompt)

            if outcome.status is Status.OK:
                self.controller.on_accepted()
                self.controller.on_success()
                correct = handler.grade(task, outcome.text)
                result = QueryResult(
                    task=task, ok=True, correct=correct,
                    response=outcome.text, latency_s=outcome.latency_s, attempts=attempts,
                )
                self.events.emit(self.events.on_result, result)
                return result

            if outcome.status is Status.RATE_LIMITED:
                self.controller.on_rate_limited(outcome.reason, outcome.retry_after)
                self.events.emit(self.events.on_429, task, outcome)
                rate_limit_retries += 1
                if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                    result = QueryResult(
                        task=task, ok=False, attempts=attempts,
                        error=f"gave up after {rate_limit_retries} rate-limit retries",
                    )
                    self.events.emit(self.events.on_result, result)
                    return result
                # The controller's pause gate handles the Retry-After wait globally;
                # add per-task jitter so retries don't stampede in lockstep.
                await asyncio.sleep(random.uniform(0.0, 0.25))
                continue

            # ERROR: the server accepted the request (it consumed a slot) but failed.
            self.controller.on_accepted()
            self.controller.on_error()
            error_retries += 1
            if error_retries > MAX_ERROR_RETRIES:
                result = QueryResult(
                    task=task, ok=False, attempts=attempts, error=outcome.error
                )
                self.events.emit(self.events.on_result, result)
                return result
            backoff = ERROR_BACKOFF_BASE_S * (2 ** (error_retries - 1))
            await asyncio.sleep(backoff + random.uniform(0, backoff))
