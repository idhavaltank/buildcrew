#!/usr/bin/env python3
"""Strict source contracts for named-reviewer assignment and receipts.

The generic ``model-assignment-v1`` remains the parent authorization.  This
module only adds a named role/profile and review-shard binding.  It does not
read role files, authenticate a host, dispatch a prompt, or claim live proof.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from .model_selection import validate_assignment


ASSIGNMENT_SCHEMA_VERSION = "ae-named-reviewer-assignment-v1"
RECEIPT_SCHEMA_VERSION = "ae-named-reviewer-receipt-v1"
TASK_REVIEW_BINDING_SCHEMA_VERSION = "ae-task-review-binding-v1"
AUTHORIZATION_ENVELOPE_SCHEMA_VERSION = "ae-authorization-evidence-envelope-v1"
AUTHORIZED_WRAPPED_ARTIFACT_TYPE = "model-assignment-v1"

REVIEW_LENSES = {"work", "architecture", "security", "runtime", "visual"}
NAMED_ROLES = {"worker", "final-audit"}
ASSIGNMENT_STATUSES = {
    "SELECTED",
    "FALLBACK_EVIDENCED",
    "OVERRIDE_EVIDENCED",
    "CAPABILITY_UNAVAILABLE",
}
EXECUTION_STATUSES = {
    "completed",
    "failed",
    "timed_out",
    "interrupted",
    "crashed",
    "shutdown",
    "pre_prompt_failure",
    "replay_rejected",
}
COVERAGE_STATUSES = {"PASS", "FINDINGS", "MISSING_COVERAGE", "BLOCKED"}
NON_DISPATCHABLE_ROUTES = {
    "DIRECT",
    "DIRECT_NO_ASSIGNMENT",
    "FAST_DAILY_DIRECT",
    "SIMPLE_QUESTION",
    "LIGHTWEIGHT",
}

FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

TASK_REVIEW_BINDING_FIELDS = {
    "schemaVersion",
    "bindingId",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "acceptanceFingerprint",
    "criterionMapFingerprint",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "writerOwner",
    "requiredLenses",
    "shardFingerprints",
    "hostId",
    "authorizationEpoch",
    "issuedAt",
    "expiresAt",
}

AUTHORIZATION_ENVELOPE_FIELDS = {
    "schemaVersion",
    "envelopeId",
    "approvalId",
    "taskReviewBindingDigest",
    "wrappedArtifactType",
    "wrappedArtifactDigest",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "hostId",
    "authorizationEpoch",
    "producerId",
    "issuedAt",
    "expiresAt",
    "terminal",
}

NAMED_ASSIGNMENT_FIELDS = {
    "schemaVersion",
    "assignmentId",
    "genericAssignmentId",
    "genericAssignmentDigest",
    "taskReviewBindingId",
    "taskReviewBindingDigest",
    "authorizationEnvelopeId",
    "authorizationEnvelopeDigest",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "policyFingerprint",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "sourceFingerprint",
    "role",
    "namedReviewer",
    "namedRoleProfileFingerprint",
    "capability",
    "scope",
    "overallDiffFingerprint",
    "shardFingerprint",
    "configuredModel",
    "resolvedModel",
    "configuredReasoningEffort",
    "resolvedReasoningEffort",
    "status",
    "hostId",
    "issuedAt",
    "expiresAt",
}

NAMED_RECEIPT_FIELDS = {
    "schemaVersion",
    "receiptId",
    "assignmentId",
    "genericAssignmentId",
    "genericAssignmentDigest",
    "taskReviewBindingDigest",
    "authorizationEnvelopeDigest",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "role",
    "host",
    "expiresAt",
    "policyFingerprint",
    "sourceFingerprint",
    "scope",
    "overallDiffFingerprint",
    "shardFingerprint",
    "namedReviewer",
    "namedRoleProfileFingerprint",
    "capability",
    "requestedModel",
    "requestedReasoningEffort",
    "observedModel",
    "observedReasoningEffort",
    "hostObservationId",
    "hostObservationDigest",
    "processIdentity",
    "nonce",
    "authenticatedObservation",
    "promptStarted",
    "terminal",
    "live",
    "executionStatus",
    "coverageStatus",
    "verdict",
    "evidence",
    "blockers",
    "baseReviewReceiptDigest",
    "baseReviewReceipt",
    "startedAt",
    "finishedAt",
}


class NamedReviewerContractError(ValueError):
    """Raised when a named-reviewer artifact cannot be safely constructed."""


def canonical_json(value: Any) -> str:
    """Return stable JSON and fail for non-JSON-compatible values."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def artifact_digest(schema_version: str, value: Any) -> str:
    """Return a domain-separated content digest for an immutable artifact."""

    payload = f"{schema_version}\0{canonical_json(value)}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def compute_profile_fingerprint(profile: Mapping[str, Any]) -> str:
    """Fingerprint only the authoritative role-profile content."""

    if not isinstance(profile, Mapping):
        raise NamedReviewerContractError("roleProfile: expected object")
    normalized = {key: value for key, value in profile.items() if key != "profileFingerprint"}
    allowed = {"name", "model", "reasoningEffort", "capability"}
    if set(normalized) != allowed:
        raise NamedReviewerContractError("roleProfile: expected name, model, reasoningEffort, and capability")
    if any(not isinstance(normalized[key], str) or not normalized[key].strip() for key in allowed):
        raise NamedReviewerContractError("roleProfile: every field must be a non-empty string")
    return artifact_digest("ae-named-role-profile-v1", normalized)


def _timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: expected ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label}: timezone is required")
        return None
    return parsed


def _strict_object(value: Any, fields: set[str], label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    missing = fields - set(value)
    unknown = set(value) - fields
    errors.extend(f"{label}: missing required field {key}" for key in sorted(missing))
    errors.extend(f"{label}: unknown field {key}" for key in sorted(unknown))
    return value


def _string(value: Any, label: str, errors: list[str], *, allow_null: bool = False) -> None:
    if allow_null and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _fingerprint(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not FINGERPRINT_RE.fullmatch(value):
        errors.append(f"{label}: expected sha256:<64 lowercase hex>")


def _string_array(value: Any, label: str, errors: list[str], *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return []
    if non_empty and not value:
        errors.append(f"{label}: expected non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label}: expected non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    return list(value)


def _validate_window(value: Mapping[str, Any], errors: list[str], *, now: str | None = None, label: str) -> None:
    issued = _timestamp(value.get("issuedAt"), f"{label}.issuedAt", errors)
    expires = _timestamp(value.get("expiresAt"), f"{label}.expiresAt", errors)
    current = _timestamp(now, "now", errors) if now is not None else None
    if issued is not None and expires is not None and expires <= issued:
        errors.append(f"{label}: expiresAt must be after issuedAt")
    if current is not None and issued is not None and current < issued:
        errors.append(f"{label}: not yet valid")
    if current is not None and expires is not None and current >= expires:
        errors.append(f"{label}: expired")


def validate_task_review_binding(
    value: Any,
    *,
    trusted_context: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    binding = _strict_object(value, TASK_REVIEW_BINDING_FIELDS, "taskReviewBinding", errors)
    if binding is None:
        return {"valid": False, "errors": errors}
    if binding.get("schemaVersion") != TASK_REVIEW_BINDING_SCHEMA_VERSION:
        errors.append(f"taskReviewBinding.schemaVersion: expected {TASK_REVIEW_BINDING_SCHEMA_VERSION}")
    for key in ("bindingId", "taskId", "projectId", "repositoryId", "sourceFingerprint", "writerOwner", "hostId"):
        _string(binding.get(key), f"taskReviewBinding.{key}", errors)
    _string(binding.get("tenantId"), "taskReviewBinding.tenantId", errors, allow_null=True)
    for key in ("acceptanceFingerprint", "criterionMapFingerprint", "overallDiffFingerprint"):
        _fingerprint(binding.get(key), f"taskReviewBinding.{key}", errors)
    lenses = _string_array(binding.get("requiredLenses"), "taskReviewBinding.requiredLenses", errors, non_empty=True)
    for lens in lenses:
        if lens not in REVIEW_LENSES:
            errors.append(f"taskReviewBinding.requiredLenses: unsupported lens {lens!r}")
    shards = _string_array(binding.get("shardFingerprints"), "taskReviewBinding.shardFingerprints", errors, non_empty=True)
    for index, fingerprint in enumerate(shards):
        _fingerprint(fingerprint, f"taskReviewBinding.shardFingerprints[{index}]", errors)
    epoch = binding.get("authorizationEpoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        errors.append("taskReviewBinding.authorizationEpoch: expected positive integer")
    _validate_window(binding, errors, now=now, label="taskReviewBinding")
    if trusted_context is not None:
        for key in (
            "taskId",
            "projectId",
            "repositoryId",
            "tenantId",
            "sourceFingerprint",
            "overallDiffFingerprint",
            "hostId",
            "authorizationEpoch",
        ):
            if key in trusted_context and binding.get(key) != trusted_context.get(key):
                errors.append(f"taskReviewBinding.{key}: does not match trusted context")
    return {"valid": not errors, "errors": errors}


def validate_authorization_evidence_envelope(
    value: Any,
    *,
    wrapped_artifact: Mapping[str, Any] | None = None,
    task_review_binding: Mapping[str, Any] | None = None,
    trusted_context: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    envelope = _strict_object(value, AUTHORIZATION_ENVELOPE_FIELDS, "authorizationEnvelope", errors)
    if envelope is None:
        return {"valid": False, "errors": errors}
    if envelope.get("schemaVersion") != AUTHORIZATION_ENVELOPE_SCHEMA_VERSION:
        errors.append(f"authorizationEnvelope.schemaVersion: expected {AUTHORIZATION_ENVELOPE_SCHEMA_VERSION}")
    for key in (
        "envelopeId",
        "approvalId",
        "wrappedArtifactType",
        "taskId",
        "projectId",
        "repositoryId",
        "hostId",
        "producerId",
    ):
        _string(envelope.get(key), f"authorizationEnvelope.{key}", errors)
    if envelope.get("wrappedArtifactType") != AUTHORIZED_WRAPPED_ARTIFACT_TYPE:
        errors.append(
            "authorizationEnvelope.wrappedArtifactType: expected model-assignment-v1"
        )
    _string(envelope.get("tenantId"), "authorizationEnvelope.tenantId", errors, allow_null=True)
    for key in ("taskReviewBindingDigest", "wrappedArtifactDigest"):
        _fingerprint(envelope.get(key), f"authorizationEnvelope.{key}", errors)
    epoch = envelope.get("authorizationEpoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        errors.append("authorizationEnvelope.authorizationEpoch: expected positive integer")
    if envelope.get("terminal") is not True:
        errors.append("authorizationEnvelope.terminal: must be exactly true")
    _validate_window(envelope, errors, now=now, label="authorizationEnvelope")
    if wrapped_artifact is not None and isinstance(envelope.get("wrappedArtifactType"), str):
        expected = artifact_digest(envelope["wrappedArtifactType"], wrapped_artifact)
        if envelope.get("wrappedArtifactDigest") != expected:
            errors.append("authorizationEnvelope.wrappedArtifactDigest: does not match wrapped artifact")
    if task_review_binding is not None:
        expected = artifact_digest(TASK_REVIEW_BINDING_SCHEMA_VERSION, task_review_binding)
        if envelope.get("taskReviewBindingDigest") != expected:
            errors.append("authorizationEnvelope.taskReviewBindingDigest: does not match task review binding")
        for key in ("taskId", "projectId", "repositoryId", "tenantId", "hostId", "authorizationEpoch"):
            if envelope.get(key) != task_review_binding.get(key):
                errors.append(f"authorizationEnvelope.{key}: does not match task review binding")
    if trusted_context is not None:
        for key in ("taskId", "projectId", "repositoryId", "tenantId", "hostId", "authorizationEpoch"):
            if key in trusted_context and envelope.get(key) != trusted_context.get(key):
                errors.append(f"authorizationEnvelope.{key}: does not match trusted context")
    return {"valid": not errors, "errors": errors}


def _profile_errors(profile: Any, expected_fingerprint: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, Mapping):
        return ["roleProfile: expected object"]
    allowed = {"name", "model", "reasoningEffort", "capability", "profileFingerprint"}
    if set(profile) != allowed:
        errors.append("roleProfile: unknown or missing fields")
        return errors
    for key in ("name", "model", "reasoningEffort", "capability"):
        _string(profile.get(key), f"roleProfile.{key}", errors)
    _fingerprint(profile.get("profileFingerprint"), "roleProfile.profileFingerprint", errors)
    try:
        computed = compute_profile_fingerprint(profile)
    except NamedReviewerContractError as exc:
        errors.append(str(exc))
    else:
        if profile.get("profileFingerprint") != computed:
            errors.append("roleProfile.profileFingerprint: does not match profile content")
        if expected_fingerprint is not None and computed != expected_fingerprint:
            errors.append("roleProfile.profileFingerprint: does not match trusted catalog")
    return errors


def _assignment_identity(payload: Mapping[str, Any]) -> str:
    normalized = {key: value for key, value in payload.items() if key != "assignmentId"}
    return "named-" + artifact_digest(ASSIGNMENT_SCHEMA_VERSION, normalized).split(":", 1)[1][:40]


def resolve_named_reviewer_assignment(
    generic_assignment: Mapping[str, Any],
    task_review_binding: Mapping[str, Any],
    authorization_envelope: Mapping[str, Any],
    role_profile: Mapping[str, Any],
    *,
    overall_diff_fingerprint: str,
    shard_fingerprint: str,
    route: str,
    expected_profile_fingerprint: str,
    supported_models: Sequence[str] | set[str],
    supported_efforts: Sequence[str] | set[str],
    now: str | None = None,
) -> dict[str, Any]:
    """Adapt one fully validated generic assignment into a named assignment."""

    if route in NON_DISPATCHABLE_ROUTES:
        raise NamedReviewerContractError(f"named reviewer is not permitted for route {route}")
    generic_result = validate_assignment(generic_assignment, now=now)
    if not generic_result["valid"]:
        raise NamedReviewerContractError("generic assignment is invalid: " + "; ".join(generic_result["errors"]))
    if generic_assignment.get("role") not in NAMED_ROLES:
        raise NamedReviewerContractError("generic assignment role cannot be adapted to a named reviewer")
    binding_result = validate_task_review_binding(task_review_binding, now=now)
    if not binding_result["valid"]:
        raise NamedReviewerContractError("task review binding is invalid: " + "; ".join(binding_result["errors"]))
    envelope_result = validate_authorization_evidence_envelope(
        authorization_envelope,
        wrapped_artifact=generic_assignment,
        task_review_binding=task_review_binding,
        now=now,
    )
    if not envelope_result["valid"]:
        raise NamedReviewerContractError("authorization envelope is invalid: " + "; ".join(envelope_result["errors"]))
    profile_errors = _profile_errors(role_profile, expected_profile_fingerprint)
    if profile_errors:
        raise NamedReviewerContractError("; ".join(profile_errors))
    _fingerprint(overall_diff_fingerprint, "overallDiffFingerprint", profile_errors)
    _fingerprint(shard_fingerprint, "shardFingerprint", profile_errors)
    if profile_errors:
        raise NamedReviewerContractError("; ".join(profile_errors))

    for key in ("taskId", "projectId", "repositoryId", "tenantId", "sourceFingerprint", "hostId"):
        if generic_assignment.get(key) != task_review_binding.get(key):
            raise NamedReviewerContractError(f"task review binding {key} does not match generic assignment")
    if task_review_binding.get("overallDiffFingerprint") != overall_diff_fingerprint:
        raise NamedReviewerContractError("overall diff does not match task review binding")
    if shard_fingerprint not in task_review_binding.get("shardFingerprints", []):
        raise NamedReviewerContractError("shard fingerprint is outside the task review binding")

    configured_model = role_profile["model"]
    configured_effort = role_profile["reasoningEffort"]
    available = configured_model in set(supported_models) and configured_effort in set(supported_efforts)
    status = "SELECTED" if available else "CAPABILITY_UNAVAILABLE"
    assignment: dict[str, Any] = {
        "schemaVersion": ASSIGNMENT_SCHEMA_VERSION,
        "assignmentId": "pending",
        "genericAssignmentId": generic_assignment["assignmentId"],
        "genericAssignmentDigest": artifact_digest("model-assignment-v1", generic_assignment),
        "taskReviewBindingId": task_review_binding["bindingId"],
        "taskReviewBindingDigest": artifact_digest(TASK_REVIEW_BINDING_SCHEMA_VERSION, task_review_binding),
        "authorizationEnvelopeId": authorization_envelope["envelopeId"],
        "authorizationEnvelopeDigest": artifact_digest(AUTHORIZATION_ENVELOPE_SCHEMA_VERSION, authorization_envelope),
        "taskId": generic_assignment["taskId"],
        "projectId": generic_assignment["projectId"],
        "repositoryId": generic_assignment["repositoryId"],
        "tenantId": generic_assignment.get("tenantId"),
        "policyFingerprint": generic_assignment["policyFingerprint"],
        "packetVersion": generic_assignment["packetVersion"],
        "planVersion": generic_assignment["planVersion"],
        "waveId": generic_assignment["waveId"],
        "attempt": generic_assignment["attempt"],
        "sourceFingerprint": generic_assignment["sourceFingerprint"],
        "role": generic_assignment["role"],
        "namedReviewer": role_profile["name"],
        "namedRoleProfileFingerprint": role_profile["profileFingerprint"],
        "capability": role_profile["capability"],
        "scope": json.loads(canonical_json(generic_assignment["scope"])),
        "overallDiffFingerprint": overall_diff_fingerprint,
        "shardFingerprint": shard_fingerprint,
        "configuredModel": configured_model,
        "resolvedModel": configured_model if available else None,
        "configuredReasoningEffort": configured_effort,
        "resolvedReasoningEffort": configured_effort if available else None,
        "status": status,
        "hostId": generic_assignment["hostId"],
        "issuedAt": generic_assignment["issuedAt"],
        "expiresAt": generic_assignment["expiresAt"],
    }
    assignment["assignmentId"] = _assignment_identity(assignment)
    return assignment


def validate_named_reviewer_assignment(
    value: Any,
    *,
    expected_generic_assignment: Mapping[str, Any] | None = None,
    expected_task_review_binding: Mapping[str, Any] | None = None,
    expected_authorization_envelope: Mapping[str, Any] | None = None,
    expected_role_profile: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    assignment = _strict_object(value, NAMED_ASSIGNMENT_FIELDS, "namedAssignment", errors)
    if assignment is None:
        return {"valid": False, "dispatchable": False, "errors": errors}
    if assignment.get("schemaVersion") != ASSIGNMENT_SCHEMA_VERSION:
        errors.append(f"namedAssignment.schemaVersion: expected {ASSIGNMENT_SCHEMA_VERSION}")
    for key in (
        "assignmentId",
        "genericAssignmentId",
        "taskReviewBindingId",
        "authorizationEnvelopeId",
        "taskId",
        "projectId",
        "repositoryId",
        "policyFingerprint",
        "packetVersion",
        "planVersion",
        "waveId",
        "sourceFingerprint",
        "namedReviewer",
        "capability",
        "configuredModel",
        "configuredReasoningEffort",
        "hostId",
    ):
        _string(assignment.get(key), f"namedAssignment.{key}", errors)
    _string(assignment.get("tenantId"), "namedAssignment.tenantId", errors, allow_null=True)
    for key in (
        "genericAssignmentDigest",
        "taskReviewBindingDigest",
        "authorizationEnvelopeDigest",
        "namedRoleProfileFingerprint",
        "overallDiffFingerprint",
        "shardFingerprint",
    ):
        _fingerprint(assignment.get(key), f"namedAssignment.{key}", errors)
    if assignment.get("role") not in NAMED_ROLES:
        errors.append("namedAssignment.role: expected worker or final-audit")
    scope = assignment.get("scope")
    if not isinstance(scope, Mapping) or set(scope) != {"scopeId", "paths", "authority"}:
        errors.append("namedAssignment.scope: invalid assignment scope")
    status = assignment.get("status")
    if status not in ASSIGNMENT_STATUSES:
        errors.append("namedAssignment.status: unsupported value")
    attempt = assignment.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        errors.append("namedAssignment.attempt: expected positive integer")
    _validate_window(assignment, errors, now=now, label="namedAssignment")
    if assignment.get("assignmentId") != _assignment_identity(assignment):
        errors.append("namedAssignment.assignmentId: does not match complete assignment content")
    if status == "CAPABILITY_UNAVAILABLE":
        if assignment.get("resolvedModel") is not None or assignment.get("resolvedReasoningEffort") is not None:
            errors.append("namedAssignment: unavailable assignment cannot contain resolved execution values")
    else:
        if assignment.get("resolvedModel") != assignment.get("configuredModel"):
            errors.append("namedAssignment.resolvedModel: exact named-role model is required")
        if assignment.get("resolvedReasoningEffort") != assignment.get("configuredReasoningEffort"):
            errors.append("namedAssignment.resolvedReasoningEffort: exact named-role effort is required")

    if expected_generic_assignment is not None:
        generic_result = validate_assignment(expected_generic_assignment, now=now)
        if not generic_result["valid"]:
            errors.extend(f"genericAssignment: {error}" for error in generic_result["errors"])
        if assignment.get("genericAssignmentDigest") != artifact_digest("model-assignment-v1", expected_generic_assignment):
            errors.append("namedAssignment.genericAssignmentDigest: does not match trusted generic assignment")
        field_map = {
            "genericAssignmentId": "assignmentId",
            "taskId": "taskId",
            "projectId": "projectId",
            "repositoryId": "repositoryId",
            "tenantId": "tenantId",
            "policyFingerprint": "policyFingerprint",
            "packetVersion": "packetVersion",
            "planVersion": "planVersion",
            "waveId": "waveId",
            "attempt": "attempt",
            "sourceFingerprint": "sourceFingerprint",
            "role": "role",
            "scope": "scope",
            "hostId": "hostId",
            "issuedAt": "issuedAt",
            "expiresAt": "expiresAt",
        }
        for actual_key, expected_key in field_map.items():
            if assignment.get(actual_key) != expected_generic_assignment.get(expected_key):
                errors.append(f"namedAssignment.{actual_key}: does not match trusted generic assignment")
    if expected_task_review_binding is not None:
        result = validate_task_review_binding(expected_task_review_binding, now=now)
        if not result["valid"]:
            errors.extend(f"taskReviewBinding: {error}" for error in result["errors"])
        if assignment.get("taskReviewBindingId") != expected_task_review_binding.get("bindingId"):
            errors.append("namedAssignment.taskReviewBindingId: does not match trusted binding")
        if assignment.get("taskReviewBindingDigest") != artifact_digest(TASK_REVIEW_BINDING_SCHEMA_VERSION, expected_task_review_binding):
            errors.append("namedAssignment.taskReviewBindingDigest: does not match trusted binding")
        if assignment.get("overallDiffFingerprint") != expected_task_review_binding.get("overallDiffFingerprint"):
            errors.append("namedAssignment.overallDiffFingerprint: does not match trusted binding")
        if assignment.get("shardFingerprint") not in expected_task_review_binding.get("shardFingerprints", []):
            errors.append("namedAssignment.shardFingerprint: outside trusted binding")
    if expected_authorization_envelope is not None:
        if assignment.get("authorizationEnvelopeId") != expected_authorization_envelope.get("envelopeId"):
            errors.append("namedAssignment.authorizationEnvelopeId: does not match trusted envelope")
        if assignment.get("authorizationEnvelopeDigest") != artifact_digest(AUTHORIZATION_ENVELOPE_SCHEMA_VERSION, expected_authorization_envelope):
            errors.append("namedAssignment.authorizationEnvelopeDigest: does not match trusted envelope")
    if expected_role_profile is not None:
        profile_errors = _profile_errors(expected_role_profile, expected_role_profile.get("profileFingerprint"))
        errors.extend(f"roleProfile: {error}" for error in profile_errors)
        profile_fields = {
            "namedReviewer": "name",
            "namedRoleProfileFingerprint": "profileFingerprint",
            "capability": "capability",
            "configuredModel": "model",
            "configuredReasoningEffort": "reasoningEffort",
        }
        for actual_key, expected_key in profile_fields.items():
            if assignment.get(actual_key) != expected_role_profile.get(expected_key):
                errors.append(f"namedAssignment.{actual_key}: does not match trusted role profile")
    return {
        "valid": not errors,
        "dispatchable": not errors and status in {"SELECTED", "FALLBACK_EVIDENCED", "OVERRIDE_EVIDENCED"},
        "errors": errors,
    }


def _validate_base_review_receipt(receipt: Any, assignment: Mapping[str, Any], errors: list[str]) -> None:
    try:
        from scripts.validate_hybrid_contracts import validate_receipt
    except ImportError as exc:  # pragma: no cover - packaging/import boundary
        errors.append(f"baseReviewReceipt: validator unavailable ({type(exc).__name__})")
        return
    result = validate_receipt(
        receipt,
        expected_task_id=assignment.get("taskId"),
        expected_fingerprint=assignment.get("sourceFingerprint"),
    )
    errors.extend(f"baseReviewReceipt: {error}" for error in result.get("errors", []))
    if result.get("completionEligible") is not True:
        errors.append("baseReviewReceipt: receipt is not completion eligible")
    if not isinstance(receipt, Mapping):
        return
    pairs = {
        "packetVersion": "packetVersion",
        "planVersion": "planVersion",
        "waveId": "waveId",
        "attempt": "attempt",
        "role": "role",
        "host": "hostId",
    }
    for receipt_key, assignment_key in pairs.items():
        if receipt.get(receipt_key) != assignment.get(assignment_key):
            errors.append(f"baseReviewReceipt.{receipt_key}: does not match named assignment")
    scope = receipt.get("scope")
    if isinstance(scope, Mapping):
        if scope.get("repository") != assignment.get("repositoryId"):
            errors.append("baseReviewReceipt.scope.repository: does not match named assignment")
        assignment_scope = assignment.get("scope")
        if isinstance(assignment_scope, Mapping) and scope.get("lane") != assignment_scope.get("scopeId"):
            errors.append("baseReviewReceipt.scope.lane: does not match named assignment")


def validate_named_reviewer_receipt(
    value: Any,
    *,
    expected_assignment: Mapping[str, Any],
    expected_host_observation: Mapping[str, Any] | None = None,
    now: str | None = None,
    require_live: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    receipt = _strict_object(value, NAMED_RECEIPT_FIELDS, "namedReceipt", errors)
    if receipt is None:
        return {"valid": False, "liveReady": False, "errors": errors}
    assignment_result = validate_named_reviewer_assignment(expected_assignment, now=now)
    if not assignment_result["valid"]:
        errors.extend(f"expectedAssignment: {error}" for error in assignment_result["errors"])
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"namedReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in (
        "receiptId",
        "assignmentId",
        "genericAssignmentId",
        "taskId",
        "projectId",
        "repositoryId",
        "packetVersion",
        "planVersion",
        "waveId",
        "role",
        "host",
        "policyFingerprint",
        "sourceFingerprint",
        "namedReviewer",
        "capability",
        "requestedModel",
        "requestedReasoningEffort",
        "executionStatus",
        "coverageStatus",
        "startedAt",
        "finishedAt",
    ):
        _string(receipt.get(key), f"namedReceipt.{key}", errors)
    _string(receipt.get("tenantId"), "namedReceipt.tenantId", errors, allow_null=True)
    for key in (
        "genericAssignmentDigest",
        "taskReviewBindingDigest",
        "authorizationEnvelopeDigest",
        "namedRoleProfileFingerprint",
        "overallDiffFingerprint",
        "shardFingerprint",
        "baseReviewReceiptDigest",
        "hostObservationDigest",
    ):
        _fingerprint(receipt.get(key), f"namedReceipt.{key}", errors)
    if receipt.get("executionStatus") not in EXECUTION_STATUSES:
        errors.append("namedReceipt.executionStatus: unsupported value")
    if receipt.get("coverageStatus") not in COVERAGE_STATUSES:
        errors.append("namedReceipt.coverageStatus: unsupported value")
    for key in ("authenticatedObservation", "promptStarted", "terminal", "live"):
        if not isinstance(receipt.get(key), bool):
            errors.append(f"namedReceipt.{key}: expected boolean")
    _string_array(receipt.get("evidence"), "namedReceipt.evidence", errors)
    _string_array(receipt.get("blockers"), "namedReceipt.blockers", errors)
    started = _timestamp(receipt.get("startedAt"), "namedReceipt.startedAt", errors)
    finished = _timestamp(receipt.get("finishedAt"), "namedReceipt.finishedAt", errors)
    if started is not None and finished is not None and finished < started:
        errors.append("namedReceipt: finishedAt must not precede startedAt")
    if receipt.get("terminal") is not True:
        errors.append("namedReceipt.terminal: must be exactly true")
    if receipt.get("executionStatus") == "completed" and receipt.get("promptStarted") is not True:
        errors.append("namedReceipt.promptStarted: completed execution requires prompt start")
    if receipt.get("coverageStatus") in {"PASS", "FINDINGS"} and not receipt.get("evidence"):
        errors.append("namedReceipt.evidence: successful coverage requires evidence")
    if receipt.get("coverageStatus") in {"PASS", "FINDINGS"}:
        _string(receipt.get("verdict"), "namedReceipt.verdict", errors)
    live_claimed = receipt.get("live") is True or receipt.get("authenticatedObservation") is True
    if live_claimed:
        errors.append(
            "namedReceipt: source package cannot authenticate live host evidence; "
            "a host-owned integration is required"
        )
        for key in (
            "hostObservationId",
            "processIdentity",
            "nonce",
            "observedModel",
            "observedReasoningEffort",
            "verdict",
        ):
            _string(receipt.get(key), f"namedReceipt.{key}", errors)
        if expected_host_observation is None:
            errors.append("namedReceipt: trusted host observation lookup is required for live evidence")
    if expected_host_observation is not None:
        if not isinstance(expected_host_observation, Mapping):
            errors.append("namedReceipt: expected host observation must be an object")
        else:
            expected_observation_digest = artifact_digest(
                "ae-named-reviewer-host-observation-v1", expected_host_observation
            )
            if receipt.get("hostObservationDigest") != expected_observation_digest:
                errors.append("namedReceipt.hostObservationDigest: does not match trusted host observation")
            if receipt.get("hostObservationId") != expected_host_observation.get("observationId"):
                errors.append("namedReceipt.hostObservationId: does not match trusted host observation")

    pairs = {
        "assignmentId": "assignmentId",
        "genericAssignmentId": "genericAssignmentId",
        "genericAssignmentDigest": "genericAssignmentDigest",
        "taskReviewBindingDigest": "taskReviewBindingDigest",
        "authorizationEnvelopeDigest": "authorizationEnvelopeDigest",
        "taskId": "taskId",
        "projectId": "projectId",
        "repositoryId": "repositoryId",
        "tenantId": "tenantId",
        "packetVersion": "packetVersion",
        "planVersion": "planVersion",
        "waveId": "waveId",
        "attempt": "attempt",
        "role": "role",
        "host": "hostId",
        "expiresAt": "expiresAt",
        "policyFingerprint": "policyFingerprint",
        "sourceFingerprint": "sourceFingerprint",
        "scope": "scope",
        "overallDiffFingerprint": "overallDiffFingerprint",
        "shardFingerprint": "shardFingerprint",
        "namedReviewer": "namedReviewer",
        "namedRoleProfileFingerprint": "namedRoleProfileFingerprint",
        "capability": "capability",
        "requestedModel": "configuredModel",
        "requestedReasoningEffort": "configuredReasoningEffort",
    }
    for receipt_key, assignment_key in pairs.items():
        if receipt.get(receipt_key) != expected_assignment.get(assignment_key):
            errors.append(f"namedReceipt.{receipt_key}: does not match trusted assignment")
    if receipt.get("observedModel") != expected_assignment.get("configuredModel") and receipt.get("live") is True:
        errors.append("namedReceipt.observedModel: does not match exact named-role model")
    if receipt.get("observedReasoningEffort") != expected_assignment.get("configuredReasoningEffort") and receipt.get("live") is True:
        errors.append("namedReceipt.observedReasoningEffort: does not match exact named-role effort")
    base_receipt = receipt.get("baseReviewReceipt")
    _validate_base_review_receipt(base_receipt, expected_assignment, errors)
    if isinstance(base_receipt, Mapping):
        expected_digest = artifact_digest("review-receipt-v2", base_receipt)
        if receipt.get("baseReviewReceiptDigest") != expected_digest:
            errors.append("namedReceipt.baseReviewReceiptDigest: does not match base receipt")

    # This source package intentionally has no host trust anchor.  Structural
    # cross-checks are useful diagnostics, but they never become authenticated
    # live authority here.
    live_ready = False
    if require_live and not live_ready:
        errors.append("namedReceipt: authenticated live terminal evidence is required")
        live_ready = False
    return {"valid": not errors, "liveReady": live_ready, "errors": errors}
