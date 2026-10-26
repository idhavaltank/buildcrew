"""Deterministic static compatibility comparison.

The comparator consumes source projections only. It never imports, launches,
or mutates a Web, Mobile, Flutter, candidate, or policy implementation.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

from .safety_boundaries import redact_value


SCHEMA_VERSION = "static-compatibility-report-v1"
SET_SCHEMA_VERSION = "static-compatibility-set-v1"
COMPARISON_FIELDS = (
    "route",
    "complexity",
    "riskTier",
    "requiredCapabilities",
    "requiredEvidence",
    "acceptanceCoverage",
    "policyBehavior",
    "sourceFingerprint",
)
FIELD_ALIASES = {
    "riskTier": ("riskTier", "risk"),
    "requiredEvidence": ("requiredEvidence", "requiredCoverage", "evidenceRequirements"),
    "acceptanceCoverage": ("acceptanceCoverage", "coverage", "acceptanceCriteria"),
    "policyBehavior": ("policyBehavior", "policy", "policyRules"),
}


def _safe(value: Any) -> Any:
    """Redact secrets and host-specific absolute paths from comparison output."""

    value = redact_value(value)
    if isinstance(value, str):
        parts = value.split()
        if any(part.startswith(("/home/", "/Users/", "/private/", "/tmp/", "/var/", "/mnt/", "/media/")) for part in parts):
            return "[REDACTED_PATH]"
        return value
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    return value


def _normalise_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, Mapping):
        return {str(key): _normalise_scalar(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, list):
        return sorted((_normalise_scalar(item) for item in value), key=repr)
    return value


def _value(projection: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    keys = FIELD_ALIASES.get(field, (field,))
    for key in keys:
        if key in projection:
            return True, _normalise_scalar(projection[key])
    return False, None


def _projection_label(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _fingerprint(*values: Any) -> str:
    return sha256(repr(values).encode("utf-8")).hexdigest()


def compare_static(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    names: Sequence[str] = ("web", "mobile"),
) -> dict[str, Any]:
    """Compare two normalized source projections and return a safe report.

    Missing fields are reported as ``MISSING_COVERAGE``. A real disagreement
    remains ``DIFFERENT`` even if another field is unavailable; no disagreement
    is ever promoted to a runtime or device pass.
    """

    errors: list[str] = []
    if not isinstance(left, Mapping):
        errors.append("left: expected object")
        left = {}
    if not isinstance(right, Mapping):
        errors.append("right: expected object")
        right = {}
    if len(names) != 2 or not all(isinstance(name, str) and name.strip() for name in names):
        errors.append("names: expected two non-empty labels")
        names = ("left", "right")

    differences: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    compared_fields: list[str] = []
    for field in COMPARISON_FIELDS:
        left_present, left_value = _value(left, field)
        right_present, right_value = _value(right, field)
        if left_present and right_present:
            compared_fields.append(field)
            if left_value != right_value:
                differences.append({
                    "field": field,
                    str(names[0]): _safe(left_value),
                    str(names[1]): _safe(right_value),
                    "status": "DIFFERENT",
                })
                coverage.append({"field": field, "status": "DIFFERENT"})
            else:
                coverage.append({"field": field, "status": "COMPARED"})
        else:
            missing = []
            if not left_present:
                missing.append(str(names[0]))
            if not right_present:
                missing.append(str(names[1]))
            coverage.append({"field": field, "status": "MISSING_COVERAGE", "missing": missing})
            differences.append({
                "field": field,
                "status": "MISSING_COVERAGE",
                "missing": missing,
                str(names[0]): _safe(left_value) if left_present else None,
                str(names[1]): _safe(right_value) if right_present else None,
            })

    has_difference = any(item.get("status") == "DIFFERENT" for item in differences)
    has_missing = any(item.get("status") == "MISSING_COVERAGE" for item in differences)
    if errors:
        status = "INVALID"
    elif has_difference:
        status = "DIFFERENT"
    elif has_missing or not compared_fields:
        status = "MISSING_COVERAGE"
    else:
        status = "COMPATIBLE"

    left_name = _projection_label(names[0], "left")
    right_name = _projection_label(names[1], "right")
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "comparisonId": _fingerprint(left_name, right_name, left, right),
        "status": status,
        "left": left_name,
        "right": right_name,
        "comparedFields": compared_fields,
        "coverage": coverage,
        "differences": differences,
        "executed": False,
        "sourceOnly": True,
        "sideEffects": [],
    }
    if errors:
        report["errors"] = errors
    return report


def validate_static_compatibility(value: Any) -> dict[str, Any]:
    """Validate the comparator's output boundary without external dependencies."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["compatibilityReport: expected object"]}
    required = {
        "schemaVersion", "comparisonId", "status", "left", "right", "comparedFields",
        "coverage", "differences", "executed", "sourceOnly", "sideEffects",
    }
    allowed = required | {"errors", "validation"}
    errors.extend(f"compatibilityReport: unknown field {key}" for key in sorted(set(value) - allowed))
    errors.extend(f"compatibilityReport: missing required field {key}" for key in sorted(required - set(value)))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"compatibilityReport.schemaVersion: expected {SCHEMA_VERSION}")
    if value.get("status") not in {"COMPATIBLE", "DIFFERENT", "MISSING_COVERAGE", "INVALID"}:
        errors.append("compatibilityReport.status: unsupported value")
    for key in ("comparisonId", "left", "right"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"compatibilityReport.{key}: expected non-empty string")
    for key in ("comparedFields", "coverage", "differences"):
        if not isinstance(value.get(key), list):
            errors.append(f"compatibilityReport.{key}: expected array")
    if value.get("executed") is not False:
        errors.append("compatibilityReport.executed: must be false")
    if value.get("sourceOnly") is not True:
        errors.append("compatibilityReport.sourceOnly: must be true")
    if value.get("sideEffects") != []:
        errors.append("compatibilityReport.sideEffects: must be empty")
    if value.get("status") != "INVALID" and value.get("errors"):
        errors.append("compatibilityReport.errors: only INVALID reports may contain errors")
    validation = value.get("validation")
    if validation is not None and (
        not isinstance(validation, Mapping)
        or not isinstance(validation.get("valid"), bool)
        or not isinstance(validation.get("errors"), list)
    ):
        errors.append("compatibilityReport.validation: expected valid boolean and errors array")
    return {"valid": not errors, "errors": errors}


def compare_static_set(
    projections: Mapping[str, Mapping[str, Any]],
    *,
    anchor: str | None = None,
) -> dict[str, Any]:
    """Compare an anchor projection with every other named source projection."""

    errors: list[str] = []
    if not isinstance(projections, Mapping):
        projections = {}
        errors.append("projections: expected object")
    names = list(projections)
    if len(names) < 2:
        errors.append("projections: at least two named projections are required")
    if len(set(names)) != len(names):
        errors.append("projections: labels must be unique")
    for name in names:
        if not isinstance(name, str) or not name.strip():
            errors.append("projections: every label must be a non-empty string")
        if not isinstance(projections.get(name), Mapping):
            errors.append(f"projections.{name}: expected object")
    if anchor is None and names:
        anchor = names[0]
    if anchor not in names:
        errors.append("anchor: must name one of the supplied projections")
        anchor = names[0] if names else "anchor"

    comparisons: list[dict[str, Any]] = []
    if not errors or all(isinstance(projections.get(name), Mapping) for name in names):
        for name in names:
            if name == anchor:
                continue
            comparisons.append(compare_static(projections[anchor], projections[name], names=(anchor, name)))
    statuses = [item.get("status") for item in comparisons]
    if errors:
        status = "INVALID"
    elif "INVALID" in statuses:
        status = "INVALID"
    elif "DIFFERENT" in statuses:
        status = "DIFFERENT"
    elif "MISSING_COVERAGE" in statuses:
        status = "MISSING_COVERAGE"
    else:
        status = "COMPATIBLE"
    report: dict[str, Any] = {
        "schemaVersion": SET_SCHEMA_VERSION,
        "setId": _fingerprint(anchor, names, projections),
        "status": status,
        "anchor": anchor,
        "projections": names,
        "comparisons": comparisons,
        "executed": False,
        "sourceOnly": True,
        "sideEffects": [],
    }
    if errors:
        report["errors"] = errors
    return report


def validate_static_compatibility_set(value: Any) -> dict[str, Any]:
    """Validate the multi-projection report boundary."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["compatibilitySet: expected object"]}
    required = {"schemaVersion", "setId", "status", "anchor", "projections", "comparisons", "executed", "sourceOnly", "sideEffects"}
    allowed = required | {"errors", "validation"}
    errors.extend(f"compatibilitySet: unknown field {key}" for key in sorted(set(value) - allowed))
    errors.extend(f"compatibilitySet: missing required field {key}" for key in sorted(required - set(value)))
    if value.get("schemaVersion") != SET_SCHEMA_VERSION:
        errors.append(f"compatibilitySet.schemaVersion: expected {SET_SCHEMA_VERSION}")
    if value.get("status") not in {"COMPATIBLE", "DIFFERENT", "MISSING_COVERAGE", "INVALID"}:
        errors.append("compatibilitySet.status: unsupported value")
    if not isinstance(value.get("setId"), str) or not value["setId"].strip():
        errors.append("compatibilitySet.setId: expected non-empty string")
    projections = value.get("projections")
    if not isinstance(projections, list) or len(projections) < 2 or not all(isinstance(item, str) and item.strip() for item in projections):
        errors.append("compatibilitySet.projections: expected at least two labels")
    if not isinstance(projections, list) or value.get("anchor") not in projections:
        errors.append("compatibilitySet.anchor: must be one of projections")
    if not isinstance(value.get("comparisons"), list):
        errors.append("compatibilitySet.comparisons: expected array")
    if value.get("executed") is not False:
        errors.append("compatibilitySet.executed: must be false")
    if value.get("sourceOnly") is not True:
        errors.append("compatibilitySet.sourceOnly: must be true")
    if value.get("sideEffects") != []:
        errors.append("compatibilitySet.sideEffects: must be empty")
    if value.get("status") != "INVALID" and value.get("errors"):
        errors.append("compatibilitySet.errors: only INVALID reports may contain errors")
    validation = value.get("validation")
    if validation is not None and (
        not isinstance(validation, Mapping)
        or not isinstance(validation.get("valid"), bool)
        or not isinstance(validation.get("errors"), list)
    ):
        errors.append("compatibilitySet.validation: expected valid boolean and errors array")
    return {"valid": not errors, "errors": errors}
