#!/usr/bin/env python3
"""Configure, verify, or roll back BuildCrew's global Codex instructions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.global_activation import (  # noqa: E402
    GlobalActivationError,
    configure_global_activation,
    rollback_global_activation,
    verify_global_activation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "rollback"))
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--allow-controller-conflict", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = configure_global_activation(
                codex_home=args.codex_home,
                state_root=args.state_root,
                allow_controller_conflict=args.allow_controller_conflict,
            )
        elif args.command == "verify":
            result = verify_global_activation(codex_home=args.codex_home, state_root=args.state_root)
        else:
            result = rollback_global_activation(codex_home=args.codex_home, state_root=args.state_root)
    except (GlobalActivationError, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"CONFIGURED", "ROLLED_BACK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
