"""Export the service's OpenAPI spec to docs/openapi.json — the shared contract.

Run from inference-service/:  uv run python scripts/export_openapi.py
"""

import json
from pathlib import Path

from inference_service.app import create_app
from inference_service.config import Settings

OUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def main() -> None:
    app = create_app(Settings(backend="mock"))  # backend choice doesn't affect the spec
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
