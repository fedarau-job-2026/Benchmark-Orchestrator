"""A test double of the inference service, implemented from docs/openapi.json.

Deliberately an independent implementation (never imported from the sibling
project — see CLAUDE.md): the orchestrator is tested against the *contract*,
exactly as you would simulate a third-party API. Limits are mutable at runtime
so tests can exercise mid-run limit changes.
"""

import asyncio
import math
import time
from collections import deque

from fastapi import FastAPI
from fastapi.responses import JSONResponse


class FakeInferenceServer:
    def __init__(self, rpm: int, max_concurrency: int, latency_s: float = 0.03):
        self.rpm = rpm
        self.max_concurrency = max_concurrency
        self.latency_s = latency_s
        self.accepted: deque[float] = deque()
        self.in_flight = 0
        self.total_requests = 0
        self.total_429 = 0
        self.max_observed_inflight = 0
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/healthz")
        async def healthz():
            return {"status": "ok", "backend": "fake", "model": "fake"}

        @app.post("/generate")
        async def generate(body: dict):
            self.total_requests += 1
            now = time.monotonic()
            while self.accepted and now - self.accepted[0] >= 60.0:
                self.accepted.popleft()

            if self.in_flight >= self.max_concurrency:
                self.total_429 += 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit breached (concurrency)"},
                    headers={"Retry-After": "1", "X-RateLimit-Reason": "concurrency"},
                )
            if len(self.accepted) >= self.rpm:
                self.total_429 += 1
                retry = max(1, math.ceil(60.0 - (now - self.accepted[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit breached (rpm)"},
                    headers={"Retry-After": str(retry), "X-RateLimit-Reason": "rpm"},
                )

            self.accepted.append(now)
            self.in_flight += 1
            self.max_observed_inflight = max(self.max_observed_inflight, self.in_flight)
            try:
                await asyncio.sleep(self.latency_s)
                prompt = body.get("prompt", "")
                return {"response": f"fake answer to: {prompt}", "model": "fake"}
            finally:
                self.in_flight -= 1

        return app


async def serve(server: FakeInferenceServer, port: int):
    """Run the fake server in-process; returns (uvicorn_server, task)."""
    import uvicorn

    config = uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    return uv, task
