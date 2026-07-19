"""Env-driven configuration. Every artificial constraint is a knob reviewers can turn."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Rate-limit constraints (the assignment's artificial limits)
    rpm: int = 60                     # max accepted requests per trailing 60s
    max_concurrency: int = 4          # max in-flight requests

    # Backend selection
    backend: str = "ollama"           # "ollama" | "mock"
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen2.5:0.5b"
    ollama_timeout_s: float = 120.0

    # Mock backend latency shape
    mock_latency_ms: int = 400        # mean simulated inference latency
    mock_jitter_ms: int = 200         # +/- uniform jitter

    # Server
    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()
