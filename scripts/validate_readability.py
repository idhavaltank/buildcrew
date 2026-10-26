#!/usr/bin/env python3
"""Create or validate a source-only human-readability assessment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.readability_gate import (  # noqa: E402
    ReadabilityGateError,
    build_readability_assessment,
    validate_readability_assessment,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or validate a source-only readability assessment")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--files-json", help="JSON array of changed files with path, content, and optional language")
    modes.add_argument("--assessment-json", help="Existing readability assessment JSON to validate")
    parser.add_argument("--task-id", help="Task identity required when creating an assessment")
    parser.add_argument("--source-fingerprint", help="Current source fingerprint required when creating an assessment")
    parser.add_argument("--policy-json", help="Optional JSON object overriding the bounded readability policy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.assessment_json:
            value = json.loads(Path(args.assessment_json).read_text(encoding="utf-8"))
            validation = validate_readability_assessment(value)
            print(json.dumps({"validation": validation, "assessment": value}, sort_keys=True, indent=2))
            return 0 if validation["valid"] else 2

        if not args.task_id or not args.source_fingerprint:
            raise ReadabilityGateError("--task-id and --source-fingerprint are required with --files-json")
        files = json.loads(Path(args.files_json).read_text(encoding="utf-8"))
        policy = json.loads(Path(args.policy_json).read_text(encoding="utf-8")) if args.policy_json else None
        assessment = build_readability_assessment(
            task_id=args.task_id,
            source_fingerprint=args.source_fingerprint,
            changed_files=files,
            policy=policy,
        )
        print(json.dumps(assessment, sort_keys=True, indent=2))
        return 0 if assessment["status"] == "PASS" else 2
    except (ReadabilityGateError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
