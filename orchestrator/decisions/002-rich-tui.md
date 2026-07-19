# 002 — Rich live TUI for observability

## Context
Observability is an explicit evaluation axis; the assignment notes a good terminal
live view often beats a half-built web dashboard.

## Options considered
- **Structured log lines + heartbeat**: cheap, CI-friendly, unimpressive live.
- **Web dashboard**: high effort, explicitly called out as a trap.
- **Rich `Live` TUI with plain-log fallback**: full live view for humans, logs for CI.

## Decision
Rich `Live` dashboard: overall + per-benchmark progress bars with ETA, live req/s,
p50/p95 latency, in-flight vs current AIMD window, learned RPM estimate, 429/failure
counters, recent-events tail. Automatically falls back to periodic plain log lines
when stdout is not a TTY or `--no-tui` is passed.

## Consequences / tradeoffs
- `tty: true` needed in compose for the containerized run — documented.
- The fallback keeps CI logs and piped output clean.
