#!/usr/bin/env python3
"""Explain one deterministic model-selection fixture without raw prompt data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.model_selection import (  # noqa: E402
    DECISION_SCHEMA_VERSION,
    ModelSelectionError,
    explain_selection,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain an AE model-selection fixture")
    parser.add_argument("--fixture", required=True, type=Path, help="taskFacts/hostCatalog fixture JSON path")
    parser.add_argument(
        "--policy",
        type=Path,
        default=PACKAGE_ROOT / "config" / "model-selection-v1.json",
        help="model-selection policy JSON path",
    )
    args = parser.parse_args(argv)
    try:
        fixture = _load(args.fixture)
        policy = _load(args.policy)
        result = explain_selection(fixture, policy)
    except (OSError, json.JSONDecodeError, ModelSelectionError) as exc:
        result = {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "status": "REJECTED",
            "assignments": [],
            "errors": [type(exc).__name__ + ": invalid input"],
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("status") not in {"REJECTED", "INVALIDATED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
