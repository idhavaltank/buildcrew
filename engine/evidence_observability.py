"""Dependency-free evidence and observability contracts.

This layer normalizes and validates status evidence. It deliberately does not
publish to a queue, dashboard, telemetry service, or live host.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Iterable, Mapping, Sequence

from .safety_boundaries import redact_value


EVENT_SCHEMA_VERSION = "evidence-event-v1"
MATRIX_SCHEMA_VERSION = "evidence-matrix-v1"
EVENT_TYPES = {"TASK", "AGENT", "MODEL", "TOOL", "CHECK", "CHECKPOINT", "TIMEOUT", "CANCELLATION", "RECEIPT", "STATUS"}
EVENT_STATUSES = {"STARTED", "IN_PROGRESS", "PASS", "FAILED", "BLOCKED", "CANCELLED", "TIMED_OUT", "SOURCE_ONLY", "NOT_VERIFIED", "CLOSED"}
ITEM_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_VERIFIED", "NOT_RUN", "MISSING_COVERAGE", "NOT_REQUIRED"}

_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users|private|tmp|var|mnt|media)/[^\s,;]+")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _redact(value: Any) -> Any:
    value = redact_value(value)
    if isinstance(value, str):
        return _ABSOLUTE_PATH.sub("[REDACTED_PATH]", value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _redact(item) for key, item in value.items()}
    return value


def redact(value: Any) -> Any:
    """Return a sanitized portable copy for evidence output."""

    return _redact(value)


def _event_id(event_type: str, task_id: str, status: str, source_fingerprint: str, sequence: int, details: Mapping[str, Any]) -> str:
    seed = repr((event_type, task_id, status, source_fingerprint, sequence, details)).encode("utf-8")
    return sha256(seed).hexdigest()


def build_event(
    event_type: str,
    task_id: str,
    status: str,
    *,
    source_fingerprint: str,
    details: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    sequence: int = 0,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Build a sanitized, source-only event with ordering metadata."""

    normalized_details = _redact(dict(details or {}))
    event_type = str(event_type).strip().upper()
    status = str(status).strip().upper()
    task_id = str(task_id).strip()
    source_fingerprint = str(source_fingerprint).strip()
    timestamp = occurred_at or _iso(_now())
    event = {
        "schemaVersion": EVENT_SCHEMA_VERSION,
        "eventId": _event_id(event_type, task_id, status, source_fingerprint, sequence, normalized_details),
        "runId": str(run_id or f"source-run-{task_id}"),
        "eventType": event_type,
        "taskId": task_id,
        "sequence": sequence,
        "occurredAt": timestamp,
        "status": status,
        "sourceFingerprint": source_fingerprint,
        "details": normalized_details,
        "live": False,
        "sideEffects": [],
    }
    return event


def validate_evidence_event(value: Any) -> dict[str, Any]:
    """Validate one event and ensure it cannot claim live execution."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["evidenceEvent: expected object"]}
    required = {
        "schemaVersion", "eventId", "runId", "eventType", "taskId", "sequence", "occurredAt",
        "status", "sourceFingerprint", "details", "live", "sideEffects",
    }
    errors.extend(f"evidenceEvent: unknown field {key}" for key in sorted(set(value) - required))
    errors.extend(f"evidenceEvent: missing required field {key}" for key in sorted(required - set(value)))
    if value.get("schemaVersion") != EVENT_SCHEMA_VERSION:
        errors.append(f"evidenceEvent.schemaVersion: expected {EVENT_SCHEMA_VERSION}")
    for key in ("eventId", "runId", "taskId", "sourceFingerprint"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"evidenceEvent.{key}: expected non-empty string")
    if value.get("eventType") not in EVENT_TYPES:
        errors.append("evidenceEvent.eventType: unsupported value")
    if value.get("status") not in EVENT_STATUSES:
        errors.append("evidenceEvent.status: unsupported value")
    if not isinstance(value.get("sequence"), int) or isinstance(value.get("sequence"), bool) or value["sequence"] < 0:
        errors.append("evidenceEvent.sequence: expected integer >= 0")
    try:
        parsed = datetime.fromisoformat(str(value.get("occurredAt", "")).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            errors.append("evidenceEvent.occurredAt: timezone is required")
    except ValueError:
        errors.append("evidenceEvent.occurredAt: expected ISO-8601 timestamp")
    if not isinstance(value.get("details"), Mapping):
        errors.append("evidenceEvent.details: expected object")
    elif _redact(value["details"]) != value["details"]:
        errors.append("evidenceEvent.details: contains unredacted sensitive data")
    if value.get("live") is not False:
        errors.append("evidenceEvent.live: must be false for source-only status")
    if value.get("sideEffects") != []:
        errors.append("evidenceEvent.sideEffects: must be empty")
    return {"valid": not errors, "errors": errors}


def validate_event_sequence(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate identity and monotonic ordering for a batch of events."""

    errors: list[str] = []
    previous_sequence: int | None = None
    previous_time: datetime | None = None
    run_id: str | None = None
    task_id: str | None = None
    source: str | None = None
    for index, event in enumerate(events):
        result = validate_evidence_event(event)
        errors.extend(f"events[{index}]: {error}" for error in result["errors"])
        if not isinstance(event, Mapping):
            continue
        if run_id is None:
            run_id, task_id, source = event.get("runId"), event.get("taskId"), event.get("sourceFingerprint")
        for key, expected in (("runId", run_id), ("taskId", task_id), ("sourceFingerprint", source)):
            if event.get(key) != expected:
                errors.append(f"events[{index}].{key}: identity does not match the sequence")
        sequence = event.get("sequence")
        if isinstance(sequence, int) and previous_sequence is not None and sequence <= previous_sequence:
            errors.append(f"events[{index}].sequence: must increase monotonically")
        if isinstance(sequence, int):
            previous_sequence = sequence
        try:
            timestamp = datetime.fromisoformat(str(event.get("occurredAt", "")).replace("Z", "+00:00"))
        except ValueError:
            timestamp = None
        if timestamp is not None and previous_time is not None and timestamp < previous_time:
            errors.append(f"events[{index}].occurredAt: cannot precede the previous event")
        if timestamp is not None:
            previous_time = timestamp
    return {"valid": not errors, "errors": errors}


def _item(layer: str, index: int, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        source = _redact(dict(value))
        item = {
            key: source[key]
            for key in ("evidenceId", "summary", "status", "evidenceRefs")
            if key in source
        }
        item.setdefault("evidenceId", f"{layer}-{index + 1}")
        item.setdefault("summary", "evidence item")
        item.setdefault("status", "NOT_VERIFIED")
        item.setdefault("evidenceRefs", [])
        return item
    return {
        "evidenceId": f"{layer}-{index + 1}",
        "summary": str(value),
        "status": "NOT_VERIFIED",
        "evidenceRefs": [],
    }


def _requirement(index: int, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        item = _redact(dict(value))
        item.setdefault("requirementId", f"requirement-{index + 1}")
        item.setdefault("description", "requirement")
        item.setdefault("status", "NOT_VERIFIED")
        item.setdefault("evidenceRefs", [])
        item.setdefault("residualRisk", ["Source-only contract; runtime proof is not connected."])
        return item
    return {
        "requirementId": f"requirement-{index + 1}",
        "description": str(value),
        "status": "NOT_VERIFIED",
        "evidenceRefs": [],
        "residualRisk": ["Source-only contract; runtime proof is not connected."],
    }


def build_evidence_matrix(
    requirements: list[Any],
    *,
    static: list[Any] | None = None,
    runtime: list[Any] | None = None,
    device: list[Any] | None = None,
    activation: list[Any] | None = None,
    task_id: str = "source-only",
    source_fingerprint: str = "source-only",
) -> dict[str, Any]:
    """Build a source-only evidence matrix and explicit coverage status."""

    static_items = [_item("static", index, value) for index, value in enumerate(static or [])]
    runtime_items = [_item("runtime", index, value) for index, value in enumerate(runtime or [])]
    device_items = [_item("device", index, value) for index, value in enumerate(device or [])]
    activation_items = [_item("activation", index, value) for index, value in enumerate(activation or [])]
    if runtime_items or device_items or activation_items:
        status = "PARTIAL"
    else:
        status = "SOURCE_ONLY"
    if any(item.get("status") in {"BLOCKED", "MISSING_COVERAGE", "NOT_VERIFIED", "NOT_RUN"} for item in static_items + runtime_items + device_items + activation_items):
        if runtime_items or device_items or activation_items:
            status = "MISSING_COVERAGE"
    generated_at = _iso(_now())
    matrix = {
        "schemaVersion": MATRIX_SCHEMA_VERSION,
        "matrixId": sha256(repr((task_id, source_fingerprint, requirements, static_items, runtime_items, device_items, activation_items)).encode("utf-8")).hexdigest(),
        "taskId": str(task_id),
        "sourceFingerprint": str(source_fingerprint),
        "requirements": [_requirement(index, value) for index, value in enumerate(requirements)],
        "static": static_items,
        "runtime": runtime_items,
        "device": device_items,
        "activation": activation_items,
        "status": status,
        "liveExecution": False,
        "freshness": {"status": "CURRENT", "generatedAt": generated_at, "maxAgeSeconds": 86400},
        "sideEffects": [],
    }
    return matrix


def validate_evidence_matrix(value: Any) -> dict[str, Any]:
    """Validate matrix shape, status coverage, freshness, and no-live claims."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["evidenceMatrix: expected object"]}
    required = {
        "schemaVersion", "matrixId", "taskId", "sourceFingerprint", "requirements", "static", "runtime",
        "device", "activation", "status", "liveExecution", "freshness", "sideEffects",
    }
    errors.extend(f"evidenceMatrix: unknown field {key}" for key in sorted(set(value) - required))
    errors.extend(f"evidenceMatrix: missing required field {key}" for key in sorted(required - set(value)))
    if value.get("schemaVersion") != MATRIX_SCHEMA_VERSION:
        errors.append(f"evidenceMatrix.schemaVersion: expected {MATRIX_SCHEMA_VERSION}")
    for key in ("matrixId", "taskId", "sourceFingerprint"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"evidenceMatrix.{key}: expected non-empty string")
    if value.get("status") not in {"SOURCE_ONLY", "PARTIAL", "MISSING_COVERAGE", "COMPLETE"}:
        errors.append("evidenceMatrix.status: unsupported value")
    if value.get("liveExecution") is not False:
        errors.append("evidenceMatrix.liveExecution: must be false")
    if value.get("sideEffects") != []:
        errors.append("evidenceMatrix.sideEffects: must be empty")
    for key in ("requirements", "static", "runtime", "device", "activation"):
        if not isinstance(value.get(key), list):
            errors.append(f"evidenceMatrix.{key}: expected array")
    for layer in ("static", "runtime", "device", "activation"):
        items = value.get(layer)
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    errors.append(f"evidenceMatrix.{layer}[{index}]: expected object")
                    continue
                for key in ("evidenceId", "summary"):
                    if not isinstance(item.get(key), str) or not item[key].strip():
                        errors.append(f"evidenceMatrix.{layer}[{index}].{key}: expected non-empty string")
                if item.get("status") not in ITEM_STATUSES:
                    errors.append(f"evidenceMatrix.{layer}[{index}].status: unsupported value")
                if not isinstance(item.get("evidenceRefs"), list) or not all(isinstance(ref, str) for ref in item.get("evidenceRefs", [])):
                    errors.append(f"evidenceMatrix.{layer}[{index}].evidenceRefs: expected string array")
    requirements = value.get("requirements")
    if isinstance(requirements, list):
        for index, item in enumerate(requirements):
            if not isinstance(item, Mapping):
                errors.append(f"evidenceMatrix.requirements[{index}]: expected object")
                continue
            for key in ("requirementId", "description"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"evidenceMatrix.requirements[{index}].{key}: expected non-empty string")
            if item.get("status") not in ITEM_STATUSES:
                errors.append(f"evidenceMatrix.requirements[{index}].status: unsupported value")
            if not isinstance(item.get("evidenceRefs"), list) or not all(isinstance(ref, str) for ref in item.get("evidenceRefs", [])):
                errors.append(f"evidenceMatrix.requirements[{index}].evidenceRefs: expected string array")
            if not isinstance(item.get("residualRisk"), list) or not all(isinstance(risk, str) for risk in item.get("residualRisk", [])):
                errors.append(f"evidenceMatrix.requirements[{index}].residualRisk: expected string array")
    freshness = value.get("freshness")
    if not isinstance(freshness, Mapping):
        errors.append("evidenceMatrix.freshness: expected object")
    else:
        if freshness.get("status") not in {"CURRENT", "STALE", "UNKNOWN"}:
            errors.append("evidenceMatrix.freshness.status: unsupported value")
        if not isinstance(freshness.get("generatedAt"), str) or not freshness["generatedAt"].strip():
            errors.append("evidenceMatrix.freshness.generatedAt: expected timestamp")
        if not isinstance(freshness.get("maxAgeSeconds"), int) or freshness["maxAgeSeconds"] < 0:
            errors.append("evidenceMatrix.freshness.maxAgeSeconds: expected integer >= 0")
    return {"valid": not errors, "errors": errors}


def validate_receipt_freshness(
    receipt: Mapping[str, Any],
    *,
    now: str | None = None,
    max_age_seconds: int = 86400,
) -> dict[str, Any]:
    """Check a receipt's finished timestamp without requiring a live clock service."""

    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return {"valid": False, "status": "INVALID", "errors": ["receipt: expected object"]}
    timestamp = receipt.get("finishedAt") or receipt.get("issuedAt")
    try:
        finished = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00")) if now else _now()
    except ValueError:
        return {"valid": False, "status": "INVALID", "errors": ["receipt timestamp must be ISO-8601"]}
    if finished.tzinfo is None or current.tzinfo is None:
        errors.append("receipt timestamps must include a timezone")
    if errors:
        return {"valid": False, "status": "INVALID", "errors": errors}
    age = (current - finished).total_seconds()
    if age < 0:
        errors.append("receipt finishedAt is in the future")
    if age > max_age_seconds:
        errors.append("receipt is stale")
    return {"valid": not errors, "status": "CURRENT" if not errors else "STALE", "ageSeconds": max(0, int(age)), "errors": errors}
