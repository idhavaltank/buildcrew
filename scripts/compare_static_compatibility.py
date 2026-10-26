#!/usr/bin/env python3
"""Compare two source projections without launching or changing either target."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.compatibility import (  # noqa: E402
    compare_static,
    compare_static_set,
    validate_static_compatibility,
    validate_static_compatibility_set,
)


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, nargs="?", help="left source projection JSON")
    parser.add_argument("right", type=Path, nargs="?", help="right source projection JSON")
    parser.add_argument("--left-name", default="web")
    parser.add_argument("--right-name", default="mobile")
    parser.add_argument("--projection", action="append", default=[], metavar="NAME=FILE", help="named source projection; repeat for a multi-projection report")
    parser.add_argument("--anchor", help="anchor label for --projection comparisons")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.projection:
            projections: dict[str, object] = {}
            for item in args.projection:
                if "=" not in item:
                    raise ValueError("--projection must use NAME=FILE")
                name, filename = item.split("=", 1)
                if not name.strip() or not filename.strip():
                    raise ValueError("--projection must contain a non-empty name and file")
                if name in projections:
                    raise ValueError(f"duplicate projection name: {name}")
                projections[name] = _read(Path(filename))
            result = compare_static_set(projections, anchor=args.anchor)
            validation = validate_static_compatibility_set(result)
        else:
            if args.left is None or args.right is None:
                raise ValueError("two projection files are required unless --projection is used")
            result = compare_static(_read(args.left), _read(args.right), names=(args.left_name, args.right_name))
            validation = validate_static_compatibility(result)
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
