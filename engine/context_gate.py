"""Fail-closed project-context gate layered over the v2 shadow binder."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .context_shadow import bind_project_context_shadow, validate_shadow_binding
from .reuse_assessment import SCHEMA_VERSION as REUSE_ASSESSMENT_SCHEMA_VERSION
from .reuse_assessment import validate_reuse_assessment


SCHEMA_VERSION = "context-enforced-gate-v1"
ACTIONS = {"routing", "model_selection", "agent_selection", "mutation", "closure"}
DECISIONS = {"ALLOW", "BLOCKED", "MISSING_COVERAGE", "INVALID"}


class ContextGateError(ValueError):
    """Raised when a gate request is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _reuse_assessment_gate(
    intake: Mapping[str, Any],
    context_map: Mapping[str, Any],
    action: str,
    reuse_assessment: Mapping[str, Any] | None,
) -> dict[str, Any]:
    repository = context_map.get("repository", {}) if isinstance(context_map.get("repository"), Mapping) else {}
    fingerprints = context_map.get("fingerprints", {}) if isinstance(context_map.get("fingerprints"), Mapping) else {}
    required = action == "mutation" or (action == "closure" and intake.get("mutationRequested") is True)
    summary = {
        "required": required,
        "status": "NOT_REQUIRED",
        "schemaVersion": REUSE_ASSESSMENT_SCHEMA_VERSION,
        "taskId": _text(intake.get("taskId")) or "unknown-task",
        "repositoryId": _text(repository.get("repositoryId")) or "unknown-repository",
        "sourceFingerprint": _text(fingerprints.get("sourceFingerprint")),
        "taskScopeFingerprint": _text(fingerprints.get("taskScopeFingerprint")),
        "outcome": None,
        "errors": [],
    }
    if not required:
        return summary
    if not isinstance(reuse_assessment, Mapping):
        summary["status"] = "MISSING"
        summary["errors"] = ["reuseAssessment: required before mutation authority can be granted"]
        return summary
    validation = validate_reuse_assessment(
        reuse_assessment,
        expected_task_id=summary["taskId"],
        expected_repository_id=summary["repositoryId"],
        expected_source_fingerprint=summary["sourceFingerprint"],
        expected_task_scope_fingerprint=summary["taskScopeFingerprint"],
    )
    summary["status"] = "VALID" if validation["valid"] else "INVALID"
    summary["outcome"] = reuse_assessment.get("decision")
    summary["errors"] = list(validation["errors"])
    return summary


def enforce_project_context_gate(
    intake: Mapping[str, Any],
    context_map: Mapping[str, Any],
    receipt: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    action: str = "routing",
    reuse_assessment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require valid, fresh, identity-matched sidecars before a controller action.

    This function is deliberately pure.  The actual AE controller must call it
    before routing/model/agent selection or mutation; this package does not
    discover or replace that host lifecycle.
    """

    if action not in ACTIONS:
        raise ContextGateError(f"unsupported gate action: {action}")
    shadow = bind_project_context_shadow(intake, context_map, receipt, policy)
    shadow_validation = validate_shadow_binding(shadow)
    blockers = list(shadow.get("blockers", [])) if isinstance(shadow.get("blockers"), list) else []
    decision = "ALLOW"
    if not shadow_validation["valid"] or shadow.get("status") == "INVALID":
        decision = "INVALID"
        blockers.extend(f"shadow binding invalid: {error}" for error in shadow_validation["errors"])
    elif shadow.get("status") == "MISSING_COVERAGE":
        decision = "MISSING_COVERAGE"
    elif shadow.get("status") == "BLOCKED":
        decision = "BLOCKED"
    reuse_gate = _reuse_assessment_gate(intake, context_map, action, reuse_assessment)
    if reuse_gate["required"] and reuse_gate["status"] != "VALID":
        decision = "BLOCKED"
        blockers.extend(reuse_gate["errors"])
    if action in {"mutation", "closure"} and policy.get("mutationStatus") != "READY":
        decision = "BLOCKED"
        blockers.append(f"{action} requires policy mutationStatus READY")
    blockers = sorted(set(str(item) for item in blockers if str(item).strip()))
    if decision == "ALLOW" and blockers:
        decision = "BLOCKED"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "ENFORCED_CONTRACT",
        "decision": decision,
        "status": decision,
        "action": action,
        "taskId": _text(intake.get("taskId")) or "unknown-task",
        "repositoryId": _text(intake.get("repositoryId")) or "unknown-repository",
        "fingerprints": dict(shadow.get("fingerprints", {})) if isinstance(shadow.get("fingerprints"), Mapping) else {},
        "sidecars": dict(shadow.get("sidecars", {})) if isinstance(shadow.get("sidecars"), Mapping) else {},
        "controllerGate": {
            "enforcement": "ENFORCED",
            "routeMutation": "APPLIED_ONLY_IF_ALLOWED",
            "modelSelection": "APPLIED_ONLY_IF_ALLOWED",
            "agentSelection": "APPLIED_ONLY_IF_ALLOWED",
            "mutation": "APPLIED_ONLY_IF_ALLOWED",
            "reuseAssessment": "REQUIRED_FOR_MUTATION",
            "closureAuthority": "AE",
        },
        "shadowBinding": shadow,
        "reuseAssessment": reuse_gate,
        "blockers": blockers,
        "sideEffects": [],
        "generatedAt": _now(),
    }


def validate_enforced_gate(value: Any) -> dict[str, Any]:
    """Validate the stable gate output and its no-side-effect invariant."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["enforcedGate: expected object"]}
    required = {
        "schemaVersion", "mode", "decision", "status", "action", "taskId", "repositoryId",
        "fingerprints", "sidecars", "controllerGate", "shadowBinding", "reuseAssessment", "blockers", "sideEffects", "generatedAt",
    }
    errors.extend(f"enforcedGate: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"enforcedGate: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"enforcedGate.schemaVersion: expected {SCHEMA_VERSION}")
    if value.get("mode") != "ENFORCED_CONTRACT":
        errors.append("enforcedGate.mode: expected ENFORCED_CONTRACT")
    if value.get("decision") not in DECISIONS or value.get("status") != value.get("decision"):
        errors.append("enforcedGate.decision: invalid decision/status")
    if value.get("action") not in ACTIONS:
        errors.append("enforcedGate.action: unsupported action")
    for key in ("taskId", "repositoryId", "generatedAt"):
        if not _text(value.get(key)):
            errors.append(f"enforcedGate.{key}: expected non-empty string")
    gate = value.get("controllerGate")
    expected = {"enforcement", "routeMutation", "modelSelection", "agentSelection", "mutation", "reuseAssessment", "closureAuthority"}
    if not isinstance(gate, Mapping) or set(gate) != expected:
        errors.append("enforcedGate.controllerGate: expected exact AE enforcement declaration")
    elif gate.get("enforcement") != "ENFORCED" or gate.get("closureAuthority") != "AE":
        errors.append("enforcedGate.controllerGate: invalid authority declaration")
    shadow_validation = validate_shadow_binding(value.get("shadowBinding"))
    if not shadow_validation["valid"]:
        errors.extend(f"enforcedGate.shadowBinding: {error}" for error in shadow_validation["errors"])
    reuse_gate = value.get("reuseAssessment")
    reuse_fields = {
        "required", "status", "schemaVersion", "taskId", "repositoryId", "sourceFingerprint",
        "taskScopeFingerprint", "outcome", "errors",
    }
    if not isinstance(reuse_gate, Mapping) or set(reuse_gate) != reuse_fields:
        errors.append("enforcedGate.reuseAssessment: expected exact validation summary")
    else:
        if not isinstance(reuse_gate.get("required"), bool):
            errors.append("enforcedGate.reuseAssessment.required: expected boolean")
        if reuse_gate.get("status") not in {"NOT_REQUIRED", "MISSING", "INVALID", "VALID"}:
            errors.append("enforcedGate.reuseAssessment.status: unsupported value")
        if reuse_gate.get("schemaVersion") != REUSE_ASSESSMENT_SCHEMA_VERSION:
            errors.append(f"enforcedGate.reuseAssessment.schemaVersion: expected {REUSE_ASSESSMENT_SCHEMA_VERSION}")
        for key in ("taskId", "repositoryId", "sourceFingerprint", "taskScopeFingerprint"):
            if not _text(reuse_gate.get(key)):
                errors.append(f"enforcedGate.reuseAssessment.{key}: expected non-empty string")
        if reuse_gate.get("outcome") not in {None, "REUSE", "EXTEND", "NEW", "DOCUMENTED_EXCEPTION"}:
            errors.append("enforcedGate.reuseAssessment.outcome: unsupported value")
        if not isinstance(reuse_gate.get("errors"), list) or not all(isinstance(item, str) and item.strip() for item in reuse_gate.get("errors", [])):
            errors.append("enforcedGate.reuseAssessment.errors: expected string array")
        if reuse_gate.get("required") and reuse_gate.get("status") == "NOT_REQUIRED":
            errors.append("enforcedGate.reuseAssessment.status: required assessment cannot be NOT_REQUIRED")
        if not reuse_gate.get("required") and reuse_gate.get("status") != "NOT_REQUIRED":
            errors.append("enforcedGate.reuseAssessment.status: non-required assessment must be NOT_REQUIRED")
        if value.get("action") == "mutation" and value.get("decision") == "ALLOW" and reuse_gate.get("status") != "VALID":
            errors.append("enforcedGate.reuseAssessment: mutation ALLOW requires VALID assessment")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("blockers", [])):
        errors.append("enforcedGate.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("enforcedGate.sideEffects: must be empty")
    if value.get("decision") == "ALLOW" and value.get("blockers"):
        errors.append("enforcedGate.blockers: ALLOW cannot contain blockers")
    return {"valid": not errors, "errors": errors}
