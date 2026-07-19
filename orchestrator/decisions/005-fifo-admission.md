# 005 — FIFO admission at the dispatch gate

## Context
The first full real-model run (200 queries, RPM=60, concurrency 4) surfaced a
fairness problem: 64 workers raced a condition-variable gate, so admission order
was random. A task could lose the race for minutes (run_002's first dispatch waited
~2 minutes; run_001 stragglers finished at the very end of the run), which inflated
per-benchmark wall times and made the per-benchmark metrics noisy.

## Options considered
- **Keep the racing gate**: fair on average, high variance; simplest code.
- **Fewer workers**: reduces queue depth but caps peak concurrency the controller
  might discover.
- **FIFO ticket queue in the controller**: each `acquire()` enqueues a future; a
  pump admits strictly in order whenever tokens/slots/pause allow.

## Decision
FIFO ticket queue (`controller.acquire()` + `_pump()`): waiters are admitted in
arrival order; the pump runs on every release/feedback and self-schedules a timer
for the earliest time a constraint might clear. Cancelled waiters hand back their
slot.

## Consequences / tradeoffs
- Tasks complete in near queue order → benchmarks finish sequentially and their
  wall-time metrics mean what they say; retried tasks can't be starved.
- The controller owns all scheduling state; workers stay trivial.
- One subtle invariant: any state change that could unblock dispatch must call
  `_pump()` — covered by release/429/pause paths and the fallback timer.
