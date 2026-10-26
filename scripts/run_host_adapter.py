#!/usr/bin/env python3
"""Run one explicitly configured host adapter request.

The command is intentionally narrow: it reads a validated manifest, sends one
JSON request to the fixed executable through stdin, and prints one bounded
JSON result. The host adapter remains responsible for authentication and any
external side effects.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_execution import (  # noqa: E402
    HostExecutionError,
    SubprocessNativeAgentTransport,
    SubprocessProviderTransport,
    validate_host_execution_manifest,
)


def _read_json(path: str) -> object:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Host-execution manifest JSON file")
    parser.add_argument("--request", required=True, help="Request JSON file, or - for stdin")
    args = parser.parse_args(argv)

    try:
        manifest = _read_json(str(args.manifest))
        request = _read_json(args.request)
        validation = validate_host_execution_manifest(manifest)
        if not validation["valid"]:
            raise HostExecutionError("invalid host execution manifest: " + "; ".join(validation["errors"]))
        if not isinstance(request, dict):
            raise HostExecutionError("host execution request must be a JSON object")
        if manifest["operation"] == "native-spawn":
            response = SubprocessNativeAgentTransport(manifest).spawn(request)
        else:
            response = SubprocessProviderTransport(manifest).execute(request)
    except (OSError, json.JSONDecodeError, TypeError, HostExecutionError) as exc:
        print(json.dumps({"status": "FAILED", "live": False, "blockers": [str(exc)]}, sort_keys=True))
        return 2

    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
