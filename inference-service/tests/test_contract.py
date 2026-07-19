"""The committed spec in docs/openapi.json IS the contract (ADR 003).

If this test fails, the API surface changed: re-run scripts/export_openapi.py and
review the diff deliberately.
"""

import json
from pathlib import Path

from inference_service.app import create_app
from inference_service.config import Settings

COMMITTED = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_live_spec_matches_committed_contract():
    live = create_app(Settings(backend="mock")).openapi()
    committed = json.loads(COMMITTED.read_text())
    assert live == committed, (
        "OpenAPI spec drifted from docs/openapi.json — "
        "run `uv run python scripts/export_openapi.py` and review the diff"
    )
