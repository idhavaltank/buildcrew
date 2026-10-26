#!/usr/bin/env python3
"""Profile a portable task and optionally evaluate it against one adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.task_packet import profile_task, validate_task_packet  # noqa: E402
from engine.tool_adapter import evaluate_task_against_adapter  # noqa: E402


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--adapter", type=Path, help="optional tool-adapter manifest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        packet = _read(args.packet)
        if not isinstance(packet, dict):
            raise ValueError("task packet must be an object")
        if args.adapter:
            adapter = _read(args.adapter)
            if not isinstance(adapter, dict):
                raise ValueError("tool adapter must be an object")
            result = evaluate_task_against_adapter(packet, adapter)
            exit_code = 0 if result["status"] == "READY" else 1
        else:
            result = profile_task(packet)
            exit_code = 0 if result["status"] != "INVALID" else 1
        if not validate_task_packet(packet)["valid"]:
            exit_code = 1
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return exit_code
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
