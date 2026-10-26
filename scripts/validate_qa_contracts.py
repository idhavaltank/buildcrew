#!/usr/bin/env python3
"""Validate one advisory QA contract and print portable JSON evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.qa_contracts import VALIDATORS, validate_qa_contract  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(set(VALIDATORS) | {"qa-receipt"}), help="QA contract kind")
    parser.add_argument("file", type=Path, help="JSON contract to validate")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"kind": args.kind, "valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    result = validate_qa_contract(args.kind, value)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
