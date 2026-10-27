#!/usr/bin/env python3
"""Route every Codex UserPromptSubmit event through BuildCrew."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PACKAGE_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
ADAPTER = PACKAGE_ROOT / "scripts" / "global_input_adapter.py"

ENGINEERING_WORDS = (
    "plan", "build", "implement", "fix", "change", "add", "update", "refactor",
    "test", "review", "debug", "diagnos", "investigat", "migrat", "deploy",
    "release", "ship", "repair", "create", "remove", "delete", "modify",
)


def _classify(prompt: str) -> tuple[str, bool]:
    lowered = prompt.casefold()
    if any(word in lowered for word in ENGINEERING_WORDS):
        if any(word in lowered for word in ("review", "audit")):
            return "REVIEW", True
        if any(word in lowered for word in ("test", "verify", "validate")):
            return "TEST", True
        if any(word in lowered for word in ("debug", "diagnos", "investigat", "failure", "bug")):
            return "INVESTIGATION", True
        return "IMPLEMENTATION", True
    return "QUESTION", False


def _run(payload: dict) -> dict:
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), str) else ""
    prompt = " ".join(prompt.strip().split())[:500]
    request_type, actionable = _classify(prompt)
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    cwd_path = Path(cwd).resolve(strict=False)
    intake = {
        "requestType": request_type,
        "actionable": actionable,
        "objective": prompt or "Codex input without prompt text.",
        "project": cwd_path.name or "codex-project",
        "repository": cwd_path.as_posix(),
        "sourceBinding": cwd_path.as_posix(),
    }
    with tempfile.TemporaryDirectory(prefix="buildcrew-hook-") as directory:
        input_path = Path(directory) / "intake.json"
        input_path.write_text(json.dumps(intake, sort_keys=True) + "\n", encoding="utf-8")
        environment = dict(os.environ)
        environment.setdefault("BUILDCREW_HOME", str(Path(os.environ.get("PLUGIN_DATA", Path.home() / ".local" / "share")) / "buildcrew-home"))
        completed = subprocess.run(
            [sys.executable, "-B", str(ADAPTER), "--input-json", str(input_path)],
            cwd=PACKAGE_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    if completed.returncode != 0:
        return {
            "systemMessage": "BuildCrew global intake was unavailable; the host should continue without claiming continuity proof.",
        }
    try:
        result = json.loads(completed.stdout)
        outcome = result.get("outcome", "ROUTED")
    except (json.JSONDecodeError, TypeError):
        outcome = "ROUTED"
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"BuildCrew global intake completed: {outcome}. Follow the loaded BuildCrew workflow for this input.",
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = _run(payload if isinstance(payload, dict) else {})
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, ValueError) as error:
        result = {"systemMessage": f"BuildCrew global intake was unavailable: {type(error).__name__}."}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
