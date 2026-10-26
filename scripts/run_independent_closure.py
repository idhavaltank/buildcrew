#!/usr/bin/env python3
"""Run an independent, fail-closed closure review for Phases 1--3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

from candidate_projection import resolve_candidate_root, source_fingerprint  # noqa: E402
from classify_hybrid_shadow import build_report, load_cases  # noqa: E402
from validate_hybrid_contracts import validate_json_file  # noqa: E402


def package_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in {".pyc", ".pyo"}:
            continue
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def run_tests(root: Path, candidate_root: Path | None) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["general_CLOSURE_CHILD"] = "1"
    if candidate_root is not None:
        environment["general_CANDIDATE_ROOT"] = str(candidate_root)
    else:
        environment.pop("general_CANDIDATE_ROOT", None)
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    rendered = (completed.stdout + completed.stderr).strip().splitlines()
    return {
        "command": "python3 -m unittest discover -s tests -p 'test_*.py'",
        "exitCode": completed.returncode,
        "summary": rendered[-1] if rendered else "no test output",
        "passed": completed.returncode == 0,
    }


def contract_checks(root: Path) -> list[dict[str, Any]]:
    checks = []
    valid_packet = root / "tests" / "fixtures" / "valid-packet.json"
    valid_receipt = root / "tests" / "fixtures" / "valid-receipt.json"
    for path, requires_completion in (
        (valid_packet, False),
        (valid_receipt, True),
    ):
        result = validate_json_file(path)
        passed = bool(result.get("valid")) and (
            not requires_completion or bool(result.get("completionEligible"))
        )
        checks.append({
            "name": path.name,
            "passed": passed,
            "valid": result.get("valid", False),
            "completionEligible": result.get("completionEligible", True),
            "errors": result.get("errors", []),
        })

    invalid_root = root / "tests" / "fixtures" / "contracts"
    for path in sorted(invalid_root.glob("invalid-*.json")):
        result = validate_json_file(path)
        checks.append({
            "name": path.name,
            "passed": not bool(result.get("valid")),
            "valid": result.get("valid", False),
            "errors": result.get("errors", []),
        })
    return checks


def shadow_check(root: Path, candidate_root: Path | None) -> dict[str, Any]:
    corpus = root / "tests" / "fixtures" / "shadow"
    report = build_report(load_cases(corpus), candidate_root=candidate_root)
    comparison_statuses = {item["comparison"]["status"] for item in report["results"]}
    if candidate_root is None:
        compared = comparison_statuses == {"CANDIDATE_UNAVAILABLE"}
        coverage_status = "MISSING_COVERAGE"
    else:
        compared = comparison_statuses == {"COMPARED"}
        coverage_status = "PASS" if compared else "MISSING_COVERAGE"
    return {
        "caseCount": report["caseCount"],
        "expectationsPassed": report["expectationsPassed"],
        "comparisonStatuses": sorted(comparison_statuses),
        "sideEffects": report["sideEffects"],
        "candidateCoverage": coverage_status,
        "technicalPassed": report["expectationsPassed"] and not report["sideEffects"],
        "passed": (
            report["expectationsPassed"]
            and not report["sideEffects"]
            and compared
            and candidate_root is not None
        ),
    }


def phase_result(name: str, checks: list[dict[str, Any]], *, status: str | None = None) -> dict[str, Any]:
    passed = all(bool(check.get("passed")) for check in checks)
    return {
        "name": name,
        "status": status or ("PASS" if passed else "MISSING_COVERAGE"),
        "passed": passed and (status in {None, "PASS"}),
        "checks": checks,
    }


def build_closure(root: Path, candidate_root: Path | None, reviewer: str) -> dict[str, Any]:
    tests = run_tests(root, candidate_root)
    contracts = contract_checks(root)
    shadow = shadow_check(root, candidate_root)
    phase_one = phase_result(
        "Phase 1",
        [{"name": "package-tests", "passed": tests["passed"]},
         {"name": "packet-and-fixture-contracts", "passed": all(item["passed"] for item in contracts)}],
    )
    phase_two = phase_result(
        "Phase 2",
        [{"name": "shadow-corpus", "passed": shadow["passed"]}],
        status="PASS" if shadow["passed"] else "MISSING_COVERAGE",
    )
    phase_three = phase_result(
        "Phase 3",
        [{"name": "receipt-and-state-tests", "passed": tests["passed"]},
         {"name": "completion-receipt", "passed": next(
             (item["passed"] for item in contracts if item["name"] == "valid-receipt.json"),
             False,
         )}],
    )
    phases = {"1": phase_one, "2": phase_two, "3": phase_three}
    resolved_candidate = (
        resolve_candidate_root(candidate_root) if candidate_root is not None else None
    )
    return {
        "schemaVersion": "independent-closure-v1",
        "reviewer": reviewer,
        "sourceFingerprint": package_fingerprint(root),
        "candidateFingerprint": (
            source_fingerprint(resolved_candidate) if resolved_candidate is not None else None
        ),
        "candidateSupplied": candidate_root is not None,
        "phases": phases,
        "closureEligible": all(item["passed"] for item in phases.values()),
        "runtimeStatus": "UNAVAILABLE",
        "deviceStatus": "NOT_REQUIRED",
        "activationStatus": "NOT_REQUIRED",
        "learningStatus": "RECORDED_AS_REVIEW_ARTIFACT",
        "sideEffects": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    candidate_root = args.candidate_root.resolve() if args.candidate_root else None
    if not root.is_dir():
        parser.error(f"package root does not exist: {root}")
    if candidate_root is not None and not candidate_root.is_dir():
        report = {
            "schemaVersion": "independent-closure-v1",
            "reviewer": args.reviewer,
            "sourceFingerprint": package_fingerprint(root),
            "candidateFingerprint": None,
            "candidateSupplied": False,
            "phases": {
                "1": {"name": "Phase 1", "status": "PASS", "passed": True, "checks": []},
                "2": {"name": "Phase 2", "status": "MISSING_COVERAGE", "passed": False,
                      "checks": [{"name": "candidate-source", "passed": False}]},
                "3": {"name": "Phase 3", "status": "MISSING_COVERAGE", "passed": False, "checks": []},
            },
            "closureEligible": False,
            "runtimeStatus": "UNAVAILABLE",
            "deviceStatus": "NOT_REQUIRED",
            "activationStatus": "NOT_REQUIRED",
            "learningStatus": "BLOCKED",
            "sideEffects": [],
            "blocker": "candidate root is unavailable",
        }
    else:
        report = build_closure(root, candidate_root, args.reviewer)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["closureEligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
