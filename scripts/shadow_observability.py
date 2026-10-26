#!/usr/bin/env python3
"""Build a sanitized, fail-closed operator status from a shadow report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "operator-status-v1"

# These are intentionally the stable values documented by the standalone
# operations and Phase 5 contracts.  Do not infer a runtime/device/activation
# success value from a passing static projection.
STATUS_VALUES = {
    "PASS",
    "FAIL",
    "BLOCKED",
    "MISSING_COVERAGE",
    "UNVERIFIED",
    "NOT_REQUIRED",
    "NOT_STARTED",
    "UNAVAILABLE",
    "SOURCE_ONLY",
    "INSTALLED_NOT_ACTIVE",
    "ACTIVE",
}
ROUTES = {"DIRECT", "REVIEW_ONLY", "PLAN_ONLY", "DIRECT_WORK", "NEW_TASK", "BLOCKED"}
COMPLEXITIES = {"LIGHTWEIGHT", "NORMAL", "COUPLED"}
RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
CANDIDATE_STATUSES = STATUS_VALUES | {
    "AVAILABLE",
    "CANDIDATE_UNAVAILABLE",
    "INVALID",
    "CONFLICT",
}
SHADOW_STATUSES = STATUS_VALUES | {"AVAILABLE", "CANDIDATE_UNAVAILABLE", "INVALID", "CONFLICT"}

CORE_FIELDS = (
    "taskId",
    "route",
    "complexity",
    "riskTier",
    "authority",
    "fingerprint",
    "planVersion",
    "writer",
    "blockers",
    "nextAction",
)
STATUS_NAMES = (
    "staticStatus",
    "reviewStatus",
    "runtimeStatus",
    "deviceStatus",
    "publicationStatus",
    "activationStatus",
    "learningStatus",
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|token|secret|cookie|dsn|api[-_ ]?key)\s*[:=]\s*\S+"
)
_BEARER_VALUE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----", re.S)


class OperatorStatusError(ValueError):
    """Raised when an operator status cannot be safely constructed."""

    def __init__(self, errors: list[str]):
        self.errors = [_safe_text(error) for error in errors]
        super().__init__("operator status rejected: " + "; ".join(self.errors))


def _safe_text(value: Any) -> str:
    text = " ".join(str(value).split())
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    text = _BEARER_VALUE.sub("authorization: Bearer [REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", text
    )
    return text[:500]


def sanitize(value: Any) -> Any:
    """Recursively sanitize JSON-compatible operator evidence."""

    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): sanitize(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    return value


def _non_empty_string(value: Any, field: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"missing or invalid required field: {field}")
        return None
    return value.strip()


def _field(report: Mapping[str, Any], name: str, *aliases: str) -> tuple[Any, bool]:
    if name in report:
        return report[name], True
    for alias in aliases:
        if alias in report:
            return report[alias], True
    return None, False


def _result(report: Mapping[str, Any], errors: list[str]) -> Mapping[str, Any] | None:
    results = report.get("results")
    if results is None:
        return report
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
        errors.append("shadow report must contain exactly one result for an operator status")
        return None
    return results[0]


def _final_projection(report: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
    reconciliation = result.get("reconciliation")
    if isinstance(reconciliation, Mapping):
        final = reconciliation.get("final")
        if isinstance(final, Mapping):
            return final
    return report


def _status_value(
    report: Mapping[str, Any],
    name: str,
    result: Mapping[str, Any],
    *,
    default: str | None = None,
) -> tuple[Any, bool]:
    value, present = _field(report, name)
    if present:
        return value, True

    statuses = report.get("statuses")
    if isinstance(statuses, Mapping) and name.removesuffix("Status") in statuses:
        nested = statuses[name.removesuffix("Status")]
        if isinstance(nested, Mapping) and "state" in nested:
            return nested["state"], True
        return nested, True

    # Accept the audit report's explicit status naming as an input form.
    short_name = name.removesuffix("Status")
    for candidate in (f"{short_name}Status", short_name):
        value, present = _field(result, candidate)
        if present:
            return value.get("state") if isinstance(value, Mapping) else value, True

    if default is not None:
        return default, True
    return None, False


def _shadow_side_effects(report: Mapping[str, Any], result: Mapping[str, Any]) -> list[Any]:
    side_effects: list[Any] = []
    for value in (
        report.get("sideEffects"),
        result.get("sideEffects"),
        result.get("general", {}).get("sideEffects")
        if isinstance(result.get("general"), Mapping)
        else None,
        result.get("reconciliation", {}).get("sideEffects")
        if isinstance(result.get("reconciliation"), Mapping)
        else None,
    ):
        if isinstance(value, list):
            side_effects.extend(value)
        elif value not in (None, [], ""):
            side_effects.append(value)
    return side_effects


def _derived_candidate_status(report: Mapping[str, Any], result: Mapping[str, Any]) -> Any:
    value, present = _field(report, "candidateStatus")
    if present:
        return value
    general = result.get("general")
    if isinstance(general, Mapping) and "candidateStatus" in general:
        return general["candidateStatus"]
    comparison = result.get("comparison")
    if isinstance(comparison, Mapping) and comparison.get("status") in CANDIDATE_STATUSES:
        return comparison["status"]
    return None


def _derived_shadow_status(report: Mapping[str, Any], result: Mapping[str, Any]) -> Any:
    value, present = _field(report, "shadowStatus")
    if present:
        return value
    expectation = result.get("expectation")
    if isinstance(expectation, Mapping) and expectation.get("passed") is False:
        return "FAIL"
    if report.get("expectationsPassed") is False:
        return "FAIL"
    if isinstance(expectation, Mapping) and expectation.get("passed") is True:
        return "PASS"
    if report.get("expectationsPassed") is True:
        return "PASS"
    return "UNVERIFIED"


def validate_shadow_report(report: Any) -> dict[str, Any]:
    """Return deterministic validation evidence without exposing credentials."""

    errors: list[str] = []
    if not isinstance(report, Mapping):
        return {"valid": False, "errors": ["shadow report must be an object"]}

    result = _result(report, errors)
    if result is None:
        return {"valid": False, "errors": [_safe_text(error) for error in errors]}

    projection = _final_projection(report, result)
    values: dict[str, Any] = {}
    for field in CORE_FIELDS:
        aliases = ("sourceFingerprint",) if field == "fingerprint" else ()
        value, present = _field(report, field, *aliases)
        if field == "fingerprint" and not present:
            value, present = _field(result, "fingerprint", "sourceFingerprint")
        if field == "writer" and not present:
            value, present = _field(report, "writerOwner")
        if field in {"route", "complexity", "riskTier"} and not present:
            value, present = _field(projection, field)
        if field == "authority" and not present:
            value, present = _field(result, "authority")
        if not present:
            errors.append(f"missing required field: {field}")
        values[field] = value

    if values.get("route") not in ROUTES:
        errors.append("route has unsupported status value")
    if values.get("complexity") not in COMPLEXITIES:
        errors.append("complexity has unsupported status value")
    if values.get("riskTier") not in RISKS:
        errors.append("riskTier has unsupported status value")
    for field in ("taskId", "authority", "fingerprint", "planVersion", "nextAction"):
        if field in values and values[field] is not None:
            _non_empty_string(values[field], field, errors)
    if "writer" in values and values["writer"] is not None:
        _non_empty_string(values["writer"], "writer", errors)
    if not isinstance(values.get("blockers"), list):
        errors.append("blockers must be an array")

    for name in STATUS_NAMES:
        default = {
            "reviewStatus": "UNAVAILABLE",
            "runtimeStatus": "UNAVAILABLE",
            "deviceStatus": "UNAVAILABLE",
            "publicationStatus": "NOT_REQUIRED",
            "activationStatus": "NOT_STARTED",
            "learningStatus": "UNVERIFIED",
        }.get(name)
        value, present = _status_value(report, name, result, default=default)
        if not present:
            if name == "staticStatus":
                value = "PASS" if _derived_shadow_status(report, result) == "PASS" else "UNVERIFIED"
            else:
                errors.append(f"missing required status field: {name}")
        if value not in STATUS_VALUES:
            errors.append(f"{name} has unsupported status value")

    candidate_status = _derived_candidate_status(report, result)
    if candidate_status is None:
        errors.append("missing required field: candidateStatus")
    elif candidate_status not in CANDIDATE_STATUSES:
        errors.append("candidateStatus has unsupported status value")

    shadow_status = _derived_shadow_status(report, result)
    if shadow_status not in SHADOW_STATUSES:
        errors.append("shadowStatus has unsupported status value")

    side_effects = _shadow_side_effects(report, result)
    if side_effects:
        errors.append("shadow report contains side effects; operator status is rejected")

    return {
        "valid": not errors,
        "errors": [_safe_text(error) for error in errors],
        "sideEffects": sanitize(side_effects),
    }


def build_operator_status(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stable operator status, rejecting unsafe or incomplete input."""

    check = validate_shadow_report(report)
    if not check["valid"]:
        raise OperatorStatusError(list(check["errors"]))

    errors: list[str] = []
    result = _result(report, errors)
    assert result is not None
    projection = _final_projection(report, result)

    def required(name: str, *aliases: str) -> Any:
        value, present = _field(report, name, *aliases)
        if name == "fingerprint" and not present:
            value, present = _field(result, "fingerprint", "sourceFingerprint")
        if name == "writer" and not present:
            value, present = _field(report, "writerOwner")
        if name in {"route", "complexity", "riskTier"} and not present:
            value, present = _field(projection, name)
        if name == "authority" and not present:
            value, present = _field(result, "authority")
        return value

    shadow_status = _derived_shadow_status(report, result)
    status_values = {
        name: _status_value(
            report,
            name,
            result,
            default={
                "staticStatus": "PASS" if shadow_status == "PASS" else "UNVERIFIED",
                "reviewStatus": "UNAVAILABLE",
                "runtimeStatus": "UNAVAILABLE",
                "deviceStatus": "UNAVAILABLE",
                "publicationStatus": "NOT_REQUIRED",
                "activationStatus": "NOT_STARTED",
                "learningStatus": "UNVERIFIED",
            }.get(name),
        )[0]
        for name in STATUS_NAMES
    }

    blockers = [sanitize(item) for item in required("blockers")]
    if shadow_status != "PASS":
        blockers.append(f"shadow status is {shadow_status}")

    output = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "PASS" if shadow_status == "PASS" and status_values["staticStatus"] == "PASS" else "BLOCKED",
        "taskId": sanitize(required("taskId")),
        "route": required("route"),
        "complexity": required("complexity"),
        "riskTier": required("riskTier"),
        "authority": sanitize(required("authority")),
        "fingerprint": sanitize(required("fingerprint", "sourceFingerprint")),
        "planVersion": sanitize(required("planVersion")),
        "writer": sanitize(required("writer", "writerOwner")),
        **status_values,
        "blockers": blockers,
        "nextAction": sanitize(required("nextAction")),
        "candidateStatus": _derived_candidate_status(report, result),
        "shadowStatus": shadow_status,
        "sideEffects": [],
    }
    return sanitize(output)


# Descriptive aliases for callers that prefer the contract's wording.
operator_status_from_shadow = build_operator_status
build_status = build_operator_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        status = build_operator_status(report)
    except (OSError, json.JSONDecodeError, OperatorStatusError) as exc:
        print(json.dumps({"schemaVersion": SCHEMA_VERSION, "status": "BLOCKED", "error": _safe_text(exc)}))
        return 1

    rendered = json.dumps(status, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
