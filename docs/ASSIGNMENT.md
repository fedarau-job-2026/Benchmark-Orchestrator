# 💻 FDE Take-Home: Benchmark Orchestrator

**Role:** Forward Deployed Engineer — H Company, Paris
**Wall-clock deadline:** 1 week from receipt
**AI tools:** Fully encouraged. Please include a short note on where you leaned on them.

---

## Context

At H we run a lot of evaluations against models. A recurring engineering problem is this: you have an endpoint with hard capacity limits, and you need to push a large queue of benchmark jobs through it as quickly, reliably, and observably as possible.

This take-home asks you to build that system end-to-end. It's intentionally open-ended: we want to see how you scope, what you prioritize, and how far you push.

## What you're building

Two components that run as **separate processes**:

**1. A rate-limited inference service.** A thin HTTP wrapper around a local Ollama model (`qwen2.5:0.5b` or similar) that enforces the constraints below and behaves like a production inference endpoint would under load.

**2. A benchmark orchestrator.** A system that consumes a queue of benchmark jobs, dispatches their queries against the inference service, collects responses, and produces a results report. This is where the interesting engineering lives. **This service should find the rate limits on its own.**

Both components should launch with a single command each.

## The workload

You are given a `benchmark.csv` containing 100 simple question-answer pairs:

```
id,question,expected_answer
1,What is the capital of France?,Paris
2,What is 12 times 8?,96
3,Who wrote Hamlet?,Shakespeare
...
```

The job queue is a JSONL file where each line describes one benchmark run — in the simplest case, the same CSV referenced 10 times. Your orchestrator must process the entire queue (1,000 queries total) and produce a results file.

```json
{"benchmark_id": "run_001", "csv_path": "benchmark.csv"}
{"benchmark_id": "run_002", "csv_path": "benchmark.csv"}
...
```

Answer grading can be a simple case-insensitive substring match against `expected_answer`. Nothing fancier is required — we are not evaluating your NLP pipeline.

## Constraints on the inference service

Your inference service must enforce, and return proper HTTP errors for:

- **Max requests per minute (RPM):** configurable, default 60
- **Max concurrent in-flight requests:** configurable, default 4
- **On breach:** HTTP 429 with a `Retry-After` header. No silent dropping, no queueing inside the service.

These are the only artificial constraints. Real latency comes from the model itself.

We will change the RPM and concurrency values when reviewing your submission, so **the orchestrator must not hard-code assumptions about them**.

## Deliverables

A repository containing:

1. **Source code:** Python or Node.js, your choice. Pick what you ship fastest in.
2. **README.md** with:
   - One command to start the inference service
   - One command to run the benchmark orchestrator against a queue file
   - Any setup steps (model pull, dependencies)
   - A short design note: what you built, what you'd build next, what tradeoffs you made
3. **Results output:** a structured file (JSON or CSV) written at the end of a run containing at minimum: total wall time, per-benchmark wall time, p50/p95 request latency, throughput (req/s), total requests, failure count, and overall accuracy.
4. **The `benchmark.csv` and a sample `queue.jsonl`** we can point the runner at.

We should be able to clone the repo, run two commands, and see your system work.

## What we're evaluating

In rough order of weight:

- **Concurrency & backoff.** Does your orchestrator saturate the endpoint without melting it? How does it react when we halve the RPM? When we double it?
- **System design.** Is the queue abstraction sensible? Can a new job type be added without rewriting the world? Where are the boundaries between components?
- **Observability.** Can we tell what the system is doing while it's running? Progress, ETA, live throughput, failures — surface whatever you'd want if a customer were watching over your shoulder.
- **Product thinking.** Is the CLI pleasant? Are the defaults right? Would we actually use this tomorrow, or would we rewrite it?
- **Pragmatism.** Did you ship something that works end-to-end, or did you over-engineer one corner and leave the rest broken? Ten hours isn't a lot — we're looking for good judgment about where to spend them.

Code quality matters, but we weigh "does this work well under load" above "is every function perfectly typed."

## Ground rules

- Use any libraries you want. Use AI tooling freely.
- Don't use Ollama's built-in concurrency settings as a substitute for your own rate limiter — the rate-limiting logic is something we want to see you build.
- If you blow past 10 hours, stop and write up what you'd do with more time. We'd rather see a crisp partial solution than a sprawling exhausted one.
- If something in this brief is ambiguous, make a reasonable call and note it in your README.

## Submitting

We like to have some time before the restitution to take a look at your code and have a first look. Please submit your code before the restitution date (ideally 24 hours before).

- If using GitHub, please add [@fred3105](https://github.com/fred3105) & [@axel-cole](https://github.com/axel-cole) to your repo (keep it private)
- You can also send a zip of your code to frederic.legrand@hcompany.ai & axel.nguyen@hcompany.ai

Don't worry if it's not completely done — it's more of a preview than a final result.

## FAQ

**Can I use a different model?** Yes, as long as it runs via Ollama and is small enough that the rate limit actually binds (i.e., the model is not the bottleneck). `qwen2.5:0.5b` is the safe default.

**Can the inference service and orchestrator run in the same process?** No. They must be separate processes communicating over HTTP. We will run them independently.

**Can I persist state to disk?** Yes, if it helps. Not required.

**Do I need a UI?** Not required. A good terminal-based live view is often more impressive than a half-built web dashboard. Your call.

**How will you test performance?** We will run your system against our own queue file with varying RPM and concurrency settings, and compare wall-clock time, throughput curves, and failure handling across candidates.

---

Good luck. Have fun with it.
