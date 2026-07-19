# 005 — Build order: system under test first, probe last

## Context
User directive: start with what we will be testing (plus its tests) and finish with
our probe. The orchestrator's correctness claims are only as good as the thing it is
measured against.

## Options considered
- **Probe first against a stub**: gets to the interesting AIMD work sooner, but the
  stub's behavior would silently become the spec.
- **Service first, fully tested, then the probe**: the limiter's exact semantics
  (window edges, Retry-After values) are pinned by tests before any client code
  depends on them.

## Decision
Implement and test the inference service first (rate limiter → app → backends →
Dockerfile → OpenAPI export), then build the orchestrator against the real, running
service (mock backend for speed, Ollama for the final runs).

## Consequences / tradeoffs
- The AIMD controller — the highest-weighted deliverable — is built last; schedule
  risk is managed by keeping the service scope deliberately thin.
- Orchestrator integration tests get a real server to run against from day one.
