# Benchmark Orchestrator

FDE take-home (brief: [`docs/ASSIGNMENT.md`](docs/ASSIGNMENT.md)): a rate-limited
inference service and an orchestrator that **discovers the service's limits on its
own** and drives a 1,000-query job queue through it as fast as those limits allow.

**TL;DR for the reviewer:**

```bash
docker compose up -d inference          # service (starts ollama, pulls qwen2.5:0.5b first time)
docker compose run --rm orchestrator    # runs data/queue.jsonl, live TUI, report in ./results/
```

Change the limits — the orchestrator is never told, it re-discovers them:

```bash
RPM=30 MAX_CONCURRENCY=2 docker compose up -d inference
docker compose run --rm orchestrator
```

No model / faster review loop: `docker compose up -d inference-mock` (same limiter,
simulated ~400ms latency) — then run the orchestrator the same way.
You can even swap `inference` ↔ `inference-mock` with different limits **while the
orchestrator is mid-run**; it re-converges and loses nothing (measured below).

No Docker: see [Native quickstart](#native-quickstart-no-docker).

---

## Where to look, by evaluation criterion

### 1. Concurrency & backoff (the interesting part)

One file: [`orchestrator/src/orchestrator/controller.py`](orchestrator/src/orchestrator/controller.py) (~200 lines).

No configured limits anywhere — TCP-style congestion control sharpened with direct
evidence from the protocol:

| Signal | Reaction |
|---|---|
| start of run | slow start: dispatch rate doubles every 2s until first contact with a limit |
| `429` + `X-RateLimit-Reason: rpm` | our accepted-count in the trailing 60s **≈ the server's RPM** (we're its only client) → adopt it ×0.98, pause exactly `Retry-After` — one observation, no oscillating guesswork |
| `429` + `X-RateLimit-Reason: concurrency` | the server rejected us with k requests in flight → its cap ≈ k → window snaps to k |
| N clean completions **and** the window was actually full | window +1 (probe; never grows a window we never filled) |
| sustained clean period | rate creeps ×1.15/2s — rediscovers a raised limit |
| `429`, no reason header | the header is a hint, not a crutch: both knobs shrink conservatively |

Retries: 429 is *traffic* (generous budget, one **global** pause shared by all
workers — no thundering herd); 5xx/timeouts get exponential backoff ×3, then the
query is recorded as failed and the run continues. Admission is strictly FIFO
(`acquire()`/`_pump()`), so retried or late-queued tasks can't be starved.

**Measured — RPM halved then doubled mid-run** (600 queries, mock backend;
service restarted under the orchestrator twice):

```
08:19:12  11/600  window 3  rate: probing            ← slow start
08:19:28 119/600  window 5  learned ~115 rpm         ← true limit: 120 (22s to converge)
08:20:38  ── service restarted with RPM=60 ──
08:21:08 240/600  paused, re-learning               ← Retry-After honored, no melt
08:21:59  ── service restarted with RPM=240 ──
08:22:44 524/600  7.12 req/s                         ← headroom found in ~15s
done in 258.0s | 600 completed, 0 failed | 429s 42
```

**Measured — saturation at the limit** (the full 1,000-query `data/queue.jsonl`,
containerized orchestrator, RPM=300 = 5/s, concurrency 8):
`1000 completed, 0 failed, 5.04 req/s sustained, learned_rpm: 299` — pinned to the
ceiling. Full report: [`docs/sample-results/`](docs/sample-results/).

**Measured — real Ollama** (200 queries, defaults RPM=60/conc=4): 200/200, 0 failed,
70.5% accuracy, 37 429s absorbed, window locked on 4. On CPU-only hardware the model
(p50 ≈ 6.8s) is slower than 60 RPM, so the *concurrency* cap binds — the report then
says `"rpm_limit_observed": false` instead of inventing a number.

### 2. System design

Two **fully independent deployments** — separate projects, separate lockfiles,
separate Dockerfiles, separate test suites, **zero shared Python code**:

```
inference-service/   the system under test — FastAPI + hand-built limiter + ollama/mock backends
orchestrator/        the probe — controller, runner, jobs, metrics, TUI, report
docs/openapi.json    the ONLY thing the two sides share (Swagger UI live at :8080/docs)
docker-compose.yml   convenience wiring; each project builds and runs standalone
decisions/           ADRs — the reasoning trail (also in each subproject)
```

The orchestrator physically cannot peek at the service's limits — there is nothing
to import. A service-side contract test pins the live `/openapi.json` against the
committed spec, so API drift fails CI.

New job type = one class + one registry entry
([`orchestrator/src/orchestrator/jobs.py`](orchestrator/src/orchestrator/jobs.py)):
a handler expands a queue line into query tasks and grades responses. The queue
format, dispatch loop, controller, metrics, and report don't change.

Rate limiter ([`inference-service/src/inference_service/rate_limiter.py`](inference-service/src/inference_service/rate_limiter.py)) is
hand-built per the ground rules: sliding 60s window over accepted timestamps +
in-flight counter; check-and-reject before the backend is touched; 429 carries a
*real* `Retry-After` (seconds until the oldest request ages out).

### 3. Observability

Run the orchestrator in a real terminal for the Rich live TUI: overall + per-benchmark
progress with ETA, live req/s (30s sliding), p50/p95, in-flight vs current window,
what the controller currently believes (learned rpm, pacer rate, pause countdown),
429/failure counters, recent-failures tail. Not a TTY (CI, piped) → automatic
fallback to 5s heartbeat log lines (the traces quoted above are that fallback).

### 4. Product thinking

Defaults are the assignment's defaults; every knob is env-driven (table below);
`--help` is honest; the orchestrator refuses to start with a clear message if the
service isn't reachable; a run **always** ends with a report — failures are counted,
not fatal. The `inference-mock` profile exists specifically so a reviewer can see the
whole system behave in under a minute.

### 5. Pragmatism / where the 10 hours went

Roughly: service + tests ~2h, orchestrator core + controller + tests ~4h, TUI +
report ~1h, Docker/compose + live validation runs (incl. mid-run limit flips) ~2h,
docs/ADRs ~1h. Corners deliberately cut: no persistence/resume, no per-query
streaming output, accuracy grading is the mandated substring match, mock-mode
accuracy is meaningless (canned responses). "What I'd build next" below.

---

## The decision trail (for the thorough reviewer)

Every significant call is an ADR — **Context → Options → Decision → Consequences** —
written so the reasoning can be reconstructed without the git history:

- [`decisions/`](decisions/) — cross-cutting: two-deployment split, OpenAPI-as-contract, Docker strategy, build order (system-under-test first, probe last)
- [`inference-service/decisions/`](inference-service/decisions/) — limiter semantics (incl. "rejected requests don't consume window slots"), mock backend
- [`orchestrator/decisions/`](orchestrator/decisions/) — AIMD design, retry policy, contract-fake testing, FIFO admission (born from a live starvation finding), TUI

Two ADRs were written mid-build in response to measured behavior
([`005-fifo-admission`](orchestrator/decisions/005-fifo-admission.md), the
`rpm_limit_observed` honesty fix) — the trail shows the system being engineered,
not just assembled.

## Results output

`results/results_<runid>.json` — everything the brief mandates plus what makes the
run explainable:

```jsonc
{
  "total_wall_time_s": 198.541,
  "per_benchmark": [ {"benchmark_id": "run_001", "wall_time_s": 15.834,
                      "queries": 100, "completed": 100, "failed": 0, "accuracy": 0.07}, ... ],
  "request_latency_p50_s": 0.305,
  "request_latency_p95_s": 0.487,
  "throughput_rps": 5.037,
  "total_http_requests": 1060,        // every attempt, incl. 429s
  "failure_count": 0,
  "overall_accuracy": 0.07,           // mock backend — see note; 0.705 on real ollama
  "http_429_count": 60,
  "discovered_limits": { "concurrency_window": 9.0, "rpm_limit_observed": true,
                          "learned_rpm": 299, "429s_rpm": 16, "429s_concurrency": 44 }
}
```

plus `results_<runid>_queries.csv` — per-query response, latency, attempts, error.
A complete sample from the 1,000-query run above is committed in
[`docs/sample-results/`](docs/sample-results/).

## Configuration

| Component | Variable | Default | Meaning |
|---|---|---|---|
| service | `RPM` | `60` | max accepted requests per trailing 60s |
| service | `MAX_CONCURRENCY` | `4` | max in-flight requests |
| service | `BACKEND` | `ollama` | `ollama` or `mock` |
| service | `OLLAMA_URL` | `http://localhost:11434` | compose: `http://ollama:11434`; host install: `http://host.docker.internal:11434` |
| service | `MODEL` | `qwen2.5:0.5b` | Ollama model |
| service | `MOCK_LATENCY_MS` / `MOCK_JITTER_MS` | `400` / `200` | mock latency shape |
| service | `PORT` | `8080` | listen port |
| orchestrator | `INFERENCE_URL` | `http://localhost:8080` | service base URL (also `--url`) |

CLI: `orchestrator <queue.jsonl> [--out DIR] [--url URL] [--no-tui]`.
Point at your own queue file:
`docker compose run --rm -v /path/to/dir:/work orchestrator /work/queue.jsonl --out /results`
(a queue line's `csv_path` resolves relative to the queue file).

## Native quickstart (no Docker)

Requires [uv](https://docs.astral.sh/uv/); for real inference, [Ollama](https://ollama.com) + `ollama pull qwen2.5:0.5b`.

```bash
# terminal 1
cd inference-service && BACKEND=mock uv run inference-service
# terminal 2
cd orchestrator && uv run orchestrator ../data/queue.jsonl
```

## Testing

```bash
cd inference-service && uv run pytest   # 15 tests: limiter edges, HTTP surface, contract pin
cd orchestrator && uv run pytest        # 26 tests: controller, jobs/report, HTTP integration
                                        # (~90s: one test honestly waits out a 60s RPM window)
```

Orchestrator integration tests run against a *contract fake* implementing
`docs/openapi.json` — never the sibling project (the fake also records ground truth
like max observed in-flight, so tests can assert "the server's cap was never
breached"). Cross-deployment verification happens at the compose level.

## Ambiguity calls (flagged per the brief)

- **Rejected requests don't consume RPM window slots** — otherwise a hammering
  client locks itself out forever ([ADR](inference-service/decisions/002-rate-limiter-semantics.md)).
- **`X-RateLimit-Reason` header** added to the 429 (mirrors real-world `x-ratelimit-*`
  practice). The orchestrator treats it as an optional hint and works without it.
- **`Retry-After` on concurrency breaches is `1`** — the true wait is unknowable
  (depends on model latency); clients must adapt, ours does.
- **Mock-mode accuracy ≈ 0%** by design (canned responses); accuracy numbers come
  from real-model runs.
- **Effective throughput can slightly exceed RPM/60** — sliding-window drains after
  a pause legitimately admit a catch-up burst; the limiter never admits more than
  RPM per trailing 60s.

## What I'd build next

- Crash-safe resume: persist per-query results incrementally, skip completed work on restart.
- JSONL event stream for post-run throughput-curve plotting (the data behind the traces above).
- Benchmark-aware scheduling: minimize p95 *benchmark* completion, not just query throughput.
- Multi-endpoint fan-out: N services behind one discovery controller.

## AI tooling note

Built with Claude Code doing the heavy lifting (scaffolding, tests, ADR drafting)
under close direction: the architecture (two non-crossed deployments, contract-first
Swagger, system-under-test-first build order, evidence-sharpened AIMD, the ADR
trail) was specified and reviewed by hand, and every component was validated against
a live stack — real Ollama in Docker, including mid-run limit changes — during
development.
