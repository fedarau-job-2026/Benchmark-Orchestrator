# 001 — Python across both deployments

## Context
The assignment allows Python or Node.js. The machine has Node 24 preinstalled but only
system Python 3.9; the audience is an AI-evals team.

## Options considered
- **Node.js**: zero install cost, fast first run; weaker fit for an ML-eval audience.
- **Python 3.12 via uv**: uv installs a pinned interpreter without touching system
  Python; FastAPI/httpx/rich are the natural tools for this problem space.
- **System Python 3.9**: no installs, but blocks modern typing/asyncio ergonomics.

## Decision
Python 3.12 managed by uv in both subprojects. FastAPI/uvicorn for the service;
asyncio + httpx + rich for the orchestrator.

## Consequences / tradeoffs
- One-time `uv` dependency for native runs (Docker images are self-contained anyway).
- Matches reviewer expectations at an AI company; best library fit for eval tooling.
