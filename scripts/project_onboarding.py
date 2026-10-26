#!/usr/bin/env python3
"""Inspect or complete one-time project onboarding for a host integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.project_onboarding import (  # noqa: E402
    ProjectOnboardingError,
    complete_onboarding,
    inspect_onboarding,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repository", help="repository root or a path inside it")
    parser.add_argument("--state-root", required=True, help="explicit host-controlled external state root")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--installation-id", required=True, help="stable ID for this skill installation")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-time project onboarding boundary")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="classify the project and decide whether the first-time message is needed")
    _common(inspect)
    complete = commands.add_parser("complete", help="persist sanitized context and mark onboarding complete")
    _common(complete)
    complete.add_argument("--context-json", required=True, help="JSON file with summary, decisions, references, and nextAction")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_onboarding(
                args.repository,
                state_root=args.state_root,
                project_id=args.project_id,
                host_id=args.host_id,
                installation_id=args.installation_id,
            )
        else:
            context = json.loads(Path(args.context_json).read_text(encoding="utf-8"))
            result = complete_onboarding(
                args.repository,
                state_root=args.state_root,
                project_id=args.project_id,
                host_id=args.host_id,
                installation_id=args.installation_id,
                context=context,
            )
    except (ProjectOnboardingError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
