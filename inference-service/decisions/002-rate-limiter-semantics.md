# 002 — Rate limiter semantics

## Context
The assignment mandates: configurable RPM (default 60), max concurrent in-flight
(default 4), HTTP 429 + `Retry-After` on breach, no silent dropping, no internal
queueing, and the limiter must be hand-built (not delegated to Ollama).

## Options considered
- **RPM accounting**: fixed windows (bursty at boundaries) vs token bucket (allows
  bursts above the per-minute cap) vs **sliding 60s window** of accepted-request
  timestamps — the most literal reading of "max requests per minute".
- **Do rejected requests consume window slots?** Counting them means a hammering
  client can lock itself out forever; not counting them matches how most production
  limiters (and HTTP semantics: the request was refused, not served) behave.
- **Retry-After for concurrency breaches**: the true wait time is unknowable (depends
  on model latency), so any value is a heuristic.

## Decision
- RPM: sliding window — deque of the timestamps of *accepted* requests in the trailing
  60 s; reject when full. Rejected requests do **not** consume slots.
- Concurrency: atomic in-flight counter checked before the backend is touched.
- On breach: immediate 429 with `Retry-After` (RPM: ceil of seconds until the oldest
  timestamp ages out; concurrency: `1` as a polite retry hint) and an advisory
  `X-RateLimit-Reason: rpm|concurrency` header.
- Check-and-reject happens before any backend work; nothing queues inside the service.

## Consequences / tradeoffs
- `X-RateLimit-Reason` mirrors real-world `x-ratelimit-*` headers; the orchestrator
  treats it as an optional hint only, so the discovery logic stays honest.
- Sliding window needs a timestamp deque per process — trivial at these scales.
- Concurrency `Retry-After: 1` is heuristic; clients must still adapt (ours does).
