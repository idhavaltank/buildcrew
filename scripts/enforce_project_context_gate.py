#!/usr/bin/env python3
"""Evaluate the v2 enforced project-context preflight contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_gate import enforce_project_context_gate, validate_enforced_gate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--context-map", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--reuse-assessment", type=Path)
    parser.add_argument("--action", choices=["routing", "model_selection", "agent_selection", "mutation", "closure"], default="routing")
    args = parser.parse_args(argv)
    values = [json.loads(path.read_text(encoding="utf-8")) for path in (args.intake, args.context_map, args.receipt, args.policy)]
    reuse_assessment = json.loads(args.reuse_assessment.read_text(encoding="utf-8")) if args.reuse_assessment else None
    result = enforce_project_context_gate(*values, action=args.action, reuse_assessment=reuse_assessment)
    result["validation"] = validate_enforced_gate(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["validation"]["valid"] and result["decision"] == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
