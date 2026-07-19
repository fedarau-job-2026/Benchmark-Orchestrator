"""Hand-built rate limiter: sliding-window RPM + in-flight concurrency cap.

Semantics (see decisions/002-rate-limiter-semantics.md):
- RPM is a sliding 60s window over *accepted* request timestamps; rejected requests
  do not consume slots.
- Concurrency is an in-flight counter; checked before RPM so a saturated service
  reports the more actionable reason.
- Decisions are made atomically from the event loop's perspective (no awaits inside),
  so no locking is needed under a single asyncio loop.
"""

import math
import time
from collections import deque
from dataclasses import dataclass

WINDOW_S = 60.0


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None       # "rpm" | "concurrency" when rejected
    retry_after: int | None = None  # seconds, for the Retry-After header


class RateLimiter:
    def __init__(self, rpm: int, max_concurrency: int, clock=time.monotonic):
        self.rpm = rpm
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._accepted: deque[float] = deque()  # timestamps of accepted requests
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def _evict(self, now: float) -> None:
        while self._accepted and now - self._accepted[0] >= WINDOW_S:
            self._accepted.popleft()

    def try_acquire(self) -> Decision:
        """Admit or reject a request. On admit, caller MUST call release() when done."""
        now = self._clock()
        self._evict(now)

        if self._in_flight >= self.max_concurrency:
            # True wait time is unknowable (depends on model latency); 1s is a polite hint.
            return Decision(allowed=False, reason="concurrency", retry_after=1)

        if len(self._accepted) >= self.rpm:
            # Seconds until the oldest accepted request ages out of the window.
            retry = math.ceil(WINDOW_S - (now - self._accepted[0]))
            return Decision(allowed=False, reason="rpm", retry_after=max(retry, 1))

        self._accepted.append(now)
        self._in_flight += 1
        return Decision(allowed=True)

    def release(self) -> None:
        assert self._in_flight > 0, "release() without matching try_acquire()"
        self._in_flight -= 1
