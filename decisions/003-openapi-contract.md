# 003 — OpenAPI (Swagger) spec as the shared contract

## Context
With zero shared code (ADR 002), the two deployments still need an agreed interface,
and reviewers need an easy way to inspect the API.

## Options considered
- **Hand-written API.md**: simple, but prose drifts from code silently.
- **Shared Pydantic models package**: convenient, but violates the no-shared-code rule.
- **OpenAPI spec generated from the service**: FastAPI already produces it; Swagger UI
  comes free at `/docs`; a contract test can pin it.

## Decision
The inference service declares its full surface (schemas, 429 responses, `Retry-After`
and `X-RateLimit-Reason` headers) via Pydantic models + `responses=` annotations. The
spec is exported to `docs/openapi.json` — that committed artifact IS the contract the
orchestrator codes against. A service-side contract test asserts the live
`/openapi.json` matches the committed file, so drift fails CI.

## Consequences / tradeoffs
- Contract changes are deliberate: regenerate the export, review the diff.
- The orchestrator reads the spec with human eyes (no codegen) — pragmatic for a
  two-endpoint API; codegen would be over-engineering here.
