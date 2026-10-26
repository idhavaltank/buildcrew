#!/usr/bin/env python3
"""Validate one identity-bound, time-limited AE evidence record."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.evidence_freshness import validate_fresh_evidence  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="JSON assignment or receipt to validate")
    parser.add_argument("--expected", type=Path, required=True, help="JSON identity binding")
    parser.add_argument("--now", help="UTC ISO-8601 reference time for deterministic checks")
    args = parser.parse_args(argv)
    try:
        record = json.loads(args.file.read_text(encoding="utf-8"))
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
        if now is not None and now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        result = validate_fresh_evidence(record, expected, now=now)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

