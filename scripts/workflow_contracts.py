#!/usr/bin/env python3
"""Dependency-free issue, dispatch, gate, and receipt-binding contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from lifecycle_state import can_transition, _mint_validated_closure_evidence
from validate_hybrid_contracts import validate_receipt

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.evidence_freshness import validate_fresh_evidence  # noqa: E402
from engine.flutter_execution_trace import validate_flutter_execution_trace  # noqa: E402
from engine.model_selection import validate_assignment  # noqa: E402
from engine.named_reviewer import (  # noqa: E402
    validate_authorization_evidence_envelope,
    validate_named_reviewer_assignment,
    validate_named_reviewer_receipt,
    validate_task_review_binding,
)
from engine.review_contracts import (  # noqa: E402
    validate_finding_ledger,
    validate_review_shard_manifest,
    validate_source_contract_closure,
)
from engine.session_handoff import validate_handoff_packet  # noqa: E402


ISSUE_REQUIRED = {
    "issueVersion",
    "taskId",
    "symptom",
    "reproduction",
    "affectedLayers",
    "hypotheses",
    "evidence",
    "rootCause",
    "regressionRisk",
    "acceptanceMapping",
}
ISSUE_ALLOWED = ISSUE_REQUIRED
DIAGNOSIS_REQUIRED = {
    "packetVersion",
    "taskId",
    "scope",
    "sourceFingerprint",
    "checkpoint",
    "expectedOutput",
    "timeoutSeconds",
    "stopConditions",
}
DIAGNOSIS_ALLOWED = DIAGNOSIS_REQUIRED
ASSIGNMENT_ROLES = {
    "knowledge",
    "explorer",
    "root-cause",
    "regression",
    "reviewer",
    "testing",
    "verification",
    "writer",
}
GATE_NAMES = {
    "static",
    "review",
    "runtime",
    "device",
    "publication",
    "activation",
    "learning",
}
GATE_STATES = {
    "PASS",
    "FAIL",
    "BLOCKED",
    "MISSING_COVERAGE",
    "UNVERIFIED",
    "NOT_REQUIRED",
    "NOT_STARTED",
    "SOURCE_ONLY",
    "INSTALLED_NOT_ACTIVE",
    "ACTIVE",
}


def _errors_for_keys(value: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    errors = [f"{label}: missing required field {key}" for key in sorted(required - set(value))]
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - required))
    return errors


def _non_empty_string(value: Any, label: str) -> list[str]:
    return [] if isinstance(value, str) and value.strip() else [f"{label}: expected non-empty string"]


def _list(value: Any, label: str) -> list[str]:
    return [] if isinstance(value, list) else [f"{label}: expected array"]


def validate_issue_record(issue: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(issue, dict):
        return {"valid": False, "errors": ["issue: expected object"]}
    errors.extend(_errors_for_keys(issue, ISSUE_REQUIRED, "issue"))
    if issue.get("issueVersion") != "issue-record-v1":
        errors.append("issue.issueVersion: expected issue-record-v1")
    errors.extend(_non_empty_string(issue.get("taskId"), "issue.taskId"))
    for key in ("symptom", "reproduction", "regressionRisk"):
        errors.extend(_non_empty_string(issue.get(key), f"issue.{key}"))
    for key in ("affectedLayers", "hypotheses", "evidence", "acceptanceMapping"):
        errors.extend(_list(issue.get(key), f"issue.{key}"))
    root_cause = issue.get("rootCause")
    if root_cause is not None:
        if not isinstance(root_cause, dict):
            errors.append("issue.rootCause: expected object or null")
        else:
            errors.extend(_errors_for_keys(root_cause, {"status", "claim", "evidence"}, "issue.rootCause"))
            if root_cause.get("status") not in {"unconfirmed", "confirmed"}:
                errors.append("issue.rootCause.status: expected unconfirmed or confirmed")
            errors.extend(_non_empty_string(root_cause.get("claim"), "issue.rootCause.claim"))
            errors.extend(_list(root_cause.get("evidence"), "issue.rootCause.evidence"))
            if root_cause.get("status") == "confirmed" and not root_cause.get("evidence"):
                errors.append("issue.rootCause: confirmed cause requires evidence")
    return {"valid": not errors, "errors": errors}


def validate_diagnosis_packet(packet: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(packet, dict):
        return {"valid": False, "errors": ["diagnosis: expected object"]}
    errors.extend(_errors_for_keys(packet, DIAGNOSIS_REQUIRED, "diagnosis"))
    if packet.get("packetVersion") != "diagnosis-packet-v1":
        errors.append("diagnosis.packetVersion: expected diagnosis-packet-v1")
    for key in ("taskId", "sourceFingerprint"):
        errors.extend(_non_empty_string(packet.get(key), f"diagnosis.{key}"))
    scope = packet.get("scope")
    if not isinstance(scope, dict):
        errors.append("diagnosis.scope: expected object")
    else:
        errors.extend(_errors_for_keys(scope, {"repository", "lane", "files"}, "diagnosis.scope"))
        errors.extend(_non_empty_string(scope.get("repository"), "diagnosis.scope.repository"))
        errors.extend(_non_empty_string(scope.get("lane"), "diagnosis.scope.lane"))
        errors.extend(_list(scope.get("files"), "diagnosis.scope.files"))
    checkpoint = packet.get("checkpoint")
    if not isinstance(checkpoint, dict):
        errors.append("diagnosis.checkpoint: expected object")
    else:
        errors.extend(_errors_for_keys(checkpoint, {"phase", "lastCommand", "nextAction"}, "diagnosis.checkpoint"))
        for key in ("phase", "lastCommand", "nextAction"):
            errors.extend(_non_empty_string(checkpoint.get(key), f"diagnosis.checkpoint.{key}"))
    errors.extend(_list(packet.get("expectedOutput"), "diagnosis.expectedOutput"))
    errors.extend(_list(packet.get("stopConditions"), "diagnosis.stopConditions"))
    timeout = packet.get("timeoutSeconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        errors.append("diagnosis.timeoutSeconds: expected positive integer")
    return {"valid": not errors, "errors": errors}


def validate_parallel_assignments(assignments: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(assignments, list) or not assignments:
        return {"valid": False, "errors": ["assignments: expected non-empty array"]}
    seen_ids: set[str] = set()
    seen_files: dict[str, str] = {}
    writers: list[str] = []
    for assignment in assignments:
        if not isinstance(assignment, dict):
            errors.append("assignment: expected object")
            continue
        errors.extend(_errors_for_keys(
            assignment,
            {"assignmentId", "role", "files", "writer"},
            "assignment",
        ))
        assignment_id = assignment.get("assignmentId")
        if not isinstance(assignment_id, str) or not assignment_id.strip():
            errors.append("assignment.assignmentId: expected non-empty string")
            assignment_id = f"assignment-{len(seen_ids)}"
        if assignment_id in seen_ids:
            errors.append(f"assignment.assignmentId: duplicate {assignment_id}")
        seen_ids.add(assignment_id)
        role = assignment.get("role")
        if role not in ASSIGNMENT_ROLES:
            errors.append(f"assignment.role: unsupported value {role!r}")
        files = assignment.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(item, str) and item.strip() for item in files):
            errors.append(f"assignment.files: expected non-empty string array for {assignment_id}")
            files = []
        for file_path in files:
            if file_path in seen_files:
                errors.append(
                    f"assignment.files: overlapping writer scope {file_path} "
                    f"({seen_files[file_path]} and {assignment_id})"
                )
            else:
                seen_files[file_path] = assignment_id
        if assignment.get("writer") is True:
            writers.append(assignment_id)
        elif assignment.get("writer") is not False:
            errors.append(f"assignment.writer: expected boolean for {assignment_id}")
    if len(writers) > 1:
        errors.append("assignments: only one writer may own an overlapping mutation wave")
    return {"valid": not errors, "errors": errors, "writerOwners": writers}


def validate_gate_set(gates: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(gates, dict):
        return {"valid": False, "closureEligible": False, "errors": ["gates: expected object"]}
    errors.extend(_errors_for_keys(gates, GATE_NAMES, "gates"))
    for name in sorted(GATE_NAMES):
        if gates.get(name) not in GATE_STATES:
            errors.append(f"gates.{name}: unsupported state {gates.get(name)!r}")
    return {
        "valid": not errors,
        "closureEligible": False,
        "evidenceRequired": True,
        "errors": errors,
    }


def validate_receipt_binding(receipt: Any, dispatch: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(dispatch, dict):
        return {"valid": False, "errors": ["dispatch: expected object"]}
    base = validate_receipt(
        receipt,
        expected_task_id=dispatch.get("taskId"),
        expected_fingerprint=dispatch.get("sourceFingerprint"),
    )
    errors.extend(base.get("errors", []))
    for field in ("packetVersion", "planVersion", "waveId", "attempt", "role", "host", "writerOwner"):
        if receipt.get(field) != dispatch.get(field):
            errors.append(f"receipt.{field}: does not match dispatch binding")
    scope = receipt.get("scope")
    if isinstance(scope, dict) and scope.get("lane") != dispatch.get("lane"):
        errors.append("receipt.scope.lane: does not match dispatch binding")
    if dispatch.get("modelAssignment") is not None or (
        isinstance(receipt, dict) and receipt.get("modelAssignment") is not None
    ):
        model_binding = validate_model_assignment_binding(receipt, dispatch)
        errors.extend(model_binding["errors"])
    if receipt.get("status") == "completed" and not receipt.get("terminal"):
        errors.append("receipt: completed status requires terminal evidence")
    return {"valid": not errors, "errors": errors, "completionEligible": not errors and base.get("completionEligible", False)}


def _model_record(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get("modelAssignment")
    if isinstance(nested, Mapping):
        return nested
    nested = value.get("assignment")
    if isinstance(nested, Mapping):
        return nested
    return value


def validate_model_assignment_binding(receipt: Any, dispatch: Mapping[str, Any]) -> dict[str, Any]:
    """Require fresh, identity-bound model evidence when a vNext assignment exists."""

    expected = _model_record(dispatch.get("modelAssignment")) or dispatch
    actual = _model_record(receipt)
    if actual is None:
        return {"valid": False, "errors": ["receipt.modelAssignment: expected object"]}

    errors: list[str] = []
    for field in (
        "taskId",
        "projectId",
        "tenantId",
        "repositoryId",
        "packetVersion",
        "planVersion",
        "waveId",
        "attempt",
        "sourceFingerprint",
        "diffFingerprint",
        "policyFingerprint",
        "hostId",
        "host",
        "role",
        "scope",
        "desiredModel",
        "resolvedModel",
        "reasoningEffort",
        "selectionStatus",
        "issuedAt",
        "expiresAt",
    ):
        if field in expected and actual.get(field) != expected.get(field):
            errors.append(f"receipt.modelAssignment.{field}: does not match dispatch binding")

    required = ("taskId", "sourceFingerprint", "policyFingerprint", "role", "issuedAt", "expiresAt")
    for field in required:
        if field not in actual:
            errors.append(f"receipt.modelAssignment: missing required field {field}")
    freshness = validate_fresh_evidence(actual, expected, label="receipt.modelAssignment")
    errors.extend(freshness["errors"])
    if actual.get("selectionStatus") in {"INVALIDATED", "REJECTED"}:
        errors.append("receipt.modelAssignment: terminal assignment is not reusable")
    return {"valid": not errors, "errors": errors, "status": freshness["status"]}


def validate_transition_for_closure(
    current: str,
    target: str,
    closure_bundle: Any = None,
    *,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_invalidation_plans: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    closure = (
        validate_closure_bundle(
            closure_bundle,
            trusted_risk_authorizations=trusted_risk_authorizations,
            trusted_resolution_receipts=trusted_resolution_receipts,
            trusted_invalidation_plans=trusted_invalidation_plans,
            now=now,
        )
        if target == "CLOSED"
        else {"valid": True, "closureEligible": False, "closureEvidence": None, "errors": []}
    )
    try:
        allowed = can_transition(
            current,
            target,
            closure_evidence=closure.get("closureEvidence"),
        )
    except ValueError as exc:
        return {"valid": False, "errors": [str(exc)]}
    errors = list(closure.get("errors", []))
    if not allowed:
        errors.append(f"{current} -> {target}: illegal transition")
    return {
        "valid": allowed and closure.get("valid") is True,
        "closureEligible": allowed and closure.get("closureEligible") is True,
        "errors": errors,
    }


def validate_named_review_bundle(bundle: Any, *, now: str | None = None, require_live: bool = False) -> dict[str, Any]:
    """Cross-bind generic authority, named assignment, and both receipts."""

    fields = {
        "genericAssignment",
        "taskReviewBinding",
        "authorizationEnvelope",
        "roleProfile",
        "namedAssignment",
        "namedReceipt",
        "hostObservation",
    }
    if not isinstance(bundle, Mapping):
        return {"valid": False, "liveReady": False, "errors": ["namedReviewBundle: expected object"]}
    errors = _errors_for_keys(bundle, fields, "namedReviewBundle")
    generic = bundle.get("genericAssignment")
    binding = bundle.get("taskReviewBinding")
    envelope = bundle.get("authorizationEnvelope")
    profile = bundle.get("roleProfile")
    named = bundle.get("namedAssignment")
    receipt = bundle.get("namedReceipt")
    host_observation = bundle.get("hostObservation")
    generic_result = validate_assignment(generic, now=now)
    errors.extend(f"genericAssignment: {error}" for error in generic_result.get("errors", []))
    binding_result = validate_task_review_binding(binding, now=now)
    errors.extend(f"taskReviewBinding: {error}" for error in binding_result.get("errors", []))
    envelope_result = validate_authorization_evidence_envelope(
        envelope,
        wrapped_artifact=generic if isinstance(generic, Mapping) else None,
        task_review_binding=binding if isinstance(binding, Mapping) else None,
        now=now,
    )
    errors.extend(f"authorizationEnvelope: {error}" for error in envelope_result.get("errors", []))
    named_result = validate_named_reviewer_assignment(
        named,
        expected_generic_assignment=generic if isinstance(generic, Mapping) else None,
        expected_task_review_binding=binding if isinstance(binding, Mapping) else None,
        expected_authorization_envelope=envelope if isinstance(envelope, Mapping) else None,
        expected_role_profile=profile if isinstance(profile, Mapping) else None,
        now=now,
    )
    errors.extend(f"namedAssignment: {error}" for error in named_result.get("errors", []))
    receipt_result = validate_named_reviewer_receipt(
        receipt,
        expected_assignment=named if isinstance(named, Mapping) else {},
        expected_host_observation=host_observation if isinstance(host_observation, Mapping) else None,
        now=now,
        require_live=require_live,
    )
    errors.extend(f"namedReceipt: {error}" for error in receipt_result.get("errors", []))
    return {
        "valid": not errors,
        "liveReady": not errors and receipt_result.get("liveReady") is True,
        "errors": errors,
    }


def validate_review_artifacts(
    bundle: Any,
    *,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_invalidation_plans: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate review artifacts without accepting authority from the bundle.

    The three trusted stores are host/controller inputs.  They deliberately
    travel through keyword-only parameters so a review artifact cannot carry
    and bless its own authorization, resolution, or reuse evidence.
    """

    fields = {"manifest", "findingLedger", "shardResults"}
    if not isinstance(bundle, Mapping):
        return {"valid": False, "closureEligible": False, "errors": ["reviewArtifacts: expected object"]}
    errors = _errors_for_keys(bundle, fields, "reviewArtifacts")
    manifest = bundle.get("manifest")
    ledger = bundle.get("findingLedger")
    manifest_result = validate_review_shard_manifest(manifest)
    ledger_result = validate_finding_ledger(
        ledger,
        trusted_risk_authorizations=trusted_risk_authorizations,
        trusted_resolution_receipts=trusted_resolution_receipts,
        now=now,
    )
    errors.extend(f"manifest: {error}" for error in manifest_result.get("errors", []))
    errors.extend(f"findingLedger: {error}" for error in ledger_result.get("errors", []))
    closure = validate_source_contract_closure(
        manifest if isinstance(manifest, Mapping) else {},
        bundle.get("shardResults") if isinstance(bundle.get("shardResults"), Mapping) else {},
        ledger if isinstance(ledger, Mapping) else {},
        trusted_risk_authorizations=trusted_risk_authorizations,
        trusted_resolution_receipts=trusted_resolution_receipts,
        trusted_invalidation_plans=trusted_invalidation_plans,
        now=now,
    )
    errors.extend(f"closure: {error}" for error in closure.get("errors", []))
    return {"valid": not errors, "closureEligible": not errors and closure.get("closureEligible") is True, "errors": errors}


def _closure_digest(domain: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(f"{domain}\0".encode("utf-8") + encoded).hexdigest()


def validate_closure_bundle(
    bundle: Any,
    *,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_invalidation_plans: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate artifacts and mint the only lifecycle-closing evidence type."""

    fields = {"taskId", "sourceFingerprint", "gates", "gateEvidence", "reviewArtifacts"}
    if not isinstance(bundle, Mapping):
        return {
            "valid": False,
            "closureEligible": False,
            "closureEvidence": None,
            "errors": ["closureBundle: expected object"],
        }
    errors = _errors_for_keys(bundle, fields, "closureBundle")
    task_id = bundle.get("taskId")
    source_fingerprint = bundle.get("sourceFingerprint")
    if not isinstance(task_id, str) or not task_id:
        errors.append("closureBundle.taskId: expected non-empty string")
    if not isinstance(source_fingerprint, str) or not source_fingerprint:
        errors.append("closureBundle.sourceFingerprint: expected non-empty string")

    gates = bundle.get("gates")
    gate_result = validate_gate_set(gates)
    errors.extend(f"closureBundle.{error}" for error in gate_result.get("errors", []))
    review_artifacts = bundle.get("reviewArtifacts")
    review_result = validate_review_artifacts(
        review_artifacts,
        trusted_risk_authorizations=trusted_risk_authorizations,
        trusted_resolution_receipts=trusted_resolution_receipts,
        trusted_invalidation_plans=trusted_invalidation_plans,
        now=now,
    )
    errors.extend(
        f"closureBundle.reviewArtifacts: {error}"
        for error in review_result.get("errors", [])
    )
    manifest = review_artifacts.get("manifest") if isinstance(review_artifacts, Mapping) else {}
    if isinstance(manifest, Mapping):
        if manifest.get("taskId") != task_id:
            errors.append("closureBundle.taskId: does not match review manifest")
        if manifest.get("sourceFingerprint") != source_fingerprint:
            errors.append("closureBundle.sourceFingerprint: does not match review manifest")
    if not isinstance(gates, Mapping) or gates.get("review") != "PASS":
        errors.append("closureBundle.gates.review: validated review must be PASS")
    if review_result.get("closureEligible") is not True:
        errors.append("closureBundle.reviewArtifacts: review closure is not eligible")

    gate_evidence = bundle.get("gateEvidence")
    if not isinstance(gate_evidence, Mapping):
        errors.append("closureBundle.gateEvidence: expected object")
        gate_evidence = {}
    else:
        errors.extend(
            f"closureBundle.gateEvidence: missing required gate {name}"
            for name in sorted(GATE_NAMES - set(gate_evidence))
        )
        errors.extend(
            f"closureBundle.gateEvidence: unknown gate {name}"
            for name in sorted(set(gate_evidence) - GATE_NAMES)
        )

    evidence_fields = {
        "status",
        "evidenceId",
        "artifact",
        "artifactDigest",
        "terminal",
        "notRequiredReason",
    }
    for name in sorted(GATE_NAMES):
        evidence = gate_evidence.get(name)
        label = f"closureBundle.gateEvidence.{name}"
        if not isinstance(evidence, Mapping):
            errors.append(f"{label}: expected object")
            continue
        errors.extend(f"{label}: missing required field {key}" for key in sorted(evidence_fields - set(evidence)))
        errors.extend(f"{label}: unknown field {key}" for key in sorted(set(evidence) - evidence_fields))
        status = gates.get(name) if isinstance(gates, Mapping) else None
        if evidence.get("status") != status:
            errors.append(f"{label}.status: does not match gate status")
        if status not in {"PASS", "NOT_REQUIRED"}:
            errors.append(f"{label}.status: closure requires PASS or NOT_REQUIRED")
        if evidence.get("terminal") is not True:
            errors.append(f"{label}.terminal: must be exactly true")
        artifact = evidence.get("artifact")
        if not isinstance(artifact, Mapping):
            errors.append(f"{label}.artifact: expected object")
            continue
        expected_digest = _closure_digest("closure-gate-artifact-v1", artifact)
        if evidence.get("artifactDigest") != expected_digest:
            errors.append(f"{label}.artifactDigest: does not match artifact")
        seed = {
            "gate": name,
            "status": status,
            "artifactDigest": evidence.get("artifactDigest"),
            "terminal": evidence.get("terminal"),
            "notRequiredReason": evidence.get("notRequiredReason"),
        }
        expected_id = "gate-evidence-" + _closure_digest(
            "closure-gate-evidence-v1", seed
        ).split(":", 1)[1][:40]
        if evidence.get("evidenceId") != expected_id:
            errors.append(f"{label}.evidenceId: does not match content")
        if status == "PASS":
            if evidence.get("notRequiredReason") is not None:
                errors.append(f"{label}.notRequiredReason: PASS gate requires null")
            if name != "review" and (
                artifact.get("status") != "PASS" or artifact.get("terminal") is not True
            ):
                errors.append(f"{label}.artifact: PASS evidence must be terminal")
        elif status == "NOT_REQUIRED":
            reason = evidence.get("notRequiredReason")
            if not isinstance(reason, str) or not reason:
                errors.append(f"{label}.notRequiredReason: explanation is required")
            if artifact.get("status") != "NOT_REQUIRED" or artifact.get("terminal") is not True:
                errors.append(f"{label}.artifact: NOT_REQUIRED decision must be terminal")
        if name == "review":
            expected_review_digest = _closure_digest(
                "closure-gate-artifact-v1", review_artifacts
            )
            if evidence.get("artifactDigest") != expected_review_digest:
                errors.append(f"{label}: must bind the validated review artifact bundle")

    if errors:
        return {
            "valid": False,
            "closureEligible": False,
            "closureEvidence": None,
            "errors": errors,
        }
    evidence_digest = _closure_digest("lifecycle-closure-evidence-v1", bundle)
    closure_evidence = _mint_validated_closure_evidence(
        task_id=task_id,
        source_fingerprint=source_fingerprint,
        evidence_digest=evidence_digest,
        gate_names=tuple(sorted(GATE_NAMES)),
    )
    return {
        "valid": True,
        "closureEligible": True,
        "closureEvidence": closure_evidence,
        "evidenceDigest": evidence_digest,
        "errors": [],
    }


def validate_session_handoff_contract(packet: Any, *, now: str | None = None) -> dict[str, Any]:
    """Expose the authority-free handoff validator at the central seam."""

    result = validate_handoff_packet(packet, now=now)
    return {**result, "authorityTransferred": False}


def validate_flutter_trace_contract(
    trace: Any,
    mobile_decision: Mapping[str, Any],
    trusted_change_facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose static Flutter evidence without satisfying runtime/device gates."""

    result = validate_flutter_execution_trace(trace, mobile_decision, trusted_change_facts)
    return {**result, "runtimeEvidenceSatisfied": False, "deviceEvidenceSatisfied": False}
