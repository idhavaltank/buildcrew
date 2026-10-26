"""Strict, task-bound contract for repository-first reuse evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


SCHEMA_VERSION = "repository-reuse-assessment-v1"
DECISIONS = {"REUSE", "EXTEND", "NEW", "DOCUMENTED_EXCEPTION"}
FITS = {"compatible", "extendable", "incompatible"}
FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    text = _text(value)
    if not text:
        errors.append(f"{label}: expected non-empty UTC timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: invalid timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label}: timezone is required")
        return None
    return parsed.astimezone(timezone.utc)


def _string_list(value: Any, label: str, errors: list[str], *, nonempty: bool = False) -> list[str] | None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected string array")
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        errors.append(f"{label}: expected non-empty strings")
        return None
    if nonempty and not value:
        errors.append(f"{label}: at least one item is required")
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    return value


def _repository_path(value: Any, label: str, errors: list[str]) -> None:
    text = _text(value)
    if not text:
        errors.append(f"{label}: expected repository-relative path")
        return
    path = PurePosixPath(text.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"{label}: expected repository-relative path")


def validate_reuse_assessment(
    value: Any,
    *,
    expected_task_id: str | None = None,
    expected_repository_id: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_task_scope_fingerprint: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate structure, decision evidence, freshness, and identity bindings."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["reuseAssessment: expected object"]}

    required = {
        "schemaVersion",
        "taskId",
        "repositoryId",
        "sourceFingerprint",
        "taskScopeFingerprint",
        "targetPaths",
        "searchTerms",
        "candidates",
        "representativeEvidence",
        "decision",
        "chosenBoundary",
        "rationale",
        "negativeSearchEvidence",
        "exceptionReason",
        "terminalStatus",
        "issuedAt",
        "expiresAt",
        "sideEffects",
    }
    errors.extend(f"reuseAssessment: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"reuseAssessment: unknown field {key}" for key in sorted(set(value) - required))

    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"reuseAssessment.schemaVersion: expected {SCHEMA_VERSION}")
    for key in ("taskId", "repositoryId", "rationale"):
        if not _text(value.get(key)):
            errors.append(f"reuseAssessment.{key}: expected non-empty string")
    for key in ("sourceFingerprint", "taskScopeFingerprint"):
        fingerprint = _text(value.get(key))
        if not FINGERPRINT.fullmatch(fingerprint):
            errors.append(f"reuseAssessment.{key}: expected lowercase sha256")

    target_paths = _string_list(value.get("targetPaths"), "reuseAssessment.targetPaths", errors, nonempty=True)
    if target_paths is not None:
        for index, path in enumerate(target_paths):
            _repository_path(path, f"reuseAssessment.targetPaths[{index}]", errors)
    _string_list(value.get("searchTerms"), "reuseAssessment.searchTerms", errors, nonempty=True)
    representative = _string_list(
        value.get("representativeEvidence"),
        "reuseAssessment.representativeEvidence",
        errors,
    )
    negative = _string_list(
        value.get("negativeSearchEvidence"),
        "reuseAssessment.negativeSearchEvidence",
        errors,
    )

    candidates = value.get("candidates")
    validated_candidates: list[Mapping[str, Any]] = []
    if not isinstance(candidates, list):
        errors.append("reuseAssessment.candidates: expected array")
    else:
        candidate_fields = {"path", "symbol", "publicBoundary", "evidence", "fit"}
        for index, candidate in enumerate(candidates):
            label = f"reuseAssessment.candidates[{index}]"
            if not isinstance(candidate, Mapping):
                errors.append(f"{label}: expected object")
                continue
            errors.extend(f"{label}: missing required field {key}" for key in sorted(candidate_fields - set(candidate)))
            errors.extend(f"{label}: unknown field {key}" for key in sorted(set(candidate) - candidate_fields))
            _repository_path(candidate.get("path"), f"{label}.path", errors)
            for key in ("symbol", "publicBoundary"):
                if candidate.get(key) is not None and not _text(candidate.get(key)):
                    errors.append(f"{label}.{key}: expected non-empty string or null")
            _string_list(candidate.get("evidence"), f"{label}.evidence", errors, nonempty=True)
            if candidate.get("fit") not in FITS:
                errors.append(f"{label}.fit: unsupported value")
            validated_candidates.append(candidate)

    decision = value.get("decision")
    if decision not in DECISIONS:
        errors.append("reuseAssessment.decision: unsupported value")
    chosen_boundary = value.get("chosenBoundary")
    if chosen_boundary is not None and not _text(chosen_boundary):
        errors.append("reuseAssessment.chosenBoundary: expected non-empty string or null")
    exception_reason = value.get("exceptionReason")
    if exception_reason is not None and not _text(exception_reason):
        errors.append("reuseAssessment.exceptionReason: expected non-empty string or null")

    if decision == "REUSE":
        if not any(candidate.get("fit") == "compatible" for candidate in validated_candidates):
            errors.append("reuseAssessment.candidates: REUSE requires a compatible candidate")
        if not _text(chosen_boundary):
            errors.append("reuseAssessment.chosenBoundary: REUSE requires the selected boundary")
        if representative is not None and not representative:
            errors.append("reuseAssessment.representativeEvidence: REUSE requires representative evidence")
    elif decision == "EXTEND":
        if not any(candidate.get("fit") in {"compatible", "extendable"} for candidate in validated_candidates):
            errors.append("reuseAssessment.candidates: EXTEND requires an extendable candidate")
        if not _text(chosen_boundary):
            errors.append("reuseAssessment.chosenBoundary: EXTEND requires the selected boundary")
        if representative is not None and not representative:
            errors.append("reuseAssessment.representativeEvidence: EXTEND requires representative evidence")
    elif decision == "NEW":
        if negative is not None and not negative:
            errors.append("reuseAssessment.negativeSearchEvidence: NEW requires bounded negative-search evidence")
        if not _text(chosen_boundary):
            errors.append("reuseAssessment.chosenBoundary: NEW requires the planned boundary")
    elif decision == "DOCUMENTED_EXCEPTION":
        if not _text(exception_reason):
            errors.append("reuseAssessment.exceptionReason: DOCUMENTED_EXCEPTION requires a reason")
        if not validated_candidates and negative is not None and not negative:
            errors.append("reuseAssessment: DOCUMENTED_EXCEPTION requires a candidate or negative-search evidence")

    if value.get("terminalStatus") != "COMPLETED":
        errors.append("reuseAssessment.terminalStatus: expected COMPLETED")
    if value.get("sideEffects") != []:
        errors.append("reuseAssessment.sideEffects: must be empty")

    issued = _timestamp(value.get("issuedAt"), "reuseAssessment.issuedAt", errors)
    expires = _timestamp(value.get("expiresAt"), "reuseAssessment.expiresAt", errors)
    validation_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued is not None and expires is not None:
        if expires <= issued:
            errors.append("reuseAssessment.expiresAt: must be after issuedAt")
        if validation_time >= expires:
            errors.append("reuseAssessment.expiresAt: assessment is stale")
        if issued > validation_time:
            errors.append("reuseAssessment.issuedAt: cannot be in the future")

    bindings = {
        "taskId": expected_task_id,
        "repositoryId": expected_repository_id,
        "sourceFingerprint": expected_source_fingerprint,
        "taskScopeFingerprint": expected_task_scope_fingerprint,
    }
    for key, expected in bindings.items():
        if expected is not None and value.get(key) != expected:
            errors.append(f"reuseAssessment.{key}: does not match current task binding")

    return {"valid": not errors, "errors": errors}


__all__ = ["DECISIONS", "SCHEMA_VERSION", "validate_reuse_assessment"]
