#!/usr/bin/env python3
"""Validate, write, discover, or render safe AE session handoffs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.session_handoff import (  # noqa: E402
    HandoffContractError,
    classify_handoff_attachment,
    discover_handoff_packets,
    render_handoff_summary,
    validate_handoff_packet,
    write_handoff_packet,
)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("packet", type=Path)
    validate.add_argument("--now")

    write = subparsers.add_parser("write")
    write.add_argument("packet", type=Path)
    write.add_argument("--state-root", type=Path, required=True)
    write.add_argument("--repository-root", type=Path, required=True)
    write.add_argument("--protected-root", type=Path, action="append", default=[])
    write.add_argument("--now")

    discover = subparsers.add_parser("discover")
    discover.add_argument("context", type=Path)
    discover.add_argument("--state-root", type=Path, required=True)
    discover.add_argument("--repository-root", type=Path, required=True)
    discover.add_argument("--protected-root", type=Path, action="append", default=[])
    discover.add_argument("--now")

    attach = subparsers.add_parser("attach")
    attach.add_argument("packet", type=Path)
    attach.add_argument("context", type=Path)
    attach.add_argument("--now")

    render = subparsers.add_parser("render")
    render.add_argument("packet", type=Path)
    render.add_argument("--now")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_handoff_packet(_read(args.packet), now=args.now)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["valid"] else 2
        if args.command == "write":
            path = write_handoff_packet(
                _read(args.packet),
                args.state_root,
                args.repository_root,
                now=args.now,
                protected_roots=args.protected_root,
            )
            print(json.dumps({"status": "WRITTEN", "file": path.name, "authorityTransferred": False}, sort_keys=True))
            return 0
        if args.command == "discover":
            result = discover_handoff_packets(
                args.state_root,
                args.repository_root,
                _read(args.context),
                now=args.now,
                protected_roots=args.protected_root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "COMPLETE" else 2
        if args.command == "attach":
            result = classify_handoff_attachment(_read(args.packet), _read(args.context), now=args.now)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["classification"] != "REJECTED" else 2
        print(render_handoff_summary(_read(args.packet), now=args.now), end="")
        return 0
    except (OSError, json.JSONDecodeError, HandoffContractError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
