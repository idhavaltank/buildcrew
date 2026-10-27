#!/usr/bin/env python3
"""Sanitize one host input and route it through BuildCrew's universal loop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.safety_boundaries import redact_value  # noqa: E402


class IntakeError(ValueError):
    """Raised when a host intake cannot be safely normalized."""


def _text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise IntakeError(f"{label} must be text")
    value = redact_value(" ".join(value.strip().split()))
    if not value or len(value) > maximum or "\x00" in value:
        raise IntakeError(f"{label} is empty, too long, or contains a NUL")
    return value


def sanitize_intake(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only task-routing fields; never forward a raw prompt or authority."""

    if not isinstance(value, Mapping):
        raise IntakeError("input must be an object")
    result: dict[str, Any] = {}
    request_type = value.get("requestType", "QUESTION")
    result["requestType"] = _text(request_type, "requestType", 40).upper()
    if "actionable" in value:
        if not isinstance(value["actionable"], bool):
            raise IntakeError("actionable must be boolean")
        result["actionable"] = value["actionable"]
    for key in ("taskId", "objective", "project", "repository", "sourceBinding"):
        if value.get(key) is not None:
            result[key] = _text(value[key], key)
    criteria = value.get("acceptanceCriteria")
    if criteria is not None:
        if not isinstance(criteria, list) or len(criteria) > 20:
            raise IntakeError("acceptanceCriteria must be a list of at most 20 items")
        result["acceptanceCriteria"] = [_text(item, "acceptanceCriteria item", 300) for item in criteria]
    if "summary" in value:
        result["summary"] = _text(value["summary"], "summary")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--memory-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        source = json.loads(args.input_json.read_text(encoding="utf-8"))
        intake = sanitize_intake(source)
        with tempfile.TemporaryDirectory(prefix="buildcrew-intake-") as directory:
            path = Path(directory) / "intake.json"
            path.write_text(json.dumps(intake, sort_keys=True) + "\n", encoding="utf-8")
            command = [sys.executable, "-B", str(PACKAGE_ROOT / "scripts" / "context_loop.py"), "auto", "--input-json", str(path)]
            if args.state_root:
                command.extend(["--state-root", str(args.state_root)])
            if args.memory_root:
                command.extend(["--memory-root", str(args.memory_root)])
            completed = subprocess.run(command, cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False)
        if completed.stdout:
            if args.output:
                args.output.write_text(completed.stdout, encoding="utf-8")
            else:
                print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        return completed.returncode
    except (OSError, UnicodeError, json.JSONDecodeError, IntakeError) as error:
        print(json.dumps({"outcome": "BLOCKED", "errors": [str(error)]}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
