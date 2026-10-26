#!/usr/bin/env python3
"""Explain one built-in policy rule without changing state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.policy_catalog import explain_rule  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a policy rule")
    parser.add_argument("rule_id")
    args = parser.parse_args(argv)
    rule = explain_rule(args.rule_id)
    if rule is None:
        print(json.dumps({"valid": False, "error": f"unknown policy rule: {args.rule_id}"}, sort_keys=True))
        return 1
    print(json.dumps(rule, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

