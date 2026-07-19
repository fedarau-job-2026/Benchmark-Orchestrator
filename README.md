# Benchmark Orchestrator

FDE take-home for H Company — a rate-limited inference service and a benchmark
orchestrator that **discovers the service's limits on its own** and pushes a job
queue through it as fast as the limits allow.

Two independent deployments (own project, own Dockerfile, own tests, zero shared
code) that talk only over HTTP — the contract is [`docs/openapi.json`](docs/openapi.json),
browsable as Swagger UI at `http://localhost:8080/docs` while the service runs.

## Quickstart (Docker)

```bash
# 1. Start the inference service (pulls qwen2.5:0.5b into an ollama container on first run)
docker compose up -d inference

# 2. Run the queue through it
docker compose run --rm orchestrator
```

Results land in `./results/`. To watch the live TUI, run step 2 in a real terminal.

No model / faster demo? Use the mock backend (simulated latency, same limiter):

```bash
docker compose up -d inference-mock
docker compose run --rm orchestrator
```

Reviewers change the limits via env — the orchestrator is never told:

```bash
RPM=30 MAX_CONCURRENCY=2 docker compose up -d inference   # or inference-mock
docker compose run --rm orchestrator
```

Point the runner at your own queue file by overriding the command:

```bash
docker compose run --rm -v /path/to/yours:/work orchestrator /work/queue.jsonl --out /results
```

## Quickstart (native, no Docker)

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv`) and, for real
inference, [Ollama](https://ollama.com) with `ollama pull qwen2.5:0.5b`.

```bash
# Terminal 1 — the service (env: RPM, MAX_CONCURRENCY, BACKEND=ollama|mock, OLLAMA_URL, MODEL, PORT)
cd inference-service && BACKEND=mock uv run inference-service

# Terminal 2 — the orchestrator
cd orchestrator && uv run orchestrator ../data/queue.jsonl
```

## Configuration

| Component | Variable | Default | Meaning |
|---|---|---|---|
| service | `RPM` | `60` | max accepted requests per trailing 60s |
| service | `MAX_CONCURRENCY` | `4` | max in-flight requests |
| service | `BACKEND` | `ollama` | `ollama` or `mock` |
| service | `OLLAMA_URL` | `http://localhost:11434` | where Ollama lives (compose: `http://ollama:11434`; host install: `http://host.docker.internal:11434`) |
| service | `MODEL` | `qwen2.5:0.5b` | Ollama model name |
| service | `MOCK_LATENCY_MS` / `MOCK_JITTER_MS` | `400` / `200` | mock backend latency shape |
| service | `PORT` | `8080` | listen port |
| orchestrator | `INFERENCE_URL` | `http://localhost:8080` | service base URL (also `--url`) |

Orchestrator CLI: `orchestrator <queue.jsonl> [--out DIR] [--url URL] [--no-tui]`.

## What's in the box

```
inference-service/    deployment #1 — the system under test
orchestrator/         deployment #2 — the probe
docs/openapi.json     the HTTP contract (the only thing the two sides share)
decisions/            ADRs: why things are the way they are (also per subproject)
data/                 benchmark.csv (100 QA pairs) + queue.jsonl (10 runs = 1,000 queries)
```

### The inference service

A FastAPI wrapper around Ollama with a **hand-built** limiter:

- RPM: sliding 60s window over *accepted* request timestamps (rejected requests
  don't consume slots — a hammering client can't lock itself out).
- Concurrency: in-flight counter, checked before the backend is ever touched.
- On breach: immediate `429` + `Retry-After` (real seconds for RPM, `1` for
  concurrency) + advisory `X-RateLimit-Reason: rpm|concurrency`. No queueing,
  no silent drops.

### The orchestrator

- **Limit discovery — pure AIMD, no configured limits** (the interesting part,
  `orchestrator/src/orchestrator/controller.py`):
  - *Rate*: a token bucket whose refill rate is learned. Slow start (doubling)
    until the first 429; on an rpm-429 the count of accepted requests in the
    trailing 60s ≈ the server's RPM — adopt it minus a safety margin, pause for
    `Retry-After`. Gentle growth afterwards rediscovers raised limits: halve the
    RPM mid-run and it re-converges after one `Retry-After`; double it and the
    creep finds the headroom.
  - *Concurrency*: a window that grows +1 only when it was actually the binding
    constraint (TCP-style), and on a concurrency-429 snaps to the observed
    in-flight count — the server just told us its cap.
  - The reason header is a hint, not a crutch: without it, both knobs shrink
    conservatively.
- **Retry semantics**: 429 is traffic (wait `Retry-After` + jitter, generous retry
  budget, one *global* pause shared by all workers); 5xx/timeouts get exponential
  backoff ×3, then the query is recorded as failed. The run always ends with a report.
- **Jobs**: queue JSONL → job-type registry (`benchmark_csv` today); a new job type
  is one class + one registry entry.
- **Observability**: Rich live TUI (progress + ETA per benchmark, live req/s,
  p50/p95, in-flight vs window, learned rate, 429/failure counters); falls back to
  periodic log lines when not a TTY (`--no-tui`).

### Results output

`results/results_<runid>.json` — total & per-benchmark wall time, p50/p95 request
latency, throughput, total HTTP requests, failure count, overall accuracy, plus the
controller's discovered limits and 429 breakdown. `results_<runid>_queries.csv` has
per-query detail (response, latency, attempts, error).

## Testing

Each project is self-contained:

```bash
cd inference-service && uv run pytest   # limiter semantics, HTTP surface, contract pin
cd orchestrator && uv run pytest        # controller AIMD, jobs/grading/report, integration
                                        # (~90s: one test honestly waits out a 60s RPM window)
```

The orchestrator's integration tests run against a *contract fake* implementing
`docs/openapi.json` — never the sibling project (see
`orchestrator/decisions/004-contract-fake-for-tests.md`). Cross-deployment
verification happens at the compose level.

## Measured behavior

From a live mock run (600 queries) where the service's RPM was **halved mid-run
(120→60) and later doubled (60→240)** while the orchestrator kept going:

```
08:19:12  11/600  window 3   rate: probing (no rpm 429 yet)     ← slow start
08:19:28 119/600  window 5   rate 1.91/s (~115 rpm)             ← learned ≈ the real 120
08:20:38  --- server restarted with RPM=60 ---
08:21:08 240/600  window 5   rate 2.24/s (~134)                 ← pause honored, re-learning
08:21:59  --- server restarted with RPM=240 ---
08:22:44 524/600  7.12 req/s                                    ← headroom found in ~15s
done in 258.0s | 600 completed, 0 failed | 429s 42
```

Throughput follows a sawtooth around the true limit: learn on 429 → run just under
it → creep up → get told no → re-learn. Net effective throughput lands at (or
slightly above) the configured RPM, because the orchestrator legitimately exploits
sliding-window drains. Zero queries were lost across both limit changes, including
the service restarts themselves.

On real Ollama (200 queries, RPM=60/concurrency=4): 200/200 completed, 0 failed,
70.5% accuracy, 37 concurrency-429s absorbed, window locked onto the true cap of 4.
On CPU-only hardware the 0.5b model is slower than 60 RPM (p50 ≈ 6.8s), so the
concurrency cap binds instead of the RPM — the report then says
`"rpm_limit_observed": false` rather than inventing a number.

## Design notes & tradeoffs

- **Two non-crossed deployments** simulate the real relationship between a client
  team and an endpoint they don't own; the orchestrator physically cannot peek at
  the limits. (`decisions/002`)
- **Evidence-based AIMD** over a probe phase: reviewers will change limits — with
  continuous adaptation a mid-run change is just another day. (`orchestrator/decisions/001`)
- **Rejected requests don't consume RPM slots** — documented interpretation choice.
  (`inference-service/decisions/002`)
- **Accuracy in mock mode is 0%** by design (canned responses); use the ollama
  backend for real accuracy numbers.
- **Ambiguity calls** are written down as ADRs in `decisions/` folders — the
  thinking process is part of the deliverable.

### What I'd build next

- Persist per-query results incrementally (crash-safe resume of a half-finished queue).
- A latency-aware scheduler: prioritize benchmarks by ETA to minimize p95 of
  *benchmark* completion, not just query throughput.
- Structured event stream (JSONL) for post-run throughput curve plotting.
- Multi-endpoint fan-out: N inference services behind the same discovery logic.

### AI tooling note

Built with Claude Code doing the heavy lifting (scaffolding, tests, ADR drafting)
under close direction: the architecture (two non-crossed deployments, contract-first
Swagger, SUT-first build order, evidence-sharpened AIMD, decision-record trail) was
specified and reviewed by hand, and every component was verified against a live
stack (real Ollama in Docker) during development.
