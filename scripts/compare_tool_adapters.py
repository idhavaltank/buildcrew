#!/usr/bin/env python3
"""Compare one normalized task across several tool adapters without executing them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.tool_adapter import compare_task_across_adapters  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--adapter", action="append", required=True, metavar="NAME=FILE", help="repeat for each adapter")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        adapters: list[dict[str, object]] = []
        for item in args.adapter:
            if "=" not in item:
                raise ValueError("--adapter must use NAME=FILE")
            _, filename = item.split("=", 1)
            adapter = json.loads(Path(filename).read_text(encoding="utf-8"))
            if not isinstance(adapter, dict):
                raise ValueError("adapter must be an object")
            adapters.append(adapter)
        result = compare_task_across_adapters(packet, adapters)
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if result["overallStatus"] == "COMPATIBLE" else 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"overallStatus": "INVALID", "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
