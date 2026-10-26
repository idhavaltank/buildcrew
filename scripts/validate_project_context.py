#!/usr/bin/env python3
"""Validate project-context maps, receipts, and manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_receipts import validate_context_map, validate_manifest, validate_receipt  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a project-context contract")
    parser.add_argument("path", type=Path)
    parser.add_argument("--kind", choices=("auto", "map", "receipt", "manifest"), default="auto")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 1
    kind = args.kind
    if kind == "auto":
        version = value.get("schemaVersion") if isinstance(value, dict) else None
        if isinstance(value, dict) and "contextMap" in value:
            kind = "map"
            value = value["contextMap"]
        elif isinstance(value, dict) and "receipt" in value:
            kind = "receipt"
            value = value["receipt"]
        else:
            kind = {"project-context-map-v1": "map", "context-discovery-receipt-v1": "receipt", "project-context-manifest-v1": "manifest"}.get(version, "map")
    elif isinstance(value, dict) and kind in {"map", "receipt", "manifest"}:
        wrapper_key = {"map": "contextMap", "receipt": "receipt", "manifest": "manifest"}[kind]
        if wrapper_key in value:
            value = value[wrapper_key]
    validator = {"map": validate_context_map, "receipt": validate_receipt, "manifest": validate_manifest}[kind]
    result = validator(value)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
