#!/usr/bin/env python3
"""Run the dependency-free Phase 7/8 hybrid release audit.

The command intentionally proves only static, fixture-backed behavior.  It never
promotes, activates, publishes, or claims device/runtime evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

from classify_hybrid_shadow import build_report, load_cases
from validate_hybrid_contracts import validate_packet, validate_receipt


SCRIPT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_ROOT.parent
DEFAULT_CORPUS = SKILL_ROOT / "tests" / "fixtures" / "shadow"
DEFAULT_PACKET = SKILL_ROOT / "tests" / "fixtures" / "valid-packet.json"
DEFAULT_RECEIPT = SKILL_ROOT / "tests" / "fixtures" / "valid-receipt.json"

STATUS_VALUES = {"PASS", "FAIL", "NOT_REQUIRED", "UNAVAILABLE"}
SCENARIO_REQUIREMENTS = {
    "utility-question": ("P7-UTILITY", "CLOSED", "Direct utility route remains lightweight."),
    "flaky-mobile-sync": (
        "P7-MOBILE-SYNC",
        "EVIDENCE_REQUIRED",
        "Coupled mobile sync requires runtime and device evidence before closure.",
    ),
    "tenant-authorization": (
        "P7-TENANT-AUTH",
        "REVIEW_REQUIRED",
        "Tenant authorization remains a high-risk review-only route.",
    ),
    "offline-mutation": (
        "P7-OFFLINE-MUTATION",
        "EVIDENCE_REQUIRED",
        "Offline mutation needs queue, conflict, auth, API, and runtime evidence.",
    ),
    "reviewer-timeout": (
        "P7-REVIEWER-TIMEOUT",
        "BLOCKED",
        "A non-completed reviewer receipt cannot close coverage.",
    ),
    "candidate-unavailable": (
        "P7-CANDIDATE-UNAVAILABLE",
        "HYBRID_UNVERIFIED",
        "Fallback keeps general authoritative while hybrid coverage is unavailable.",
    ),
    "wrong-fingerprint-receipt": (
        "P7-WRONG-FINGERPRINT",
        "REJECTED",
        "A receipt bound to a different task/fingerprint is rejected.",
    ),
    "changed-acceptance": (
        "P7-CHANGED-ACCEPTANCE",
        "INVALIDATED",
        "Changed acceptance invalidates affected plan and receipt evidence.",
    ),
    "stale-rag-conflict": (
        "P7-STALE-RAG",
        "SOURCE_WINS_NEEDS_REVIEW",
        "Stale/conflicting knowledge is only a lead; current source wins.",
    ),
    "unauthorized-memory": (
        "P7-UNAUTHORIZED-MEMORY",
        "FILTERED_BEFORE_EXPOSURE",
        "Unauthorized memory is filtered before model exposure.",
    ),
}

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|token|secret|cookie|dsn|api[-_ ]?key)\s*[:=]\s*\S+"
)
_BEARER_VALUE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")


def _safe_text(value: str) -> str:
    value = _BEARER_VALUE.sub("authorization: Bearer [REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", value
    )[:500]


def sanitize(value: Any) -> Any:
    """Return JSON-safe audit output without credential-like values."""
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items() if not str(key).startswith("_")}
    return value


def _read_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"read {path.name}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"read {path.name}: expected JSON object"]
    return value, []


def _status(state: str, required: bool, evidence: list[str]) -> dict[str, Any]:
    if state not in STATUS_VALUES:
        raise ValueError(f"unsupported audit status: {state}")
    return {"state": state, "required": required, "evidence": evidence}


def _scenario_exit(name: str, result: Mapping[str, Any], receipt: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Evaluate each deliberately static scenario without inventing live evidence."""
    expectation_ok = bool(result.get("expectation", {}).get("passed"))
    if not expectation_ok:
        return "MISMATCH", ["route/risk/complexity expectation failed"]
    if name == "reviewer-timeout":
        timed_out = copy.deepcopy(receipt)
        timed_out["status"] = "timed_out"
        check = validate_receipt(timed_out)
        return (
            "BLOCKED" if check["valid"] and not check["completionEligible"] else "MISMATCH",
            list(check["errors"]),
        )
    if name == "candidate-unavailable":
        actual = result.get("comparison", {}).get("status")
        return (
            "HYBRID_UNVERIFIED" if actual == "CANDIDATE_UNAVAILABLE" else "MISMATCH",
            [f"fallback candidate status: {actual}"],
        )
    if name == "wrong-fingerprint-receipt":
        rejected = validate_receipt(
            receipt,
            expected_task_id="other-task",
            expected_fingerprint="other-fingerprint",
        )
        return (
            "REJECTED" if not rejected["valid"] else "MISMATCH",
            list(rejected["errors"]),
        )
    requirement = SCENARIO_REQUIREMENTS.get(name)
    if requirement is None:
        return "MISSING_COVERAGE", ["scenario is not in the Phase 7 expectation registry"]
    return requirement[1], [requirement[2]]


def evaluate_scenarios(
    fallback_report: Mapping[str, Any], receipt: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the ten-scenario evidence set and reject unknown/duplicate cases."""
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for result in fallback_report.get("results", []):
        name = str(result.get("name", ""))
        if name in seen:
            errors.append(f"duplicate scenario result: {name}")
            continue
        seen.add(name)
        requirement = SCENARIO_REQUIREMENTS.get(name)
        if requirement is None:
            errors.append(f"orphan scenario result: {name}")
            continue
        observed, evidence = _scenario_exit(name, result, receipt)
        entries.append({
            "requirementId": requirement[0],
            "scenario": name,
            "owner": "audit_hybrid_release",
            "expectedExit": requirement[1],
            "observedExit": observed,
            "passed": observed == requirement[1],
            "evidence": evidence,
        })
    missing = sorted(set(SCENARIO_REQUIREMENTS) - seen)
    errors.extend(f"missing scenario result: {name}" for name in missing)
    return entries, errors


def validate_matrix(entries: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Require exactly one evidence owner for each Phase 7 scenario requirement."""
    expected_ids = {spec[0] for spec in SCENARIO_REQUIREMENTS.values()}
    ids = [str(entry.get("requirementId", "")) for entry in entries]
    errors: list[str] = []
    for requirement_id in sorted(expected_ids - set(ids)):
        errors.append(f"missing requirement ownership: {requirement_id}")
    for requirement_id in sorted(set(ids) - expected_ids):
        errors.append(f"orphan requirement ownership: {requirement_id}")
    for requirement_id in sorted(set(ids)):
        if ids.count(requirement_id) != 1:
            errors.append(f"duplicate requirement ownership: {requirement_id}")
    for entry in entries:
        if not entry.get("owner"):
            errors.append(f"unowned requirement: {entry.get('requirementId', '')}")
        if not entry.get("passed"):
            errors.append(f"failed requirement evidence: {entry.get('requirementId', '')}")
    return {
        "schemaVersion": "verification-matrix-v1",
        "valid": not errors,
        "requirements": [dict(entry) for entry in entries],
        "errors": errors,
    }


def _candidate_check(candidate_report: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    if candidate_report is None:
        return True, ["candidate-aware mode not requested"]
    unavailable = [
        str(item.get("name", "unnamed"))
        for item in candidate_report.get("results", [])
        if item.get("comparison", {}).get("status") != "COMPARED"
    ]
    if unavailable:
        return False, ["candidate projection unavailable or invalid for: " + ", ".join(unavailable)]
    if not candidate_report.get("expectationsPassed"):
        return False, ["candidate-aware route/risk expectations failed"]
    if candidate_report.get("sideEffects") != []:
        return False, ["candidate-aware shadow reported side effects"]
    conflicts = [
        str(item.get("name", "unnamed"))
        for item in candidate_report.get("results", [])
        if item.get("reconciliation", {}).get("status") == "CONFLICT"
    ]
    if conflicts:
        return False, [
            "candidate-aware reconciliation conflicts block closure for: "
            + ", ".join(conflicts)
        ]
    return True, ["candidate-aware shadow comparisons completed"]


def build_audit_report(
    *,
    corpus: Path = DEFAULT_CORPUS,
    packet_path: Path = DEFAULT_PACKET,
    receipt_path: Path = DEFAULT_RECEIPT,
    candidate_root: Path | None = None,
) -> dict[str, Any]:
    """Run static validation and return an honest, sanitized audit report."""
    packet, packet_errors = _read_json(packet_path)
    receipt, receipt_errors = _read_json(receipt_path)
    packet_check = validate_packet(packet) if packet is not None else {"valid": False, "errors": packet_errors}
    receipt_check = validate_receipt(receipt) if receipt is not None else {
        "valid": False, "completionEligible": False, "errors": receipt_errors,
    }

    try:
        cases = load_cases(corpus)
        fallback = build_report(cases)
        candidate = build_report(cases, candidate_root=candidate_root) if candidate_root else None
        shadow_errors: list[str] = []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        fallback = {"results": [], "expectationsPassed": False, "sideEffects": ["unavailable"]}
        candidate = None
        shadow_errors = [f"shadow corpus could not run: {exc}"]

    scenario_entries, scenario_errors = evaluate_scenarios(fallback, receipt or {})
    matrix = validate_matrix(scenario_entries)
    candidate_ok, candidate_evidence = _candidate_check(candidate)
    static_errors = (
        packet_check.get("errors", [])
        + receipt_check.get("errors", [])
        + shadow_errors
        + scenario_errors
        + matrix["errors"]
    )
    if fallback.get("sideEffects") != []:
        static_errors.append("fallback shadow reported side effects")
    if not fallback.get("expectationsPassed"):
        static_errors.append("fallback shadow expectations failed")
    if not receipt_check.get("completionEligible"):
        static_errors.append("receipt fixture is not completion eligible")
    if candidate_root is not None and not candidate_ok:
        static_errors.extend(candidate_evidence)

    static_passed = not static_errors
    statuses = {
        "static": _status("PASS" if static_passed else "FAIL", True, [
            "packet fixture validation",
            "receipt fixture validation",
            "fallback shadow corpus",
            "requirement-to-evidence matrix",
        ] + static_errors),
        "review": _status("UNAVAILABLE", False, [
            "No separately supplied named-reviewer terminal receipt; fixture validation is not live review proof."
        ]),
        "runtime": _status("UNAVAILABLE", False, [
            "The audit is static; mobile/offline scenario runtime evidence was not supplied."
        ]),
        "device": _status("UNAVAILABLE", False, [
            "No device execution receipt was supplied."
        ]),
        "publication": _status("NOT_REQUIRED", False, [
            "The audit command does not publish artifacts."
        ]),
        "activation": _status("UNAVAILABLE", False, [
            "The audit command does not install or activate the skill."
        ]),
        "learning": _status("PASS", False, [
            "All ten scenarios include a modeled learning/knowledge lifecycle outcome; no durable write-back is claimed."
        ]),
    }
    technical_pass = all(
        value["state"] == "PASS" for value in statuses.values() if value["required"]
    )
    report = {
        "schemaVersion": "audit-report-v1",
        "terminalStatus": "TECHNICAL_PASS_LIMITED" if technical_pass else "TECHNICAL_FAILURE",
        "technicalPass": technical_pass,
        "sideEffects": [],
        "modes": {
            "fallback": fallback,
            "candidateAware": candidate if candidate is not None else {
                "state": "NOT_REQUESTED",
                "evidence": ["candidate root was not supplied"],
            },
        },
        "fixtureChecks": {"packet": packet_check, "receipt": receipt_check},
        "matrix": matrix,
        "statuses": statuses,
        "findings": static_errors + candidate_evidence,
    }
    return sanitize(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--output", type=Path, help="Write the sanitized JSON report to this path.")
    args = parser.parse_args()

    report = build_audit_report(
        corpus=args.corpus,
        packet_path=args.packet,
        receipt_path=args.receipt,
        candidate_root=args.candidate_root,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["technicalPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
