"""HTTP client for the inference service — one attempt per call.

Retry policy lives in the runner; this module just translates HTTP reality
(2xx / 429+Retry-After / 5xx / timeouts) into a typed Outcome.
"""

import time
from dataclasses import dataclass
from enum import Enum

import httpx


class Status(Enum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(frozen=True)
class Outcome:
    status: Status
    latency_s: float
    text: str = ""
    reason: str | None = None       # X-RateLimit-Reason if the server sent it
    retry_after: float = 1.0        # from Retry-After header
    error: str = ""


class InferenceClient:
    def __init__(self, base_url: str, timeout_s: float = 180.0):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=256, max_keepalive_connections=64),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def healthy(self) -> bool:
        try:
            r = await self._client.get("/healthz")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate(self, prompt: str) -> Outcome:
        t0 = time.monotonic()
        try:
            r = await self._client.post("/generate", json={"prompt": prompt})
        except httpx.HTTPError as e:
            return Outcome(
                status=Status.ERROR,
                latency_s=time.monotonic() - t0,
                error=f"{type(e).__name__}: {e}",
            )
        latency = time.monotonic() - t0

        if r.status_code == 200:
            return Outcome(status=Status.OK, latency_s=latency, text=r.json()["response"])

        if r.status_code == 429:
            try:
                retry_after = float(r.headers.get("Retry-After", "1"))
            except ValueError:
                retry_after = 1.0
            return Outcome(
                status=Status.RATE_LIMITED,
                latency_s=latency,
                reason=r.headers.get("X-RateLimit-Reason"),
                retry_after=retry_after,
            )

        return Outcome(
            status=Status.ERROR,
            latency_s=latency,
            error=f"HTTP {r.status_code}: {r.text[:200]}",
        )
