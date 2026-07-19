# 003 — Retry policy: 429 is traffic, errors are failures

## Context
Under discovered limits, 429s are a *normal* part of probing — punishing them like
errors would make the run fragile. Genuine failures (5xx, timeouts) are different:
retrying forever would hide real breakage and stall the queue.

## Options considered
- **Uniform retry budget for everything**: simple, but either abandons queries during
  routine rate limiting or retries hard failures absurdly long.
- **Split policies**: 429 → wait `Retry-After` (+ per-task jitter against thundering
  herd) and retry up to a generous cap (100); 5xx/transport → exponential backoff,
  3 attempts, then record the query as failed and move on.

## Decision
Split policies as above. A failed query never aborts the run — it is counted in
`failure_count` and listed in the per-query CSV. Every attempt (success, 429, error)
feeds the AIMD controller, so backoff state is shared across all workers rather than
per-request.

## Consequences / tradeoffs
- The run always terminates with a report, even against a flaky backend.
- The Retry-After pause is enforced globally in the controller (one gate), not
  per-worker — a hundred queued retries produce one coordinated pause, not a storm.
