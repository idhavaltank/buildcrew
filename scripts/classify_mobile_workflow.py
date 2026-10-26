#!/usr/bin/env python3
"""Classify a mobile request before expensive repository-wide sidecars run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.mobile_workflow import resolve_mobile_model_selection  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="JSON file or inline JSON intake object")
    parser.add_argument("--platform", action="append", default=[], help="mobile, Flutter, Android, iOS, React Native, or Expo")
    parser.add_argument("--target", action="append", default=[], help="target/module hint")
    parser.add_argument("--objective", default="")
    parser.add_argument("--acceptance-criterion", action="append", default=[])
    parser.add_argument("--request-type", default="NEW_TASK")
    parser.add_argument("--task-class", default="")
    parser.add_argument("--risk-tier", default="")
    parser.add_argument("--runtime-validation", action="store_true")
    return parser


def _load_input(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    candidate = Path(raw)
    if candidate.is_file():
        value = json.loads(candidate.read_text(encoding="utf-8"))
    else:
        value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return dict(value)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        intake = _load_input(args.input)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 1
    if args.platform:
        intake["platforms"] = args.platform
    if args.target:
        intake["targets"] = args.target
    if args.objective:
        intake["normalizedObjective"] = args.objective
    if args.acceptance_criterion:
        intake["acceptanceCriteria"] = args.acceptance_criterion
    if args.request_type:
        intake["requestType"] = args.request_type
    if args.task_class:
        intake["taskClass"] = args.task_class
    if args.risk_tier:
        intake["riskTier"] = args.risk_tier
    if args.runtime_validation:
        intake["runtimeValidation"] = True
    routing = resolve_mobile_model_selection(intake)
    print(json.dumps(routing, indent=2, sort_keys=True))
    return 0 if routing["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
