"""Rate-limited inference HTTP service.

The OpenAPI spec generated from this module (exported to ../../docs/openapi.json,
browsable at /docs) is the ONLY contract shared with the orchestrator.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .backends import Backend, GenerationError, MockBackend, OllamaBackend
from .config import Settings, settings
from .rate_limiter import RateLimiter


class GenerateRequest(BaseModel):
    prompt: str = Field(description="The prompt to run through the model.")


class GenerateResponse(BaseModel):
    response: str = Field(description="The model's completion text.")
    model: str = Field(description="Name of the model that produced the response.")


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    backend: str
    model: str


RATE_LIMITED_RESPONSE = {
    "description": (
        "Rate limit breached. `Retry-After` (seconds) is always set. "
        "`X-RateLimit-Reason` is an advisory hint: `rpm` or `concurrency`."
    ),
    "model": ErrorResponse,
    "headers": {
        "Retry-After": {
            "description": "Seconds to wait before retrying.",
            "schema": {"type": "integer", "minimum": 1},
        },
        "X-RateLimit-Reason": {
            "description": "Which limit was breached: `rpm` or `concurrency`. Advisory only.",
            "schema": {"type": "string", "enum": ["rpm", "concurrency"]},
        },
    },
}


def build_backend(cfg: Settings) -> Backend:
    if cfg.backend == "mock":
        return MockBackend(latency_ms=cfg.mock_latency_ms, jitter_ms=cfg.mock_jitter_ms)
    if cfg.backend == "ollama":
        return OllamaBackend(
            base_url=cfg.ollama_url, model=cfg.model, timeout_s=cfg.ollama_timeout_s
        )
    raise ValueError(f"unknown backend {cfg.backend!r} (expected 'ollama' or 'mock')")


def create_app(
    cfg: Settings | None = None,
    limiter: RateLimiter | None = None,
    backend: Backend | None = None,
) -> FastAPI:
    cfg = cfg or settings
    limiter = limiter or RateLimiter(rpm=cfg.rpm, max_concurrency=cfg.max_concurrency)
    backend = backend or build_backend(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await backend.startup()
        yield
        await backend.shutdown()

    app = FastAPI(
        title="Rate-Limited Inference Service",
        version="1.0.0",
        description=(
            "Thin HTTP wrapper around a local model that enforces hard capacity "
            "limits: max requests per minute and max concurrent in-flight requests. "
            "Breaches get an immediate HTTP 429 with a `Retry-After` header — "
            "no queueing, no silent drops."
        ),
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.state.backend = backend

    @app.post(
        "/generate",
        response_model=GenerateResponse,
        responses={429: RATE_LIMITED_RESPONSE, 502: {"model": ErrorResponse}},
    )
    async def generate(req: GenerateRequest) -> GenerateResponse | JSONResponse:
        decision = limiter.try_acquire()
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": f"rate limit breached ({decision.reason})"},
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Reason": decision.reason,
                },
            )
        try:
            text = await backend.generate(req.prompt)
            return GenerateResponse(response=text, model=backend.model_name)
        except GenerationError as e:
            return JSONResponse(status_code=502, content={"detail": str(e)})
        finally:
            limiter.release()

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(
            status="ok", backend=type(backend).__name__, model=backend.model_name
        )

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
