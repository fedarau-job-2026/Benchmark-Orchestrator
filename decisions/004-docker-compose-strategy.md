# 004 — Docker-first delivery, native uv path kept working

## Context
User directive: deliver as separate Docker deployments working against each other.
Assignment: reviewers clone the repo and run two commands. Ollama is needed for real
inference; the review machine may or may not have it.

## Options considered
- **Docker only**: cleanest story, but no fallback if a reviewer's Docker misbehaves
  (this very machine was missing the compose plugin).
- **Native only**: fewest moving parts, but pushes Ollama/python setup onto reviewers.
- **Docker-first + native**: compose is the documented path; each project also runs
  via `uv run` with identical env-var config.

## Decision
Docker-first with a working native path. Compose services:
- `ollama` (`ollama/ollama` image, named model volume, pulls `qwen2.5:0.5b` on first
  start, healthcheck) — default profile; `OLLAMA_URL` can be pointed at a host install
  (`host.docker.internal:11434`) instead.
- `inference` — builds `inference-service/`, env pass-through for `RPM` /
  `MAX_CONCURRENCY` so reviewers change limits without touching code; `mock` profile
  runs it without Ollama.
- `orchestrator` — `docker compose run --rm orchestrator ...` with `tty: true` so the
  Rich TUI renders; mounts `./data` and `./results`.

## Consequences / tradeoffs
- Ollama in a container is CPU-only on macOS — acceptable: the model is 0.5b and the
  assignment wants the rate limit, not the model, to be the bottleneck.
- Two run paths to keep green; mitigated by both reading the same env vars.
