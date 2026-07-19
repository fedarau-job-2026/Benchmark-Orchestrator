# 001 — Ship a mock backend alongside Ollama

## Context
Real Ollama inference is slow to iterate against and requires a model pull. The rate
limiter — the part the assignment actually wants to see — is independent of what
generates the text.

## Options considered
- **Ollama only**: simplest code; every test run pays model latency, and the service
  cannot be exercised at all until Ollama is up.
- **Mock only**: fastest iteration; leaves the primary deliverable unvalidated.
- **Both behind one interface**: `BACKEND=ollama|mock` env switch over a common ABC.

## Decision
Two backends behind a small ABC: `OllamaBackend` (default) and `MockBackend`
(`asyncio.sleep` with configurable mean/jitter latency, canned response text).

## Consequences / tradeoffs
- Rate-limiter and orchestrator development/tests run instantly and deterministically
  in mock mode; reviewers without a pulled model can still watch the system work.
- Accuracy is meaningless in mock mode (canned text) — documented; final accuracy
  numbers come from real-model runs.
