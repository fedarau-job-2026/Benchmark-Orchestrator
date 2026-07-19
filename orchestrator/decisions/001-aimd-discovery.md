# 001 — AIMD controller for rate-limit discovery

## Context
The assignment's heaviest-weighted criterion: the orchestrator must find the service's
RPM and concurrency limits on its own, saturate without melting, and react when
reviewers halve or double the limits.

## Options considered
- **Explicit probe phase, then steady state**: binary-search the ceiling at startup,
  run at ~90%. Legible, but brittle if limits change mid-run — the assignment says
  they will change them.
- **Probe + AIMD hybrid**: fastest convergence, most moving parts.
- **Pure AIMD (TCP-congestion-control style)**: no configured limits at all;
  continuous adaptation is the steady state, so a mid-run limit change is just
  another day.

## Decision
Pure AIMD, sharpened with direct evidence where the protocol provides it:
- **Concurrency window**: on a concurrency-flavored 429 the server just told us its
  cap ≈ the number of requests we had in flight — adopt that (evidence-based
  decrease, floor 1). Additive +1 probe after N clean completions, but **only if the
  window was actually the binding constraint** (TCP-style: don't grow a cwnd you
  never filled — otherwise the window inflates meaninglessly while the rate binds).
- **RPM pacer**: token bucket with a *learned* refill rate. **Slow start**: rate
  doubles per interval until the first 429, then gentle multiplicative creep
  (×1.15/2s). On an rpm-flavored 429, the count of our accepted requests in the
  trailing 60s ≈ the server's RPM (we are its only client) — adopt it with a 2%
  safety margin and pause for `Retry-After`. The creep rediscovers raised limits;
  halved limits are respected within one Retry-After.
- The service's `X-RateLimit-Reason` header picks which knob reacts; when absent,
  both shrink conservatively.

## Consequences / tradeoffs
- Slow start converges in seconds without a separate probe phase; discovery is just
  the controller's natural behavior.
- Evidence-based decreases converge in one observation instead of oscillating like
  blind ×0.5 AIMD.
- The "only client" assumption behind RPM learning is stated, not hidden: with
  competing clients the estimate degrades into a safe underestimate.
- Never hard-codes or reads the service's limits, structurally satisfying the
  "no hard-coded assumptions" requirement.
