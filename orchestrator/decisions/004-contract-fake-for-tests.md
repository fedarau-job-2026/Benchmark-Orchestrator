# 004 — Test against a contract fake, not the sibling service

## Context
The orchestrator needs integration tests over real HTTP. The repo rule (CLAUDE.md)
forbids importing the inference service, and even booting the sibling project as a
subprocess would couple the test suite to the sibling's checkout and toolchain —
breaking the "delete either directory, the other still works" guarantee.

## Options considered
- **Subprocess the real service in mock mode**: highest fidelity, but cross-project
  coupling in CI and in local dev.
- **In-process fake implementing `docs/openapi.json`**: an independent
  implementation of the contract (sliding-window RPM, concurrency cap, 429 +
  `Retry-After` + `X-RateLimit-Reason`), mutable limits for adaptation tests.

## Decision
`tests/fake_server.py`: a FastAPI test double written from the contract, served
in-process by uvicorn on a random port. This is exactly how you'd simulate a
third-party API you don't control. True cross-deployment verification happens at the
compose level (end-to-end runs), not in unit/integration tests.

## Consequences / tradeoffs
- Two implementations of the limiter semantics exist (service + fake); the contract
  test on the service side plus compose-level e2e runs keep them honest.
- The fake records ground truth (max observed in-flight, total 429s) that tests can
  assert against — something the real service deliberately doesn't expose.
