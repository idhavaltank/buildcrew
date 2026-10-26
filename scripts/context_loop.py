#!/usr/bin/env python3
"""Run the BuildCrew day-to-day context and memory continuity loop."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PACKAGE_ROOT / "scripts"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_continuity import (  # noqa: E402
    attach_worklog_result,
    close_continuity,
    default_state_root,
    measure_input_context,
    start_or_resume,
)
from learning_runtime import classify_input  # noqa: E402
from memory_context_runtime import lookup_memory_context  # noqa: E402
from storage_paths import resolve_storage_paths  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must contain an object")
    return value


def _write_or_print(value: dict, output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


def _iso_date_time(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.date().isoformat(), parsed.time().replace(microsecond=0).isoformat()


def _run_worklog(
    report: dict,
    closure: dict,
    *,
    state_root: Path,
    worklog_root: Path | None,
    now: str | None,
) -> dict:
    state = report["state"]
    task_id = report["taskId"]
    command = [
        sys.executable,
        str(SCRIPT_ROOT / "update_daily_log.py"),
        "--title",
        f"AE continuity: {task_id}",
        "--session-id",
        task_id,
        "--summary",
        str(closure.get("summary") or closure.get("claim") or "Terminal continuity update."),
        "--complete",
        f"Continuity status: {state.get('status')}",
        "--todo",
        str(closure.get("nextAction") or "Resume the task with current-source revalidation."),
        "--print-path",
    ]
    date_value, time_value = _iso_date_time(now)
    if date_value:
        command.extend(["--date", date_value])
    if time_value:
        command.extend(["--time", time_value])
    if worklog_root:
        command.extend(["--root", str(worklog_root)])
    completed = subprocess.run(
        command,
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    if completed.returncode != 0:
        result = {
            "status": "BLOCKED",
            "path": None,
            "errors": [completed.stderr.strip() or "worklog update failed"],
        }
    else:
        path = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else None
        result = {"status": "WRITTEN", "path": path, "errors": []}
    updated_state = attach_worklog_result(
        state_root=state_root,
        task_id=task_id,
        result=result,
    )
    report["state"] = updated_state
    report["worklog"] = result
    if result["status"] != "WRITTEN":
        report["outcome"] = "PARTIAL"
        report.setdefault("errors", []).extend(result.get("errors", []))
    return report


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--memory-root", type=Path, default=None)
    parser.add_argument("--now", default=None, help="ISO-8601 time for deterministic operation")
    parser.add_argument("--profile", choices=["LIGHTWEIGHT", "NORMAL", "COUPLED"])
    parser.add_argument("--output", type=Path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start or refresh a continuity session")
    start.add_argument("--input-json", type=Path, required=True)
    _common(start)

    resume = subparsers.add_parser("resume", help="resume an exact continuity session")
    resume.add_argument("--input-json", type=Path, required=True)
    _common(resume)

    auto = subparsers.add_parser("auto", help="route one input to lookup-only or start/resume")
    auto.add_argument("--input-json", type=Path, required=True)
    _common(auto)

    measure = subparsers.add_parser("measure", help="measure bounded context without writing state")
    measure.add_argument("--input-json", type=Path, required=True)
    measure.add_argument("--memory-root", type=Path, default=None)
    measure.add_argument("--now", default=None)
    measure.add_argument("--profile", choices=["LIGHTWEIGHT", "NORMAL", "COUPLED"])
    measure.add_argument("--output", type=Path)

    close = subparsers.add_parser("close", help="close a task and write learning/context references")
    close.add_argument("--task-id", required=True)
    close.add_argument("--closure-json", type=Path, required=True)
    close.add_argument("--state-root", type=Path, default=None)
    close.add_argument("--memory-root", type=Path, default=None)
    close.add_argument("--store", type=Path, default=None)
    close.add_argument("--worklog-root", type=Path, default=None)
    close.add_argument("--now", default=None)
    close.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command in {"start", "resume", "auto"}:
            packet = _read(args.input_json)
            if args.command == "auto":
                if "actionable" in packet and not isinstance(packet["actionable"], bool):
                    raise ValueError("actionable must be a boolean when supplied")
                if "requestType" not in packet and "actionable" not in packet:
                    raise ValueError("auto intake needs requestType or an explicit actionable decision")
                decision = classify_input(packet)
                request_type = str(packet.get("requestType") or "").upper()
                informational = request_type in {"QUESTION", "CONVERSATION", "STATUS"}
                substantive = request_type in {
                    "REVIEW", "INVESTIGATION", "IMPLEMENTATION", "MIGRATION", "SECURITY", "DEPLOYMENT",
                }
                lookup_only = packet.get("actionable") is False or (
                    packet.get("actionable") is not True
                    and (informational or (not substantive and decision["decision"] == "SKIP"))
                )
                if lookup_only:
                    lookup = lookup_memory_context(
                        packet,
                        memory_root=resolve_storage_paths(memory_root=args.memory_root).memory_root,
                        now=args.now,
                    )
                    result = {
                        "schemaVersion": "continuity-report-v1",
                        "outcome": "LOOKUP_ONLY",
                        "taskId": packet.get("taskId"),
                        "lookup": lookup,
                        "sourceRevalidationRequired": True,
                        "sourceOnly": True,
                        "executed": False,
                        "sideEffects": [],
                        "errors": lookup.get("errors", []),
                    }
                    _write_or_print(result, args.output)
                    return 0 if lookup["outcome"] not in {"invalid", "failed"} else 2
            result = start_or_resume(
                packet,
                action=args.command,
                state_root=args.state_root,
                memory_root=args.memory_root,
                now=args.now,
                profile=args.profile,
            )
            _write_or_print(result, args.output)
            return 0 if result["outcome"] in {"STARTED", "RESUMED"} else 1
        if args.command == "measure":
            result = measure_input_context(
                _read(args.input_json),
                memory_root=args.memory_root,
                now=args.now,
                profile=args.profile,
            )
            _write_or_print(result, args.output)
            return 0
        closure = _read(args.closure_json)
        result = close_continuity(
            {"taskId": args.task_id},
            closure,
            state_root=args.state_root,
            memory_root=args.memory_root,
            store_path=args.store,
            now=args.now,
        )
        result = _run_worklog(
            result,
            closure,
            state_root=args.state_root or default_state_root(),
            worklog_root=args.worklog_root,
            now=args.now,
        )
        _write_or_print(result, args.output)
        return 0 if result["outcome"] == "CLOSED" else 2
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        result = {
            "schemaVersion": "continuity-report-v1",
            "outcome": "BLOCKED",
            "errors": [f"{type(error).__name__}: {error}"],
            "sourceOnly": True,
            "executed": False,
            "sideEffects": [],
        }
        _write_or_print(result, getattr(args, "output", None))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
