#!/usr/bin/env python3
"""Run the configured AE runtime coordinator for one bounded operation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.runtime_coordinator import RuntimeCoordinator, RuntimeCoordinatorError  # noqa: E402


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_value(request: dict[str, object], key: str) -> object:
    if key not in request:
        raise RuntimeCoordinatorError(f"runtime request is missing {key}")
    return request[key]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="runtime-coordinator-config-v1 JSON file")
    parser.add_argument("--operation", choices={"status", "spawn-agent", "execute-provider"}, required=True)
    parser.add_argument("--request", type=Path, help="JSON request for spawn-agent or execute-provider")
    args = parser.parse_args(argv)

    try:
        config = _read_json(args.config)
        coordinator = RuntimeCoordinator.from_config(config)
        if args.operation == "status":
            result = coordinator.status()
        else:
            if args.request is None:
                raise RuntimeCoordinatorError("--request is required for runtime execution")
            request = _read_json(args.request)
            if not isinstance(request, dict):
                raise RuntimeCoordinatorError("runtime request must be a JSON object")
            expected_policy = request.get("expectedPolicy")
            if expected_policy is not None and not isinstance(expected_policy, dict):
                raise RuntimeCoordinatorError("expectedPolicy must be a JSON object")
            assignment = _request_value(request, "assignment")
            if not isinstance(assignment, dict):
                raise RuntimeCoordinatorError("assignment must be a JSON object")
            if args.operation == "spawn-agent":
                slot = _request_value(request, "slot")
                if not isinstance(slot, dict):
                    raise RuntimeCoordinatorError("slot must be a JSON object")
                result = coordinator.spawn_agent(assignment, slot, expected_policy=expected_policy)
            else:
                result = coordinator.execute_provider(assignment, expected_policy=expected_policy)
    except (OSError, json.JSONDecodeError, TypeError, RuntimeCoordinatorError) as exc:
        print(json.dumps({"status": "FAILED", "live": False, "blockers": [str(exc)]}, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
