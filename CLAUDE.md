# Benchmark Orchestrator — Repo Rules

FDE take-home for H Company. Full brief: `docs/ASSIGNMENT.md`.

## Architecture rules (non-negotiable)

1. **Two separate, non-crossed deployments — simulate real life.**
   `inference-service/` (the system under test) and `orchestrator/` (our probe) are
   independent projects: own `pyproject.toml`, own lockfile, own Dockerfile, own tests.
   **Zero shared Python code.** The only interface between them is HTTP, described by
   the exported OpenAPI spec `docs/openapi.json` (served live as Swagger UI at `/docs`
   on the inference service). Treat the service like a third-party endpoint: the
   orchestrator may rely only on what the OpenAPI contract promises.
   If either directory were deleted, the other must still build, test, and run.

2. **Build & test order: system under test first, probe last.**
   Work on the inference service (plus its tests) lands before orchestrator work that
   depends on it. Tests live in the subproject whose code they cover. Orchestrator
   integration tests exercise the service **black-box over HTTP only** (e.g. boot it as
   a subprocess in mock mode) — never import it.

3. **Every significant decision becomes an ADR.**
   Record it in the owning subproject's `decisions/` folder; cross-cutting decisions go
   in root `decisions/`. Format: `NNN-short-title.md` with sections
   **Context → Options considered → Decision → Consequences/tradeoffs**.
   These files exist to show our thinking process to the reviewers (human or LLM) —
   write them so the reasoning can be reconstructed without reading git history.
   Append new ADRs *as decisions are made*, not retroactively at the end.

## Practical pointers

- Python 3.12 via `uv` in both subprojects (system Python is 3.9 — don't use it).
- Rate limiting in the service is hand-built (assignment forbids delegating it to Ollama).
- The orchestrator must **discover** RPM/concurrency limits itself (AIMD); never
  hard-code or read the service's limit config.
- Run natively: `uv run inference-service` / `uv run orchestrator <queue.jsonl>` from
  each subproject dir. Run via Docker: root `docker-compose.yml`
  (`docker compose up -d inference`, `docker compose run --rm orchestrator ...`).
- Service config is env-driven: `RPM`, `MAX_CONCURRENCY`, `BACKEND=ollama|mock`,
  `OLLAMA_URL`, `MODEL`, `PORT`. Orchestrator: `INFERENCE_URL`.
- Results land in `results/` (gitignored). Sample workload: `data/benchmark.csv`,
  `data/queue.jsonl`.
