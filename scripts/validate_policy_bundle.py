#!/usr/bin/env python3
"""Validate a policy-bundle-v1 JSON document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_receipts import validate_policy_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a policy bundle")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 1
    if isinstance(value, dict) and "policyBundle" in value:
        value = value["policyBundle"]
    result = validate_policy_bundle(value)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
