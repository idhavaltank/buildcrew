#!/usr/bin/env python3
"""Report-only project-context and policy sidecar binding."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .context_receipts import validate_context_map, validate_policy_bundle, validate_receipt


SCHEMA_VERSION = "context-shadow-binding-v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _binding_errors(intake: Mapping[str, Any], context_map: Mapping[str, Any], receipt: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("taskId", "repositoryId", "sourceFingerprint", "contextFingerprint", "taskScopeFingerprint", "requestType", "normalizedObjective"):
        if not _text(intake.get(key)):
            errors.append(f"intake.{key}: missing required shadow binding")

    repository = context_map.get("repository")
    fingerprints = context_map.get("fingerprints")
    if not isinstance(repository, Mapping) or not isinstance(fingerprints, Mapping):
        return errors + ["contextMap: repository and fingerprints are required for binding"]
    if intake.get("repositoryId") != repository.get("repositoryId"):
        errors.append("intake.repositoryId: does not match contextMap.repository.repositoryId")
    for key in ("sourceFingerprint", "contextFingerprint", "taskScopeFingerprint"):
        if intake.get(key) != fingerprints.get(key):
            errors.append(f"intake.{key}: does not match contextMap.fingerprints.{key}")
    receipt_fingerprints = receipt.get("fingerprints")
    if receipt_fingerprints != dict(fingerprints):
        errors.append("receipt.fingerprints: do not match contextMap.fingerprints")
    policy_repository = policy.get("repository")
    if policy_repository != dict(repository):
        errors.append("policyBundle.repository: does not match contextMap.repository")
    policy_fingerprints = policy.get("fingerprints")
    if not isinstance(policy_fingerprints, Mapping):
        errors.append("policyBundle.fingerprints: expected object")
    else:
        for key in ("sourceFingerprint", "contextFingerprint", "taskScopeFingerprint"):
            if policy_fingerprints.get(key) != fingerprints.get(key):
                errors.append(f"policyBundle.fingerprints.{key}: does not match contextMap.fingerprints.{key}")
    return errors


def _status(context_map: Mapping[str, Any], policy: Mapping[str, Any], errors: list[str]) -> tuple[str, list[str]]:
    blockers: list[str] = list(errors)
    context_status = context_map.get("status")
    if context_status == "CONFLICT":
        blockers.extend(
            f"context conflict: {item.get('conflictId', 'unknown')}"
            for item in context_map.get("conflicts", [])
            if isinstance(item, Mapping) and item.get("blocking")
        )
        return "BLOCKED", blockers
    if context_status in {"INCOMPLETE", "UNAVAILABLE", "STALE", "CORRUPT", "UNSEEN", "DISCOVERING"}:
        blockers.append(f"context status is {context_status}")
        return "MISSING_COVERAGE", blockers
    if policy.get("mutationStatus") in {"BLOCKED", "REVIEW_REQUIRED"}:
        blockers.append(f"policy mutation status is {policy.get('mutationStatus')}")
    if any(item.get("blocking") for item in context_map.get("gaps", []) if isinstance(item, Mapping)):
        blockers.append("context contains blocking gaps")
    return ("BLOCKED" if blockers else "PASS"), blockers


def bind_project_context_shadow(
    intake: Mapping[str, Any],
    context_map: Mapping[str, Any],
    receipt: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach sidecar identity to a canonical intake projection without enforcing it."""

    contract_errors: list[str] = []
    contract_errors.extend(f"contextMap: {error}" for error in validate_context_map(context_map).get("errors", []))
    contract_errors.extend(f"receipt: {error}" for error in validate_receipt(receipt).get("errors", []))
    contract_errors.extend(f"policyBundle: {error}" for error in validate_policy_bundle(policy).get("errors", []))
    if not isinstance(intake, Mapping):
        contract_errors.append("intake: expected object")
        intake = {}
    contract_errors.extend(_binding_errors(intake, context_map, receipt, policy))

    repository = context_map.get("repository", {}) if isinstance(context_map, Mapping) else {}
    fingerprints = context_map.get("fingerprints", {}) if isinstance(context_map, Mapping) else {}
    status, blockers = _status(context_map, policy, contract_errors)
    evidence = [
        {
            "check": "sidecar-contracts",
            "result": "PASS" if not contract_errors else "INVALID",
            "summary": "context map, discovery receipt, and policy bundle contracts validated",
        },
        {
            "check": "identity-bindings",
            "result": "PASS" if not _binding_errors(intake, context_map, receipt, policy) else "INVALID",
            "summary": "task, repository, source, context, policy, and task-scope identities compared",
        },
        {
            "check": "context-status",
            "result": status if status in {"PASS", "BLOCKED", "MISSING_COVERAGE"} else "INVALID",
            "summary": f"context status is {context_map.get('status', 'missing')}; shadow mode does not enforce it",
        },
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "SHADOW",
        "status": status if status in {"PASS", "BLOCKED", "MISSING_COVERAGE"} else "INVALID",
        "taskId": _text(intake.get("taskId")) or "unknown-task",
        "repositoryId": _text(repository.get("repositoryId")) or "unknown-repository",
        "fingerprints": {
            "sourceFingerprint": _text(fingerprints.get("sourceFingerprint")),
            "contextFingerprint": _text(fingerprints.get("contextFingerprint")),
            "policyFingerprint": _text(policy.get("fingerprints", {}).get("policyFingerprint")) if isinstance(policy.get("fingerprints"), Mapping) else "",
            "taskScopeFingerprint": _text(fingerprints.get("taskScopeFingerprint")),
        },
        "canonicalIntake": {
            "requestType": _text(intake.get("requestType")),
            "normalizedObjective": _text(intake.get("normalizedObjective")),
            "targetPaths": [str(item) for item in intake.get("targetPaths", [])] if isinstance(intake.get("targetPaths"), list) else [],
            "acceptanceCriteria": [str(item) for item in intake.get("acceptanceCriteria", [])] if isinstance(intake.get("acceptanceCriteria"), list) else [],
        },
        "sidecars": {
            "contextMap": "BOUND" if validate_context_map(context_map)["valid"] else "INVALID",
            "receipt": "BOUND" if validate_receipt(receipt)["valid"] else "INVALID",
            "policyBundle": "BOUND" if validate_policy_bundle(policy)["valid"] else "INVALID",
        },
        "policy": {
            "selectedProfiles": list(policy.get("selectedProfiles", [])) if isinstance(policy.get("selectedProfiles"), list) else [],
            "riskTier": policy.get("riskTier", "normal"),
            "mutationStatus": policy.get("mutationStatus", "REVIEW_REQUIRED"),
            "requiredCapabilities": list(policy.get("requiredCapabilities", [])) if isinstance(policy.get("requiredCapabilities"), list) else [],
            "requiredEvidence": list(policy.get("requiredEvidence", [])) if isinstance(policy.get("requiredEvidence"), list) else [],
        },
        "controllerGate": {
            "enforcement": "SHADOW_ONLY",
            "routeMutation": "NOT_APPLIED",
            "agentSelection": "NOT_APPLIED",
            "closureAuthority": "AE",
        },
        "blockers": sorted(set(str(item) for item in blockers if str(item).strip())),
        "evidence": evidence,
        "sideEffects": [],
        "generatedAt": _now(),
    }


def validate_shadow_binding(value: Any) -> dict[str, Any]:
    """Validate the stable output contract without changing its status."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["shadowBinding: expected object"]}
    allowed = {
        "schemaVersion", "mode", "status", "taskId", "repositoryId", "fingerprints", "canonicalIntake",
        "sidecars", "policy", "controllerGate", "blockers", "evidence", "sideEffects", "generatedAt", "validation",
    }
    errors.extend(f"shadowBinding: unknown field {key}" for key in sorted(set(value) - allowed))
    for key in sorted({"schemaVersion", "mode", "status", "taskId", "repositoryId", "fingerprints", "canonicalIntake", "sidecars", "policy", "controllerGate", "blockers", "evidence", "sideEffects", "generatedAt"} - set(value)):
        errors.append(f"shadowBinding: missing required field {key}")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"shadowBinding.schemaVersion: expected {SCHEMA_VERSION}")
    if value.get("mode") != "SHADOW":
        errors.append("shadowBinding.mode: must be SHADOW")
    if value.get("status") not in {"PASS", "BLOCKED", "MISSING_COVERAGE", "INVALID"}:
        errors.append("shadowBinding.status: unsupported value")
    for key in ("taskId", "repositoryId", "generatedAt"):
        if not _text(value.get(key)):
            errors.append(f"shadowBinding.{key}: expected non-empty string")
    if value.get("sideEffects") != []:
        errors.append("shadowBinding.sideEffects: must be empty")
    gate = value.get("controllerGate")
    if not isinstance(gate, Mapping) or gate.get("enforcement") != "SHADOW_ONLY" or gate.get("routeMutation") != "NOT_APPLIED" or gate.get("agentSelection") != "NOT_APPLIED" or gate.get("closureAuthority") != "AE":
        errors.append("shadowBinding.controllerGate: shadow-only controller boundary is invalid")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("blockers", [])):
        errors.append("shadowBinding.blockers: expected string array")
    if not isinstance(value.get("evidence"), list) or not value.get("evidence"):
        errors.append("shadowBinding.evidence: expected non-empty array")
    if value.get("status") == "PASS" and value.get("blockers"):
        errors.append("shadowBinding.blockers: PASS binding cannot contain blockers")
    validation = value.get("validation")
    if validation is not None:
        if not isinstance(validation, Mapping) or not isinstance(validation.get("valid"), bool) or not isinstance(validation.get("errors"), list):
            errors.append("shadowBinding.validation: expected valid boolean and errors array")
    return {"valid": not errors, "errors": errors}
