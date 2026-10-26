"""Freshness and identity checks for model assignments and worker evidence.

This module is deliberately dependency-free and side-effect free.  It does not
decide whether work is complete; it only answers whether a record is still
eligible to be considered by the AE controller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class FreshnessError(ValueError):
    """Raised only for malformed timestamp input."""


REQUIRED_BINDING_KEYS = (
    "projectId",
    "tenantId",
    "repositoryId",
    "taskId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "sourceFingerprint",
    "policyFingerprint",
    "hostId",
    "role",
    "scope",
)


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FreshnessError(f"{label}: expected an ISO-8601 timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise FreshnessError(f"{label}: invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FreshnessError(f"{label}: timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _same(expected: Mapping[str, Any], actual: Mapping[str, Any], key: str, label: str) -> list[str]:
    if key not in expected:
        return []
    if key not in actual:
        return [f"{label}.{key}: missing required binding"]
    if actual.get(key) != expected.get(key):
        return [f"{label}.{key}: does not match expected binding"]
    return []


def validate_identity_binding(
    record: Any,
    expected: Mapping[str, Any],
    *,
    label: str = "record",
) -> dict[str, Any]:
    """Compare all supplied identity fields without accepting text-only matches."""

    if not isinstance(record, Mapping):
        return {"valid": False, "errors": [f"{label}: expected object"]}
    if not isinstance(expected, Mapping) or not expected:
        return {"valid": False, "errors": [f"{label}: expected a complete non-empty identity binding"]}
    errors: list[str] = []
    for key in REQUIRED_BINDING_KEYS:
        if key not in expected:
            errors.append(f"{label}: expected binding is missing {key}")
    binding_keys = (
        *REQUIRED_BINDING_KEYS,
        "repository",
        "diffFingerprint",
        "host",
    )
    for key in binding_keys:
        errors.extend(_same(expected, record, key, label))
    return {"valid": not errors, "errors": errors}


def validate_time_window(
    record: Any,
    *,
    now: datetime | None = None,
    required: tuple[str, ...] = ("issuedAt", "expiresAt"),
    label: str = "record",
    max_future_seconds: int = 5,
) -> dict[str, Any]:
    """Validate ordering, expiry, and a small clock-skew allowance."""

    if not isinstance(record, Mapping):
        return {"valid": False, "errors": [f"{label}: expected object"]}
    errors: list[str] = []
    parsed: dict[str, datetime] = {}
    for key in required:
        if key not in record:
            errors.append(f"{label}: missing required timestamp {key}")
            continue
        try:
            parsed[key] = parse_timestamp(record.get(key), f"{label}.{key}")
        except FreshnessError as exc:
            errors.append(str(exc))

    optional = tuple(key for key in ("startedAt", "finishedAt", "invalidatedAt") if key in record)
    for key in optional:
        try:
            parsed[key] = parse_timestamp(record.get(key), f"{label}.{key}")
        except FreshnessError as exc:
            errors.append(str(exc))

    order = ("issuedAt", "startedAt", "finishedAt", "expiresAt")
    for earlier, later in zip(order, order[1:]):
        if earlier in parsed and later in parsed and parsed[earlier] > parsed[later]:
            errors.append(f"{label}: {earlier} must not be after {later}")

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if "issuedAt" in parsed and parsed["issuedAt"] > reference + timedelta(seconds=max_future_seconds):
        errors.append(f"{label}.issuedAt: future-dated beyond clock-skew allowance")
    if "startedAt" in parsed and parsed["startedAt"] > reference + timedelta(seconds=max_future_seconds):
        errors.append(f"{label}.startedAt: future-dated beyond clock-skew allowance")
    if "expiresAt" in parsed and parsed["expiresAt"] <= reference:
        errors.append(f"{label}.expiresAt: evidence is expired")
    if "invalidatedAt" in parsed and "issuedAt" in parsed and parsed["invalidatedAt"] < parsed["issuedAt"]:
        errors.append(f"{label}.invalidatedAt: must not precede issuedAt")

    return {
        "valid": not errors,
        "errors": errors,
        "status": "CURRENT" if not errors else "INVALIDATED",
    }


def validate_fresh_evidence(
    record: Any,
    expected: Mapping[str, Any],
    *,
    now: datetime | None = None,
    label: str = "evidence",
) -> dict[str, Any]:
    """Require identity binding and a current time window for new evidence."""

    identity = validate_identity_binding(record, expected, label=label)
    window = validate_time_window(record, now=now, label=label)
    errors = identity["errors"] + window["errors"]
    return {
        "valid": not errors,
        "status": "CURRENT" if not errors else "INVALIDATED",
        "errors": errors,
    }
