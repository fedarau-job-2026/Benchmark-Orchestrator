"""Inference backends behind a common interface.

See decisions/001-mock-backend.md: OllamaBackend is the default; MockBackend
simulates realistic latency so the rate limiter can be exercised without a model.
"""

import asyncio
import random
from abc import ABC, abstractmethod

import httpx


class GenerationError(Exception):
    """Backend failed to produce a response (surfaced as HTTP 502)."""


class Backend(ABC):
    @abstractmethod
    async def generate(self, prompt: str) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    async def startup(self) -> None:  # optional hook
        pass

    async def shutdown(self) -> None:  # optional hook
        pass


class MockBackend(Backend):
    """Simulates inference latency; returns canned text (accuracy is meaningless here)."""

    def __init__(self, latency_ms: int, jitter_ms: int):
        self._latency_ms = latency_ms
        self._jitter_ms = jitter_ms

    @property
    def model_name(self) -> str:
        return "mock"

    async def generate(self, prompt: str) -> str:
        jitter = random.uniform(-self._jitter_ms, self._jitter_ms)
        await asyncio.sleep(max(0.0, (self._latency_ms + jitter) / 1000.0))
        return f"[mock] response to: {prompt[:60]}"


class OllamaBackend(Backend):
    def __init__(self, base_url: str, model: str, timeout_s: float):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    @property
    def model_name(self) -> str:
        return self._model

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_s
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def generate(self, prompt: str) -> str:
        assert self._client, "startup() not called"
        try:
            resp = await self._client.post(
                "/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    # Keep the model resident between requests so latency stays flat.
                    "keep_alive": "10m",
                },
            )
            resp.raise_for_status()
            return resp.json()["response"]
        except (httpx.HTTPError, KeyError) as e:
            raise GenerationError(f"ollama backend error: {e}") from e
