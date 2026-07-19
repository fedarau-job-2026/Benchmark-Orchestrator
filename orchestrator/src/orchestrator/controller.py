"""Adaptive dispatch controller — discovers the endpoint's limits by probing.

AIMD (TCP-congestion-control style) sharpened with direct evidence
(see decisions/001-aimd-discovery.md):

- Concurrency window: when a concurrency-flavored 429 arrives while k requests are
  in flight, the server's cap is ≈ k, so we set the window to k (evidence-based
  decrease); after N clean completions we probe upward by +1 (additive increase).
- Request rate: a token bucket whose refill rate is learned. When an rpm-flavored
  429 arrives, the number of *accepted* requests in the trailing 60s is direct
  evidence of the server's RPM, so we adopt it (with a safety margin) and pause for
  Retry-After. During sustained clean periods the rate creeps up multiplicatively,
  so a raised server limit gets rediscovered and a lowered one gets respected.
- Without the advisory X-RateLimit-Reason header we shrink both knobs conservatively.

No configured limits anywhere: the controller starts polite and converges within
seconds; a mid-run limit change is just another day at the office.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass

# Tuning constants (behavior shape, not endpoint assumptions)
INITIAL_CONCURRENCY = 2.0
INITIAL_RATE = 1.0            # req/s — polite start, ramps quickly
CLEAN_STREAK_FOR_INCREASE = 5  # completions between +1 concurrency probes
RATE_GROWTH_SLOW_START = 2.0  # doubling per interval until the first 429 (TCP slow start)
RATE_GROWTH = 1.15            # gentle growth after the first 429
RATE_GROWTH_INTERVAL_S = 2.0
RATE_SAFETY = 0.98            # run just under the learned RPM
MAX_RATE = 100.0
BUCKET_BURST = 2.0            # small burst capacity keeps arrivals smooth


@dataclass
class ControllerStats:
    concurrency_limit: float = INITIAL_CONCURRENCY
    rate: float = INITIAL_RATE           # current pacer rate, req/s (creeps between 429s)
    last_learned_rpm: int | None = None  # RPM evidence captured at the last rpm-429
    in_flight: int = 0
    paused_until: float = 0.0
    total_429_rpm: int = 0
    total_429_concurrency: int = 0
    total_429_unknown: int = 0


class AdaptiveController:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._concurrency = INITIAL_CONCURRENCY
        self._rate = INITIAL_RATE
        self._in_flight = 0
        self._clean_streak = 0
        self._window_pressed = False   # did in-flight actually reach the window?
        self._slow_start = True        # aggressive rate growth until the first 429
        self._tokens = 1.0
        self._last_refill = clock()
        self._last_growth = clock()
        self._paused_until = 0.0
        self._last_learned_rpm: int | None = None  # RPM observed at the last rpm-429
        self._accepted_times: deque[float] = deque()  # our requests the server accepted
        self._stats_429 = {"rpm": 0, "concurrency": 0, "unknown": 0}
        self._waiters: deque[asyncio.Future] = deque()  # FIFO admission queue
        self._pump_handle: asyncio.TimerHandle | None = None

    # --- dispatch gate ---

    async def acquire(self) -> None:
        """Block until a request may be dispatched (token available, slot free,
        not in a Retry-After pause). Admission is strictly FIFO so retried and
        late-queued tasks can't be starved by racing workers.
        Caller must call a feedback method after."""
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        self._pump()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # Admitted between cancellation and wakeup — hand the slot back.
                self._release()
            raise

    def _pump(self) -> None:
        """Admit waiters in FIFO order while dispatch is allowed; otherwise
        schedule a re-pump for when the earliest constraint might clear."""
        now = self._clock()
        self._refill(now)
        while self._waiters:
            if self._waiters[0].cancelled():
                self._waiters.popleft()
                continue
            wait = self._blocked_for(now)
            if wait > 0.0:
                self._schedule_pump(wait)
                return
            fut = self._waiters.popleft()
            self._tokens -= 1.0
            self._in_flight += 1
            if self._in_flight >= int(self._concurrency):
                self._window_pressed = True
            fut.set_result(None)

    def _schedule_pump(self, delay: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # sync context (unit tests): no scheduling needed
        if self._pump_handle is not None:
            self._pump_handle.cancel()
        self._pump_handle = loop.call_later(delay, self._pump)

    def _blocked_for(self, now: float) -> float:
        """0.0 if dispatch is allowed now, else seconds until it might be."""
        waits = []
        if now < self._paused_until:
            waits.append(self._paused_until - now)
        if self._in_flight >= int(self._concurrency):
            waits.append(0.05)  # re-check when a completion wakes us
        if self._tokens < 1.0:
            waits.append((1.0 - self._tokens) / self._rate)
        return max(waits) if waits else 0.0

    def _refill(self, now: float) -> None:
        self._tokens = min(BUCKET_BURST, self._tokens + (now - self._last_refill) * self._rate)
        self._last_refill = now
        # Multiplicative rate growth while clean — slow start until the first 429,
        # then a gentle creep that rediscovers raised limits.
        if now - self._last_growth >= RATE_GROWTH_INTERVAL_S and now >= self._paused_until:
            growth = RATE_GROWTH_SLOW_START if self._slow_start else RATE_GROWTH
            self._rate = min(MAX_RATE, self._rate * growth)
            self._last_growth = now

    # --- feedback ---

    def on_accepted(self) -> None:
        """Server admitted the request (2xx or backend error — it consumed a slot)."""
        now = self._clock()
        self._accepted_times.append(now)
        while self._accepted_times and now - self._accepted_times[0] >= 60.0:
            self._accepted_times.popleft()

    def on_success(self) -> None:
        if self._in_flight >= int(self._concurrency):
            self._window_pressed = True
        self._release()
        self._clean_streak += 1
        if self._clean_streak >= CLEAN_STREAK_FOR_INCREASE:
            self._clean_streak = 0
            # Additive probe upward — but only if the window was actually the
            # binding constraint (TCP-style: don't grow cwnd you never filled).
            if self._window_pressed:
                self._concurrency += 1.0
                self._window_pressed = False

    def on_rate_limited(self, reason: str | None, retry_after: float) -> None:
        now = self._clock()
        in_flight_others = self._in_flight - 1  # excluding the rejected request
        self._release()
        self._clean_streak = 0
        self._slow_start = False  # first contact with a limit ends slow start
        self._window_pressed = False
        self._stats_429[reason if reason in ("rpm", "concurrency") else "unknown"] += 1

        if reason == "concurrency":
            # Server rejected us while `in_flight_others` were running → cap ≈ that.
            self._concurrency = max(1.0, float(in_flight_others))
        elif reason == "rpm":
            self._learn_rate_from_window(now)
            self._pause(now, retry_after)
        else:
            # No hint: shrink both, politely.
            self._concurrency = max(1.0, min(self._concurrency * 0.5, float(in_flight_others or 1)))
            self._rate = max(0.1, self._rate * 0.7)
            self._pause(now, retry_after)
        self._last_growth = now  # don't grow immediately after a 429

    def on_error(self) -> None:
        """Accepted-but-failed (5xx/timeout): frees a slot, resets the clean streak."""
        self._release()
        self._clean_streak = 0

    # --- internals ---

    def _learn_rate_from_window(self, now: float) -> None:
        """Accepted requests in the trailing 60s ≈ the server's RPM (we are its only
        client during a benchmark run) — adopt it with a safety margin."""
        while self._accepted_times and now - self._accepted_times[0] >= 60.0:
            self._accepted_times.popleft()
        observed_rpm = len(self._accepted_times)
        if observed_rpm > 0:
            self._rate = max(0.1, (observed_rpm / 60.0) * RATE_SAFETY)
            self._last_learned_rpm = observed_rpm
        else:
            self._rate = max(0.1, self._rate * 0.5)

    def _pause(self, now: float, retry_after: float) -> None:
        self._paused_until = max(self._paused_until, now + max(retry_after, 0.5))

    def _release(self) -> None:
        self._in_flight -= 1
        if self._waiters:
            self._pump()  # a slot just freed — admit the FIFO head immediately

    # --- introspection ---

    def stats(self) -> ControllerStats:
        return ControllerStats(
            concurrency_limit=self._concurrency,
            rate=self._rate,
            last_learned_rpm=self._last_learned_rpm,
            in_flight=self._in_flight,
            paused_until=self._paused_until,
            total_429_rpm=self._stats_429["rpm"],
            total_429_concurrency=self._stats_429["concurrency"],
            total_429_unknown=self._stats_429["unknown"],
        )
