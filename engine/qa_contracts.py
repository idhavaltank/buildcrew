#!/usr/bin/env python3
"""Strict, dependency-free contracts for the advisory QA capability.

This module validates planning and evidence data only. It never launches a
browser, emulator, Flutter process, service, or external provider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


SCHEMA_VERSIONS = {
    "request": "qa-request-v1",
    "workflow-map": "qa-workflow-map-v1",
    "execution-packet": "qa-execution-packet-v1",
    "finding": "qa-finding-v1",
    "receipt": "qa-receipt-v1",
    "compatibility-matrix": "compatibility-matrix-v1",
}

PLATFORMS = {"WEB", "MOBILE", "IOS", "FLUTTER", "SHARED"}
RISK_TIERS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
MODES = {"ADVISORY", "REQUIRED"}
WORKFLOW_STATUSES = {"READY", "BLOCKED"}
ADAPTERS = {"source_only", "browser", "android_emulator", "ios_simulator", "flutter", "compatibility"}
RECEIPT_STATUSES = {"PASS", "FAILED", "BLOCKED", "NOT_VERIFIED", "STALE", "MISSING_COVERAGE"}
STEP_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_VERIFIED", "NOT_RUN", "NOT_REQUIRED"}
FINDING_SEVERITIES = {"BLOCKER", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
FINDING_STATUSES = {"OPEN", "CONFIRMED", "NOT_REPRODUCED", "BLOCKED", "RESOLVED"}
COMPARISON_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_VERIFIED", "NOT_REQUIRED", "MISSING_COVERAGE"}
EVIDENCE_KINDS = {"screenshot", "log", "trace", "network", "device", "source", "command", "other"}


def _object(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    return value


def _keys(value: Mapping[str, Any], required: set[str], allowed: set[str], label: str, errors: list[str]) -> None:
    for key in sorted(required - set(value)):
        errors.append(f"{label}: missing required field {key}")
    for key in sorted(set(value) - allowed):
        errors.append(f"{label}: unknown field {key}")


def _string(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _string_list(value: Any, label: str, errors: list[str], *, non_empty: bool = False) -> None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return
    if non_empty and not value:
        errors.append(f"{label}: expected non-empty array")
    for index, item in enumerate(value):
        _string(item, f"{label}[{index}]", errors)


def _enum(value: Any, label: str, choices: set[str], errors: list[str]) -> None:
    if value not in choices:
        errors.append(f"{label}: unsupported value {value!r}")


def _integer(value: Any, label: str, errors: list[str], *, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        errors.append(f"{label}: expected integer >= {minimum}")


def _boolean(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, bool):
        errors.append(f"{label}: expected boolean")


def _timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    _string(value, label, errors)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: expected ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label}: timestamp must include a timezone")
    return parsed


def _time_window(value: Mapping[str, Any], start_key: str, end_key: str, label: str, errors: list[str]) -> None:
    start = _timestamp(value.get(start_key), f"{label}.{start_key}", errors)
    end = _timestamp(value.get(end_key), f"{label}.{end_key}", errors)
    if start is not None and end is not None and end <= start:
        errors.append(f"{label}: {end_key} must be after {start_key}")


def _fingerprint(value: Any, label: str, errors: list[str]) -> None:
    _string(value, label, errors)
    if isinstance(value, str) and value.strip() and len(value.strip()) < 8:
        errors.append(f"{label}: fingerprint is too short")


def _unique_strings(values: list[Any], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for index, value in enumerate(values):
        _string(value, f"{label}[{index}]", errors)
        if isinstance(value, str) and value in seen:
            errors.append(f"{label}: duplicate value {value!r}")
        if isinstance(value, str):
            seen.add(value)


def _validate_request(value: Any) -> list[str]:
    errors: list[str] = []
    request = _object(value, "qaRequest", errors)
    if request is None:
        return errors
    allowed = {
        "schemaVersion", "requestId", "taskId", "repository", "sourceFingerprint",
        "objective", "acceptanceCriteria", "targetPlatforms", "riskTier", "mode",
        "issuedAt", "expiresAt",
    }
    _keys(request, allowed, allowed, "qaRequest", errors)
    if request.get("schemaVersion") != SCHEMA_VERSIONS["request"]:
        errors.append(f"qaRequest.schemaVersion: expected {SCHEMA_VERSIONS['request']}")
    for key in ("requestId", "taskId", "repository", "objective"):
        _string(request.get(key), f"qaRequest.{key}", errors)
    _fingerprint(request.get("sourceFingerprint"), "qaRequest.sourceFingerprint", errors)
    _string_list(request.get("acceptanceCriteria"), "qaRequest.acceptanceCriteria", errors, non_empty=True)
    platforms = request.get("targetPlatforms")
    if not isinstance(platforms, list) or not platforms:
        errors.append("qaRequest.targetPlatforms: expected non-empty array")
    else:
        _unique_strings(platforms, "qaRequest.targetPlatforms", errors)
        for index, platform in enumerate(platforms):
            _enum(platform, f"qaRequest.targetPlatforms[{index}]", PLATFORMS, errors)
    _enum(request.get("riskTier"), "qaRequest.riskTier", RISK_TIERS, errors)
    _enum(request.get("mode"), "qaRequest.mode", MODES, errors)
    _time_window(request, "issuedAt", "expiresAt", "qaRequest", errors)
    return errors


def _validate_workflow_step(value: Any, label: str, errors: list[str]) -> None:
    step = _object(value, label, errors)
    if step is None:
        return
    allowed = {"stepId", "title", "target", "entryPoint", "action", "expected", "evidenceRequired"}
    _keys(step, allowed, allowed, label, errors)
    for key in ("stepId", "title", "entryPoint", "action", "expected"):
        _string(step.get(key), f"{label}.{key}", errors)
    _enum(step.get("target"), f"{label}.target", PLATFORMS, errors)
    _string_list(step.get("evidenceRequired"), f"{label}.evidenceRequired", errors, non_empty=True)


def _validate_workflow_map(value: Any) -> list[str]:
    errors: list[str] = []
    workflow = _object(value, "workflowMap", errors)
    if workflow is None:
        return errors
    allowed = {"schemaVersion", "workflowId", "requestId", "taskId", "sourceFingerprint", "objective", "steps", "unresolved", "status", "generatedAt"}
    _keys(workflow, allowed, allowed, "workflowMap", errors)
    if workflow.get("schemaVersion") != SCHEMA_VERSIONS["workflow-map"]:
        errors.append(f"workflowMap.schemaVersion: expected {SCHEMA_VERSIONS['workflow-map']}")
    for key in ("workflowId", "requestId", "taskId", "objective"):
        _string(workflow.get(key), f"workflowMap.{key}", errors)
    _fingerprint(workflow.get("sourceFingerprint"), "workflowMap.sourceFingerprint", errors)
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("workflowMap.steps: expected non-empty array")
    else:
        step_ids: list[Any] = []
        for index, step in enumerate(steps):
            _validate_workflow_step(step, f"workflowMap.steps[{index}]", errors)
            if isinstance(step, Mapping):
                step_ids.append(step.get("stepId"))
        _unique_strings(step_ids, "workflowMap.steps.stepId", errors)
    _string_list(workflow.get("unresolved"), "workflowMap.unresolved", errors)
    _enum(workflow.get("status"), "workflowMap.status", WORKFLOW_STATUSES, errors)
    if workflow.get("status") == "BLOCKED" and not workflow.get("unresolved"):
        errors.append("workflowMap.unresolved: blocked workflow requires an unresolved reason")
    if workflow.get("status") == "READY" and workflow.get("unresolved"):
        errors.append("workflowMap.unresolved: ready workflow cannot contain unresolved reasons")
    _timestamp(workflow.get("generatedAt"), "workflowMap.generatedAt", errors)
    return errors


def _validate_execution_packet(value: Any) -> list[str]:
    errors: list[str] = []
    packet = _object(value, "executionPacket", errors)
    if packet is None:
        return errors
    allowed = {
        "schemaVersion", "packetId", "requestId", "workflowId", "taskId", "sourceFingerprint",
        "targetAllowlist", "requiredCapabilities", "requiredEvidence", "stepIds", "timeoutSeconds",
        "isolation", "mode", "status", "issuedAt", "expiresAt",
    }
    _keys(packet, allowed, allowed, "executionPacket", errors)
    if packet.get("schemaVersion") != SCHEMA_VERSIONS["execution-packet"]:
        errors.append(f"executionPacket.schemaVersion: expected {SCHEMA_VERSIONS['execution-packet']}")
    for key in ("packetId", "requestId", "workflowId", "taskId"):
        _string(packet.get(key), f"executionPacket.{key}", errors)
    _fingerprint(packet.get("sourceFingerprint"), "executionPacket.sourceFingerprint", errors)

    allowlist = _object(packet.get("targetAllowlist"), "executionPacket.targetAllowlist", errors)
    if allowlist is not None:
        required_allowlist = {"repository", "webTargets", "androidTargets", "testDataScope"}
        allowed_allowlist = required_allowlist | {"iosTargets", "flutterTargets"}
        _keys(allowlist, required_allowlist, allowed_allowlist, "executionPacket.targetAllowlist", errors)
        _string(allowlist.get("repository"), "executionPacket.targetAllowlist.repository", errors)
        for key in ("webTargets", "androidTargets", "testDataScope"):
            _string_list(allowlist.get(key), f"executionPacket.targetAllowlist.{key}", errors)
        for key in ("iosTargets", "flutterTargets"):
            if key in allowlist:
                _string_list(allowlist.get(key), f"executionPacket.targetAllowlist.{key}", errors)

    _string_list(packet.get("requiredCapabilities"), "executionPacket.requiredCapabilities", errors)
    _string_list(packet.get("requiredEvidence"), "executionPacket.requiredEvidence", errors)
    step_ids = packet.get("stepIds")
    if not isinstance(step_ids, list) or not step_ids:
        errors.append("executionPacket.stepIds: expected non-empty array")
    else:
        _unique_strings(step_ids, "executionPacket.stepIds", errors)
    _integer(packet.get("timeoutSeconds"), "executionPacket.timeoutSeconds", errors, minimum=1)

    isolation = _object(packet.get("isolation"), "executionPacket.isolation", errors)
    if isolation is not None:
        allowed_isolation = {"externalState", "network", "credentials"}
        _keys(isolation, allowed_isolation, allowed_isolation, "executionPacket.isolation", errors)
        _enum(isolation.get("externalState"), "executionPacket.isolation.externalState", {"NONE", "DISPOSABLE_ONLY", "EXPLICIT_APPROVAL"}, errors)
        _enum(isolation.get("network"), "executionPacket.isolation.network", {"DISABLED", "ALLOWLISTED", "HOST_CONTROLLED"}, errors)
        _enum(isolation.get("credentials"), "executionPacket.isolation.credentials", {"NONE", "SANDBOXED", "HOST_SUPPLIED"}, errors)
    _enum(packet.get("mode"), "executionPacket.mode", MODES, errors)
    _enum(packet.get("status"), "executionPacket.status", WORKFLOW_STATUSES, errors)
    _time_window(packet, "issuedAt", "expiresAt", "executionPacket", errors)
    return errors


def _validate_finding(value: Any) -> list[str]:
    errors: list[str] = []
    finding = _object(value, "qaFinding", errors)
    if finding is None:
        return errors
    allowed = {
        "schemaVersion", "findingId", "packetId", "taskId", "sourceFingerprint", "severity", "status",
        "title", "observed", "expected", "affectedSteps", "evidenceRefs", "blocking", "createdAt", "residualRisk",
    }
    _keys(finding, allowed, allowed, "qaFinding", errors)
    if finding.get("schemaVersion") != SCHEMA_VERSIONS["finding"]:
        errors.append(f"qaFinding.schemaVersion: expected {SCHEMA_VERSIONS['finding']}")
    for key in ("findingId", "packetId", "taskId", "title", "observed", "expected"):
        _string(finding.get(key), f"qaFinding.{key}", errors)
    _fingerprint(finding.get("sourceFingerprint"), "qaFinding.sourceFingerprint", errors)
    _enum(finding.get("severity"), "qaFinding.severity", FINDING_SEVERITIES, errors)
    _enum(finding.get("status"), "qaFinding.status", FINDING_STATUSES, errors)
    _string_list(finding.get("affectedSteps"), "qaFinding.affectedSteps", errors, non_empty=True)
    _string_list(finding.get("evidenceRefs"), "qaFinding.evidenceRefs", errors)
    _boolean(finding.get("blocking"), "qaFinding.blocking", errors)
    _timestamp(finding.get("createdAt"), "qaFinding.createdAt", errors)
    _string_list(finding.get("residualRisk"), "qaFinding.residualRisk", errors)
    if finding.get("status") == "CONFIRMED" and not finding.get("evidenceRefs"):
        errors.append("qaFinding.evidenceRefs: confirmed finding requires evidence")
    if finding.get("blocking") is True and finding.get("severity") not in {"BLOCKER", "CRITICAL", "HIGH"}:
        errors.append("qaFinding.severity: blocking finding must be HIGH, CRITICAL, or BLOCKER")
    return errors


def _validate_receipt_evidence(value: Any, label: str, errors: list[str]) -> None:
    evidence = _object(value, label, errors)
    if evidence is None:
        return
    allowed = {"evidenceId", "kind", "summary", "contentHash", "ref"}
    _keys(evidence, allowed, allowed, label, errors)
    for key in ("evidenceId", "summary", "contentHash", "ref"):
        _string(evidence.get(key), f"{label}.{key}", errors)
    _enum(evidence.get("kind"), f"{label}.kind", EVIDENCE_KINDS, errors)


def _validate_receipt_step(value: Any, label: str, errors: list[str]) -> None:
    step = _object(value, label, errors)
    if step is None:
        return
    allowed = {"stepId", "status", "evidenceRefs"}
    _keys(step, allowed, allowed, label, errors)
    _string(step.get("stepId"), f"{label}.stepId", errors)
    _enum(step.get("status"), f"{label}.status", STEP_STATUSES, errors)
    _string_list(step.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
    if step.get("status") == "PASS" and not step.get("evidenceRefs"):
        errors.append(f"{label}.evidenceRefs: PASS step requires evidence")


def _validate_blocked_item(value: Any, label: str, errors: list[str]) -> None:
    item = _object(value, label, errors)
    if item is None:
        return
    allowed = {"stepId", "reason"}
    _keys(item, allowed, allowed, label, errors)
    _string(item.get("stepId"), f"{label}.stepId", errors)
    _string(item.get("reason"), f"{label}.reason", errors)


def _validate_receipt(value: Any) -> list[str]:
    errors: list[str] = []
    receipt = _object(value, "qaReceipt", errors)
    if receipt is None:
        return errors
    allowed = {
        "schemaVersion", "receiptId", "packetId", "requestId", "taskId", "sourceFingerprint", "adapter",
        "runtimeIdentity", "startedAt", "finishedAt", "status", "steps", "evidence", "findings",
        "skippedOrBlocked", "sideEffects", "terminal", "residualRisk",
    }
    _keys(receipt, allowed, allowed, "qaReceipt", errors)
    if receipt.get("schemaVersion") != SCHEMA_VERSIONS["receipt"]:
        errors.append(f"qaReceipt.schemaVersion: expected {SCHEMA_VERSIONS['receipt']}")
    for key in ("receiptId", "packetId", "requestId", "taskId"):
        _string(receipt.get(key), f"qaReceipt.{key}", errors)
    _fingerprint(receipt.get("sourceFingerprint"), "qaReceipt.sourceFingerprint", errors)
    _enum(receipt.get("adapter"), "qaReceipt.adapter", ADAPTERS, errors)
    runtime = _object(receipt.get("runtimeIdentity"), "qaReceipt.runtimeIdentity", errors)
    if runtime is not None:
        allowed_runtime = {"target", "version", "build"}
        _keys(runtime, allowed_runtime, allowed_runtime, "qaReceipt.runtimeIdentity", errors)
        for key in sorted(allowed_runtime):
            _string(runtime.get(key), f"qaReceipt.runtimeIdentity.{key}", errors)
    _time_window(receipt, "startedAt", "finishedAt", "qaReceipt", errors)
    _enum(receipt.get("status"), "qaReceipt.status", RECEIPT_STATUSES, errors)

    steps = receipt.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("qaReceipt.steps: expected non-empty array")
    else:
        step_ids: list[Any] = []
        for index, step in enumerate(steps):
            _validate_receipt_step(step, f"qaReceipt.steps[{index}]", errors)
            if isinstance(step, Mapping):
                step_ids.append(step.get("stepId"))
        _unique_strings(step_ids, "qaReceipt.steps.stepId", errors)

    evidence = receipt.get("evidence")
    if not isinstance(evidence, list):
        errors.append("qaReceipt.evidence: expected array")
    else:
        evidence_ids: list[Any] = []
        for index, item in enumerate(evidence):
            _validate_receipt_evidence(item, f"qaReceipt.evidence[{index}]", errors)
            if isinstance(item, Mapping):
                evidence_ids.append(item.get("evidenceId"))
        _unique_strings(evidence_ids, "qaReceipt.evidence.evidenceId", errors)
    _string_list(receipt.get("findings"), "qaReceipt.findings", errors)
    blocked = receipt.get("skippedOrBlocked")
    if not isinstance(blocked, list):
        errors.append("qaReceipt.skippedOrBlocked: expected array")
    else:
        for index, item in enumerate(blocked):
            _validate_blocked_item(item, f"qaReceipt.skippedOrBlocked[{index}]", errors)
    if receipt.get("sideEffects") != []:
        errors.append("qaReceipt.sideEffects: must be an empty array for the contract-only QA layer")
    if receipt.get("terminal") is not True:
        errors.append("qaReceipt.terminal: must be true")
    _string_list(receipt.get("residualRisk"), "qaReceipt.residualRisk", errors)

    if receipt.get("status") == "PASS":
        if not evidence:
            errors.append("qaReceipt.evidence: PASS receipt requires evidence")
        if receipt.get("skippedOrBlocked"):
            errors.append("qaReceipt.skippedOrBlocked: PASS receipt cannot contain skipped or blocked steps")
        step_statuses = {item.get("status") for item in steps if isinstance(item, Mapping)}
        if not step_statuses or not step_statuses.issubset({"PASS", "NOT_REQUIRED"}):
            errors.append("qaReceipt.steps: PASS receipt requires every step to be PASS or NOT_REQUIRED")
    elif not receipt.get("skippedOrBlocked") and not receipt.get("residualRisk"):
        errors.append("qaReceipt: non-PASS receipt requires a blocker or residual-risk explanation")
    return errors


def _validate_matrix_criterion(value: Any, label: str, errors: list[str]) -> None:
    criterion = _object(value, label, errors)
    if criterion is None:
        return
    allowed = {"criterionId", "description", "webStatus", "mobileStatus", "comparisonStatus", "evidenceRefs", "residualRisk"}
    _keys(criterion, allowed, allowed, label, errors)
    for key in ("criterionId", "description"):
        _string(criterion.get(key), f"{label}.{key}", errors)
    for key in ("webStatus", "mobileStatus", "comparisonStatus"):
        _enum(criterion.get(key), f"{label}.{key}", COMPARISON_STATUSES, errors)
    _string_list(criterion.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
    _string_list(criterion.get("residualRisk"), f"{label}.residualRisk", errors)
    if criterion.get("comparisonStatus") == "PASS" and not criterion.get("evidenceRefs"):
        errors.append(f"{label}.evidenceRefs: PASS comparison requires evidence")


def _validate_compatibility_matrix(value: Any) -> list[str]:
    errors: list[str] = []
    matrix = _object(value, "compatibilityMatrix", errors)
    if matrix is None:
        return errors
    allowed = {"schemaVersion", "matrixId", "taskId", "sourceFingerprint", "criteria", "overallStatus", "generatedAt", "sideEffects"}
    _keys(matrix, allowed, allowed, "compatibilityMatrix", errors)
    if matrix.get("schemaVersion") != SCHEMA_VERSIONS["compatibility-matrix"]:
        errors.append(f"compatibilityMatrix.schemaVersion: expected {SCHEMA_VERSIONS['compatibility-matrix']}")
    for key in ("matrixId", "taskId"):
        _string(matrix.get(key), f"compatibilityMatrix.{key}", errors)
    _fingerprint(matrix.get("sourceFingerprint"), "compatibilityMatrix.sourceFingerprint", errors)
    criteria = matrix.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        errors.append("compatibilityMatrix.criteria: expected non-empty array")
    else:
        criterion_ids: list[Any] = []
        for index, item in enumerate(criteria):
            _validate_matrix_criterion(item, f"compatibilityMatrix.criteria[{index}]", errors)
            if isinstance(item, Mapping):
                criterion_ids.append(item.get("criterionId"))
        _unique_strings(criterion_ids, "compatibilityMatrix.criteria.criterionId", errors)
    _enum(matrix.get("overallStatus"), "compatibilityMatrix.overallStatus", COMPARISON_STATUSES, errors)
    _timestamp(matrix.get("generatedAt"), "compatibilityMatrix.generatedAt", errors)
    if matrix.get("sideEffects") != []:
        errors.append("compatibilityMatrix.sideEffects: must be an empty array")
    if matrix.get("overallStatus") == "PASS":
        statuses = {item.get("comparisonStatus") for item in criteria if isinstance(item, Mapping)}
        if not statuses or not statuses.issubset({"PASS", "NOT_REQUIRED"}):
            errors.append("compatibilityMatrix.criteria: PASS matrix requires all criteria to pass or be not required")
    return errors


VALIDATORS = {
    "request": _validate_request,
    "workflow-map": _validate_workflow_map,
    "execution-packet": _validate_execution_packet,
    "finding": _validate_finding,
    "receipt": _validate_receipt,
    "compatibility-matrix": _validate_compatibility_matrix,
}


def validate_qa_contract(kind: str, value: Any) -> dict[str, Any]:
    """Validate one supported QA contract and return portable errors."""
    if kind == "qa-receipt":
        kind = "receipt"
    validator = VALIDATORS.get(kind)
    if validator is None:
        return {"kind": kind, "valid": False, "errors": [f"unsupported QA contract kind: {kind}"]}
    errors = validator(value)
    return {"kind": kind, "valid": not errors, "errors": errors}
