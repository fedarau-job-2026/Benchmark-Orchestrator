"""Black-box tests of the HTTP surface: 429 semantics, headers, no queueing."""

import asyncio

import httpx
import pytest

from inference_service.app import create_app
from inference_service.config import Settings


def make_app(**overrides):
    kwargs = {"backend": "mock", "mock_latency_ms": 50, "mock_jitter_ms": 0, **overrides}
    return create_app(Settings(**kwargs))


@pytest.fixture
def client_factory():
    def _make(app):
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return _make


async def test_generate_happy_path(client_factory):
    app = make_app()
    async with client_factory(app) as client:
        r = await client.post("/generate", json={"prompt": "2+2?"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "mock"
    assert "response" in body


async def test_concurrency_cap_over_http(client_factory):
    app = make_app(max_concurrency=2, rpm=1000, mock_latency_ms=200)
    async with client_factory(app) as client:
        results = await asyncio.gather(
            *[client.post("/generate", json={"prompt": "q"}) for _ in range(5)]
        )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 200, 429, 429, 429]
    rejected = [r for r in results if r.status_code == 429]
    for r in rejected:
        assert r.headers["Retry-After"] == "1"
        assert r.headers["X-RateLimit-Reason"] == "concurrency"


async def test_rpm_cap_over_http(client_factory):
    app = make_app(rpm=3, max_concurrency=100, mock_latency_ms=1)
    async with client_factory(app) as client:
        for _ in range(3):
            r = await client.post("/generate", json={"prompt": "q"})
            assert r.status_code == 200
        r = await client.post("/generate", json={"prompt": "q"})
    assert r.status_code == 429
    assert r.headers["X-RateLimit-Reason"] == "rpm"
    assert 1 <= int(r.headers["Retry-After"]) <= 60


async def test_429_is_immediate_no_queueing(client_factory):
    """A rejected request must return without waiting on the backend."""
    app = make_app(max_concurrency=1, rpm=1000, mock_latency_ms=500)
    async with client_factory(app) as client:
        slow = asyncio.create_task(client.post("/generate", json={"prompt": "slow"}))
        await asyncio.sleep(0.05)  # let the slow request occupy the single slot
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        r = await client.post("/generate", json={"prompt": "fast-reject"})
        elapsed = loop.time() - t0
        await slow
    assert r.status_code == 429
    assert elapsed < 0.2, f"429 took {elapsed:.3f}s — service must not queue"


async def test_healthz(client_factory):
    app = make_app()
    async with client_factory(app) as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "backend": "MockBackend", "model": "mock"}


async def test_validation_error_on_bad_body(client_factory):
    app = make_app()
    async with client_factory(app) as client:
        r = await client.post("/generate", json={"not_prompt": 1})
    assert r.status_code == 422
