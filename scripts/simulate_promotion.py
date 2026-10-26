#!/usr/bin/env python3
"""Simulate candidate promotion and rollback using disposable state only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.promotion_simulation import simulate_promotion, validate_promotion_simulation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="candidate JSON object")
    parser.add_argument("--activation-requested", action="store_true", help="exercise modeled activation and rollback")
    parser.add_argument("--canary-fail", action="store_true", help="exercise the failed-canary recovery path")
    parser.add_argument("--recovery-fail", action="store_true", help="exercise a failed recovery assertion")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        result = simulate_promotion(
            candidate,
            canary_pass=not args.canary_fail,
            activation_requested=args.activation_requested,
            recovery_pass=not args.recovery_fail,
        )
        validation = validate_promotion_simulation(result)
        rendered = json.dumps({**result, "validation": validation}, indent=2, sort_keys=True) + "\n"
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
