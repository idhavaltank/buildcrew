#!/usr/bin/env python3
"""Dependency-free validation for the hybrid intake and receipt contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RECEIPT_STATUSES = {
    "completed",
    "failed",
    "timed_out",
    "interrupted",
    "crashed",
    "shutdown",
}

PACKET_REQUIRED = {
    "schemaVersion",
    "taskId",
    "request",
    "scope",
    "behavior",
    "evidence",
    "candidateProjection",
    "reconciliation",
    "execution",
    "verification",
    "learning",
    "state",
}

RECEIPT_REQUIRED = {
    "receiptVersion",
    "receiptId",
    "taskId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "role",
    "host",
    "writerOwner",
    "scope",
    "startedAt",
    "finishedAt",
    "status",
    "confidence",
    "coverage",
    "findings",
    "commands",
    "artifacts",
    "blockers",
    "residualRisk",
    "terminal",
}

REQUEST_FIELDS = {
    "rawText",
    "normalizedObjective",
    "requestType",
    "constraints",
    "acceptanceCriteria",
}
SCOPE_FIELDS = {"project", "repository", "repositoryRef", "domain", "sourceFingerprint"}
BEHAVIOR_FIELDS = {"current", "expected", "reproduction", "userImpact"}
BEHAVIOR_REQUIRED = {"current", "expected", "reproduction", "userImpact"}
CANDIDATE_REQUIRED = {
    "schemaVersion",
    "sourceFingerprint",
    "route",
    "complexity",
    "riskTier",
    "riskReasons",
    "knowledgeScope",
    "requiredCapabilities",
    "requiredCoverage",
    "status",
}
CANDIDATE_STATUSES = {"AVAILABLE", "CANDIDATE_UNAVAILABLE", "INVALID"}
CANDIDATE_COMPLEXITIES = {"LIGHTWEIGHT", "NORMAL", "COUPLED"}
CANDIDATE_RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
CANDIDATE_KNOWLEDGE_SCOPES = {
    "NONE",
    "WORKFLOW_ONLY",
    "TARGETED_REPOSITORY",
    "CROSS_REPOSITORY",
    "EXTERNAL",
}


def _errors_for_keys(value: dict[str, Any], required: set[str], label: str) -> list[str]:
    missing = sorted(required - set(value))
    return [f"{label}: missing required field {key}" for key in missing]


def _errors_for_unknown_keys(value: dict[str, Any], allowed: set[str], label: str) -> list[str]:
    unknown = sorted(set(value) - allowed)
    return [f"{label}: unknown field {key}" for key in unknown]


def _require_dict(value: Any, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected object")
        return None
    return value


def _require_string(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _require_string_or_null(value: Any, label: str, errors: list[str]) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        errors.append(f"{label}: expected non-empty string or null")


def _require_list(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")


def validate_packet(packet: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(packet, dict):
        return {"valid": False, "errors": ["packet: expected object"]}

    errors.extend(_errors_for_keys(packet, PACKET_REQUIRED, "packet"))
    errors.extend(_errors_for_unknown_keys(packet, PACKET_REQUIRED, "packet"))
    if packet.get("schemaVersion") != "hybrid-intake-v1":
        errors.append("packet.schemaVersion: expected hybrid-intake-v1")
    _require_string(packet.get("taskId"), "packet.taskId", errors)

    for key in ("request", "scope", "behavior", "evidence", "reconciliation",
                "execution", "verification", "learning", "state"):
        if not isinstance(packet.get(key), dict):
            errors.append(f"packet.{key}: expected object")

    request = packet.get("request")
    if isinstance(request, dict):
        errors.extend(_errors_for_unknown_keys(request, REQUEST_FIELDS, "packet.request"))
        for key in ("rawText", "normalizedObjective", "requestType"):
            _require_string(request.get(key), f"packet.request.{key}", errors)
        for key in ("constraints", "acceptanceCriteria"):
            _require_list(request.get(key), f"packet.request.{key}", errors)

    scope = packet.get("scope")
    if isinstance(scope, dict):
        errors.extend(_errors_for_unknown_keys(scope, SCOPE_FIELDS, "packet.scope"))
        for key in ("repository", "repositoryRef", "domain", "sourceFingerprint"):
            _require_string(scope.get(key), f"packet.scope.{key}", errors)

    behavior = packet.get("behavior")
    if isinstance(behavior, dict):
        errors.extend(_errors_for_unknown_keys(behavior, BEHAVIOR_FIELDS, "packet.behavior"))
        errors.extend(_errors_for_keys(behavior, BEHAVIOR_REQUIRED, "packet.behavior"))
        for key in sorted(BEHAVIOR_REQUIRED):
            _require_string_or_null(
                behavior.get(key),
                f"packet.behavior.{key}",
                errors,
            )

    candidate = packet.get("candidateProjection")
    if candidate is not None and not isinstance(candidate, dict):
        errors.append("packet.candidateProjection: expected object or null")
    elif isinstance(candidate, dict):
        errors.extend(_errors_for_keys(candidate, CANDIDATE_REQUIRED, "candidateProjection"))
        errors.extend(_errors_for_unknown_keys(candidate, CANDIDATE_REQUIRED, "candidateProjection"))
        if candidate.get("schemaVersion") != "candidate-projection-v1":
            errors.append("candidateProjection.schemaVersion: expected candidate-projection-v1")
        _require_string(candidate.get("sourceFingerprint"), "candidateProjection.sourceFingerprint", errors)
        route = candidate.get("route")
        if route is not None:
            _require_string(route, "candidateProjection.route", errors)
        if candidate.get("complexity") not in CANDIDATE_COMPLEXITIES:
            errors.append("candidateProjection.complexity: unsupported value")
        if candidate.get("riskTier") not in CANDIDATE_RISKS:
            errors.append("candidateProjection.riskTier: unsupported value")
        if candidate.get("knowledgeScope") not in CANDIDATE_KNOWLEDGE_SCOPES:
            errors.append("candidateProjection.knowledgeScope: unsupported value")
        if candidate.get("status") not in CANDIDATE_STATUSES:
            errors.append("candidateProjection.status: unsupported value")
        for key in ("riskReasons", "requiredCapabilities", "requiredCoverage"):
            _require_list(candidate.get(key), f"candidateProjection.{key}", errors)

    return {"valid": not errors, "errors": errors}


def validate_receipt(
    receipt: Any,
    *,
    expected_task_id: str | None = None,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return {
            "valid": False,
            "completionEligible": False,
            "errors": ["receipt: expected object"],
        }

    errors.extend(_errors_for_keys(receipt, RECEIPT_REQUIRED, "receipt"))
    errors.extend(_errors_for_unknown_keys(receipt, RECEIPT_REQUIRED, "receipt"))
    if receipt.get("receiptVersion") != "review-receipt-v2":
        errors.append("receipt.receiptVersion: expected review-receipt-v2")

    for key in (
        "receiptId",
        "taskId",
        "packetVersion",
        "planVersion",
        "waveId",
        "role",
        "host",
        "startedAt",
        "finishedAt",
    ):
        _require_string(receipt.get(key), f"receipt.{key}", errors)

    attempt = receipt.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        errors.append("receipt.attempt: expected positive integer")

    writer_owner = receipt.get("writerOwner")
    if writer_owner is not None and (
        not isinstance(writer_owner, str) or not writer_owner.strip()
    ):
        errors.append("receipt.writerOwner: expected non-empty string or null")

    scope = receipt.get("scope")
    if isinstance(scope, dict):
        errors.extend(_errors_for_unknown_keys(
            scope,
            {"repository", "sourceFingerprint", "lane"},
            "receipt.scope",
        ))
        for key in ("repository", "sourceFingerprint", "lane"):
            _require_string(scope.get(key), f"receipt.scope.{key}", errors)
    else:
        errors.append("receipt.scope: expected object")

    status = receipt.get("status")
    if status not in RECEIPT_STATUSES:
        errors.append(f"receipt.status: unsupported value {status!r}")

    if receipt.get("confidence") not in {"low", "medium", "high"}:
        errors.append("receipt.confidence: expected low, medium, or high")

    coverage = receipt.get("coverage")
    if isinstance(coverage, dict):
        errors.extend(_errors_for_unknown_keys(
            coverage,
            {"requirements", "files", "flows"},
            "receipt.coverage",
        ))
        for key in ("requirements", "files", "flows"):
            _require_list(coverage.get(key), f"receipt.coverage.{key}", errors)
    else:
        errors.append("receipt.coverage: expected object")

    for key in ("findings", "commands", "artifacts", "blockers", "residualRisk"):
        _require_list(receipt.get(key), f"receipt.{key}", errors)

    if receipt.get("terminal") is not True:
        errors.append("receipt.terminal: must be true for a terminal receipt")

    if expected_task_id and receipt.get("taskId") != expected_task_id:
        errors.append("receipt.taskId: does not match expected task")
    if expected_fingerprint:
        actual = scope.get("sourceFingerprint") if isinstance(scope, dict) else None
        if actual != expected_fingerprint:
            errors.append("receipt.scope.sourceFingerprint: does not match expected fingerprint")

    valid = not errors
    completion_eligible = valid and status == "completed"
    return {
        "valid": valid,
        "completionEligible": completion_eligible,
        "errors": errors,
        "status": status,
    }


def validate_json_file(path: Path, *, expected_task_id: str | None = None,
                       expected_fingerprint: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": str(path), "valid": False, "errors": [f"read: {exc}"]}

    schema_version = value.get("schemaVersion") if isinstance(value, dict) else None
    receipt_version = value.get("receiptVersion") if isinstance(value, dict) else None
    if schema_version == "hybrid-intake-v1":
        result = validate_packet(value)
    elif receipt_version == "review-receipt-v2":
        result = validate_receipt(
            value,
            expected_task_id=expected_task_id,
            expected_fingerprint=expected_fingerprint,
        )
    else:
        result = {
            "valid": False,
            "errors": ["file: unknown contract version"],
        }
    result["path"] = str(path)
    return result


def _iter_json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Validate one packet or receipt JSON file")
    parser.add_argument("--fixtures", type=Path, help="Validate all JSON files below a directory")
    parser.add_argument("--expected-task-id")
    parser.add_argument("--expected-fingerprint")
    parser.add_argument(
        "--require-completion",
        action="store_true",
        help="Return failure unless every receipt is eligible for completion",
    )
    args = parser.parse_args()

    target = args.file or args.fixtures
    if target is None:
        parser.error("provide --file or --fixtures")

    paths = _iter_json_files(target)
    if not paths:
        print(json.dumps({"valid": False, "errors": ["no JSON files found"]}, indent=2))
        return 1

    results = [
        validate_json_file(
            path,
            expected_task_id=args.expected_task_id,
            expected_fingerprint=args.expected_fingerprint,
        )
        for path in paths
    ]
    output = {
        "valid": all(result.get("valid") for result in results),
        "completionEligible": all(
            result.get("completionEligible", True) for result in results
        ),
        "results": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    passed = output["valid"] and (
        not args.require_completion or output["completionEligible"]
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
