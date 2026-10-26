#!/usr/bin/env python3
"""Bounded source-only bridge for supplemental named-reviewer receipts.

The bridge can exercise assignment and observation contracts, but a
caller-supplied Python object is never authentication authority.  This package
ships no production host authenticator, so receipts minted here are always
non-live and cannot satisfy a named-required closure shard.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .host_dispatch import AssignmentClaimStore, DispatchBoundaryError
from .named_reviewer import (
    RECEIPT_SCHEMA_VERSION,
    artifact_digest,
    validate_named_reviewer_assignment,
    validate_named_reviewer_receipt,
)


OBSERVATION_FIELDS = {
    "observationId",
    "hostId",
    "processIdentity",
    "nonce",
    "promptStarted",
    "observedModel",
    "observedReasoningEffort",
    "executionStatus",
    "coverageStatus",
    "verdict",
    "evidence",
    "blockers",
    "startedAt",
    "finishedAt",
    "terminal",
}


class NamedReviewerDispatchError(ValueError):
    """Raised when a named review wave cannot safely cross the host boundary."""


class NamedReviewerHost(Protocol):
    """Source-contract transport; it is explicitly not host authentication."""

    def preflight(self, assignment: Mapping[str, Any]) -> None:
        """Confirm exact named model/effort support without starting a prompt."""

    def execute(self, assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return one independently observed execution record."""


def _validate_base_receipt(receipt: Any, assignment: Mapping[str, Any]) -> list[str]:
    try:
        from scripts.validate_hybrid_contracts import validate_receipt
    except ImportError as exc:  # pragma: no cover - package import boundary
        return [f"base receipt validator unavailable ({type(exc).__name__})"]
    result = validate_receipt(
        receipt,
        expected_task_id=assignment.get("taskId"),
        expected_fingerprint=assignment.get("sourceFingerprint"),
    )
    errors = list(result.get("errors", []))
    if result.get("completionEligible") is not True:
        errors.append("base receipt is not completion eligible")
    if not isinstance(receipt, Mapping):
        return errors
    fields = {
        "packetVersion": "packetVersion",
        "planVersion": "planVersion",
        "waveId": "waveId",
        "attempt": "attempt",
        "role": "role",
        "host": "hostId",
    }
    for receipt_key, assignment_key in fields.items():
        if receipt.get(receipt_key) != assignment.get(assignment_key):
            errors.append(f"base receipt {receipt_key} does not match named assignment")
    scope = receipt.get("scope")
    assignment_scope = assignment.get("scope")
    if isinstance(scope, Mapping) and isinstance(assignment_scope, Mapping):
        if scope.get("repository") != assignment.get("repositoryId"):
            errors.append("base receipt repository does not match named assignment")
        if scope.get("lane") != assignment_scope.get("scopeId"):
            errors.append("base receipt lane does not match named assignment scope")
    return errors


def _validate_observation(observation: Any, assignment: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(observation, Mapping):
        return ["host observation must be an object"]
    missing = OBSERVATION_FIELDS - set(observation)
    unknown = set(observation) - OBSERVATION_FIELDS
    errors.extend(f"host observation missing {key}" for key in sorted(missing))
    errors.extend(f"host observation has unknown field {key}" for key in sorted(unknown))
    for key in ("observationId", "hostId", "processIdentity", "nonce", "executionStatus", "coverageStatus", "startedAt", "finishedAt"):
        if not isinstance(observation.get(key), str) or not observation.get(key):
            errors.append(f"host observation {key} must be a non-empty string")
    for key in ("promptStarted", "terminal"):
        if not isinstance(observation.get(key), bool):
            errors.append(f"host observation {key} must be boolean")
    for key in ("evidence", "blockers"):
        value = observation.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            errors.append(f"host observation {key} must be a string array")
    if observation.get("hostId") != assignment.get("hostId"):
        errors.append("host observation hostId does not match assignment")
    if observation.get("promptStarted") is not True:
        errors.append("host observation does not prove prompt start")
    if observation.get("terminal") is not True:
        errors.append("host observation is not terminal")
    if observation.get("observedModel") != assignment.get("configuredModel"):
        errors.append("host observation model does not match exact role profile")
    if observation.get("observedReasoningEffort") != assignment.get("configuredReasoningEffort"):
        errors.append("host observation effort does not match exact role profile")
    if observation.get("executionStatus") != "completed":
        errors.append("host observation did not complete")
    if observation.get("coverageStatus") not in {"PASS", "FINDINGS"}:
        errors.append("host observation lacks complete review coverage")
    if not observation.get("evidence"):
        errors.append("host observation lacks evidence references")
    if not isinstance(observation.get("verdict"), str) or not observation.get("verdict"):
        errors.append("host observation lacks a terminal verdict")
    return errors


def preflight_named_review_wave(
    assignments: Sequence[Mapping[str, Any]],
    *,
    base_review_receipts: Mapping[str, Mapping[str, Any]],
    host: NamedReviewerHost,
    trusted_assignments: Mapping[str, Mapping[str, Any]],
    now: str | None = None,
) -> list[Mapping[str, Any]]:
    """Validate the complete wave before any claim or prompt is attempted."""

    if not assignments:
        raise NamedReviewerDispatchError("named review wave must not be empty")
    prepared: list[Mapping[str, Any]] = []
    identities: set[str] = set()
    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, Mapping):
            raise NamedReviewerDispatchError(f"assignment[{index}] must be an object")
        assignment_id = assignment.get("assignmentId")
        if not isinstance(assignment_id, str) or not assignment_id or assignment_id in identities:
            raise NamedReviewerDispatchError("named review wave contains a missing or duplicate assignment identity")
        identities.add(assignment_id)
        trusted = trusted_assignments.get(assignment_id)
        if not isinstance(trusted, Mapping):
            raise NamedReviewerDispatchError("named review wave lacks a trusted controller assignment")
        if artifact_digest("ae-named-reviewer-assignment-v1", assignment) != artifact_digest(
            "ae-named-reviewer-assignment-v1", trusted
        ):
            raise NamedReviewerDispatchError("named assignment differs from trusted controller lookup")
        result = validate_named_reviewer_assignment(assignment, now=now)
        if not result["valid"] or not result["dispatchable"]:
            raise NamedReviewerDispatchError("named assignment failed contract/freshness preflight")
        receipt = base_review_receipts.get(assignment_id)
        base_errors = _validate_base_receipt(receipt, assignment)
        if base_errors:
            raise NamedReviewerDispatchError("base review receipt failed preflight: " + "; ".join(base_errors))
        try:
            host.preflight(assignment)
        except Exception as exc:
            if isinstance(exc, NamedReviewerDispatchError):
                raise
            raise NamedReviewerDispatchError(f"host preflight failed ({type(exc).__name__})") from exc
        prepared.append(assignment)
    return prepared


def _build_receipt(
    assignment: Mapping[str, Any],
    base_receipt: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    live: bool,
) -> dict[str, Any]:
    seed = {
        "assignmentId": assignment["assignmentId"],
        "observationId": observation["observationId"],
        "baseReviewReceiptDigest": artifact_digest("review-receipt-v2", base_receipt),
    }
    receipt_id = "named-receipt-" + artifact_digest(RECEIPT_SCHEMA_VERSION, seed).split(":", 1)[1][:40]
    return {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "receiptId": receipt_id,
        "assignmentId": assignment["assignmentId"],
        "genericAssignmentId": assignment["genericAssignmentId"],
        "genericAssignmentDigest": assignment["genericAssignmentDigest"],
        "taskReviewBindingDigest": assignment["taskReviewBindingDigest"],
        "authorizationEnvelopeDigest": assignment["authorizationEnvelopeDigest"],
        "taskId": assignment["taskId"],
        "projectId": assignment["projectId"],
        "repositoryId": assignment["repositoryId"],
        "tenantId": assignment.get("tenantId"),
        "packetVersion": assignment["packetVersion"],
        "planVersion": assignment["planVersion"],
        "waveId": assignment["waveId"],
        "attempt": assignment["attempt"],
        "role": assignment["role"],
        "host": assignment["hostId"],
        "expiresAt": assignment["expiresAt"],
        "policyFingerprint": assignment["policyFingerprint"],
        "sourceFingerprint": assignment["sourceFingerprint"],
        "scope": dict(assignment["scope"]),
        "overallDiffFingerprint": assignment["overallDiffFingerprint"],
        "shardFingerprint": assignment["shardFingerprint"],
        "namedReviewer": assignment["namedReviewer"],
        "namedRoleProfileFingerprint": assignment["namedRoleProfileFingerprint"],
        "capability": assignment["capability"],
        "requestedModel": assignment["configuredModel"],
        "requestedReasoningEffort": assignment["configuredReasoningEffort"],
        "observedModel": observation.get("observedModel"),
        "observedReasoningEffort": observation.get("observedReasoningEffort"),
        "hostObservationId": observation.get("observationId"),
        "hostObservationDigest": artifact_digest(
            "ae-named-reviewer-host-observation-v1", observation
        ),
        "processIdentity": observation.get("processIdentity"),
        "nonce": observation.get("nonce"),
        "authenticatedObservation": False,
        "promptStarted": observation.get("promptStarted") is True,
        "terminal": observation.get("terminal") is True,
        "live": live,
        "executionStatus": observation.get("executionStatus"),
        "coverageStatus": observation.get("coverageStatus"),
        "verdict": observation.get("verdict"),
        "evidence": list(observation.get("evidence", [])),
        "blockers": list(observation.get("blockers", [])),
        "baseReviewReceiptDigest": artifact_digest("review-receipt-v2", base_receipt),
        "baseReviewReceipt": dict(base_receipt),
        "startedAt": observation.get("startedAt"),
        "finishedAt": observation.get("finishedAt"),
    }


def _build_failure_record(
    assignment: Mapping[str, Any],
    *,
    failure_stage: str,
    execution_status: str,
    prompt_start_status: str,
    error_code: str,
    blockers: Sequence[str],
    evidence: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a bounded terminal record for claimed work lacking coverage."""

    seed = {
        "assignmentId": assignment["assignmentId"],
        "failureStage": failure_stage,
        "executionStatus": execution_status,
        "promptStartStatus": prompt_start_status,
        "errorCode": error_code,
    }
    return {
        "recordVersion": "ae-named-reviewer-failure-v1",
        "recordId": "named-failure-"
        + artifact_digest("ae-named-reviewer-failure-v1", seed).split(":", 1)[1][:40],
        "assignmentId": assignment["assignmentId"],
        "taskId": assignment["taskId"],
        "projectId": assignment["projectId"],
        "repositoryId": assignment["repositoryId"],
        "tenantId": assignment.get("tenantId"),
        "sourceFingerprint": assignment["sourceFingerprint"],
        "overallDiffFingerprint": assignment["overallDiffFingerprint"],
        "shardFingerprint": assignment["shardFingerprint"],
        "failureStage": failure_stage,
        "executionStatus": execution_status,
        "coverageStatus": "MISSING_COVERAGE",
        "promptStartStatus": prompt_start_status,
        "errorCode": error_code,
        "evidence": list(evidence),
        "blockers": list(blockers),
        "terminal": True,
        "completionEligible": False,
    }


def _append_unstarted_failure_records(
    failures: list[dict[str, Any]],
    remaining: Sequence[Mapping[str, Any]],
) -> None:
    for assignment in remaining:
        failures.append(
            _build_failure_record(
                assignment,
                failure_stage="wave_aborted",
                execution_status="pre_prompt_failure",
                prompt_start_status="NOT_STARTED",
                error_code="PRIOR_ASSIGNMENT_FAILED",
                blockers=["A prior assignment failed after the wave was claimed."],
            )
        )


def dispatch_named_review_wave(
    assignments: Sequence[Mapping[str, Any]],
    *,
    base_review_receipts: Mapping[str, Mapping[str, Any]],
    host: NamedReviewerHost,
    claim_store: AssignmentClaimStore,
    trusted_assignments: Mapping[str, Mapping[str, Any]],
    now: str | None = None,
) -> dict[str, Any]:
    """Preflight, atomically claim, execute, and validate one named-review wave."""

    prepared = preflight_named_review_wave(
        assignments,
        base_review_receipts=base_review_receipts,
        host=host,
        trusted_assignments=trusted_assignments,
        now=now,
    )
    claim_ids = [assignment["assignmentId"] for assignment in prepared]
    try:
        claim_store.claim(claim_ids)
    except (DispatchBoundaryError, ValueError) as exc:
        raise NamedReviewerDispatchError("named assignment replay was rejected") from exc

    # A transport object supplied by the caller cannot authenticate itself.
    # Live host receipts must be minted by a future host-owned integration
    # outside this source-only package.
    live = False
    receipts: list[dict[str, Any]] = []
    failure_records: list[dict[str, Any]] = []
    for index, assignment in enumerate(prepared):
        try:
            observation = host.execute(assignment)
        except Exception as exc:
            failure_records.append(
                _build_failure_record(
                    assignment,
                    failure_stage="host_execute",
                    execution_status="failed",
                    prompt_start_status="UNKNOWN",
                    error_code=f"HOST_EXECUTE_{type(exc).__name__.upper()}",
                    blockers=[f"Host execution failed with {type(exc).__name__}."],
                )
            )
            _append_unstarted_failure_records(failure_records, prepared[index + 1 :])
            break
        observation_errors = _validate_observation(observation, assignment)
        if observation_errors:
            prompt_status = "STARTED" if observation.get("promptStarted") is True else "UNKNOWN"
            partial_evidence = observation.get("evidence")
            if not isinstance(partial_evidence, list) or any(
                not isinstance(item, str) or not item for item in partial_evidence
            ):
                partial_evidence = []
            failure_records.append(
                _build_failure_record(
                    assignment,
                    failure_stage="observation_validation",
                    execution_status="failed",
                    prompt_start_status=prompt_status,
                    error_code="HOST_OBSERVATION_INVALID",
                    blockers=observation_errors,
                    evidence=partial_evidence,
                )
            )
            _append_unstarted_failure_records(failure_records, prepared[index + 1 :])
            break
        base_receipt = base_review_receipts[assignment["assignmentId"]]
        receipt = _build_receipt(assignment, base_receipt, observation, live=False)
        result = validate_named_reviewer_receipt(
            receipt,
            expected_assignment=assignment,
            expected_host_observation=observation,
            now=now,
            require_live=live,
        )
        if not result["valid"]:
            failure_records.append(
                _build_failure_record(
                    assignment,
                    failure_stage="receipt_validation",
                    execution_status="failed",
                    prompt_start_status="STARTED",
                    error_code="NAMED_RECEIPT_INVALID",
                    blockers=result["errors"],
                    evidence=observation.get("evidence", []),
                )
            )
            _append_unstarted_failure_records(failure_records, prepared[index + 1 :])
            break
        receipts.append(receipt)
    source_contract_complete = not failure_records and len(receipts) == len(prepared)
    return {
        "status": "CAPABILITY_UNAVAILABLE" if source_contract_complete else "INCOMPLETE",
        "live": False,
        "assignmentCount": len(prepared),
        "terminalResultCount": len(receipts) + len(failure_records),
        "sourceContractComplete": source_contract_complete,
        "completionEligible": False,
        "receipts": receipts,
        "failureRecords": failure_records,
    }
