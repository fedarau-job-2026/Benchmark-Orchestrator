# 002 — Two fully separate, non-crossed deployments

## Context
The assignment mandates two components in separate processes over HTTP. We want to
simulate real life: in production, the team probing an endpoint does not share code
with the team serving it.

## Options considered
- **Monorepo single package, two entry points**: least boilerplate, but shared config/
  models inevitably leak between client and server, weakening the "no hard-coded
  assumptions" guarantee.
- **Two independent projects, HTTP-only contract**: each has its own pyproject,
  lockfile, Dockerfile, and tests; the orchestrator can only rely on what the API
  contract promises.

## Decision
Two independent projects: `inference-service/` and `orchestrator/`. Zero shared Python
code. The exported OpenAPI spec (`docs/openapi.json`) is the only interface.
Root `docker-compose.yml` is convenience wiring only — each project builds and runs
standalone.

## Consequences / tradeoffs
- Some duplication (both declare httpx, pytest, etc.) — accepted as the cost of real
  isolation.
- Structurally enforces the assignment's core constraint: the orchestrator cannot peek
  at the service's limits, because there is nothing to import.
- Orchestrator integration tests must treat the service as a black box (subprocess +
  HTTP), which doubles as an honest end-to-end test.
