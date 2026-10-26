"""Portable task packets and the lightweight-to-safety route decision.

The packet is intentionally tool-neutral.  It contains normalized task facts,
not a raw prompt, and can be passed between AI tools without transferring
authority or claiming that a host capability exists.
"""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA_VERSION = "portable-task-packet-v1"

REQUEST_TYPES = {
    "QUESTION",
    "CONVERSATION",
    "STATUS",
    "VALIDATION",
    "REVIEW",
    "INVESTIGATION",
    "IMPLEMENTATION",
    "MIGRATION",
    "SECURITY",
    "DEPLOYMENT",
}
COMPLEXITIES = {"LIGHTWEIGHT", "NORMAL", "COUPLED"}
PROOF_TIERS = {
    "source",
    "test",
    "review",
    "runtime",
    "browser",
    "device",
    "deployment",
    "user",
}
CAPABILITY_IDS = {
    "repo_read",
    "repo_write",
    "shell",
    "tests",
    "browser",
    "device",
    "subagents",
    "memory",
    "external_actions",
    "approval",
    "persistent_state",
    "worktree",
}
APPROVAL_STATUSES = {"NOT_REQUIRED", "REQUIRED", "APPROVED", "REJECTED"}


def _errors_for_keys(value: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    errors = [f"{label}: missing required field {key}" for key in sorted(required - set(value))]
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - required))
    return errors


def _text(value: Any, label: str, *, max_length: int = 2000) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{label}: expected non-empty text"]
    if len(value) > max_length:
        return [f"{label}: exceeds {max_length} characters"]
    if "\x00" in value:
        return [f"{label}: NUL characters are not allowed"]
    return []


def _text_list(value: Any, label: str, *, required: bool = True) -> list[str]:
    if not isinstance(value, list):
        return [f"{label}: expected array"]
    if required and not value:
        return [f"{label}: expected non-empty array"]
    errors: list[str] = []
    if all(isinstance(item, str) for item in value) and len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    for index, item in enumerate(value):
        errors.extend(_text(item, f"{label}[{index}]", max_length=500))
    return errors


def validate_task_packet(value: Any) -> dict[str, Any]:
    """Validate a normalized, portable task packet without executing anything."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["taskPacket: expected object"]}

    required = {
        "schemaVersion",
        "taskId",
        "requestType",
        "objective",
        "complexity",
        "acceptanceCriteria",
        "constraints",
        "riskSignals",
        "requiredCapabilities",
        "requiredEvidence",
        "allowedActions",
        "approval",
        "scope",
    }
    errors.extend(_errors_for_keys(value, required, "taskPacket"))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"taskPacket.schemaVersion: expected {SCHEMA_VERSION}")
    errors.extend(_text(value.get("taskId"), "taskPacket.taskId", max_length=200))
    if value.get("requestType") not in REQUEST_TYPES:
        errors.append("taskPacket.requestType: unsupported value")
    errors.extend(_text(value.get("objective"), "taskPacket.objective"))
    if value.get("complexity") not in COMPLEXITIES:
        errors.append("taskPacket.complexity: unsupported value")

    errors.extend(_text_list(value.get("acceptanceCriteria"), "taskPacket.acceptanceCriteria"))
    errors.extend(_text_list(value.get("constraints"), "taskPacket.constraints", required=False))
    errors.extend(_text_list(value.get("riskSignals"), "taskPacket.riskSignals", required=False))

    capabilities = value.get("requiredCapabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("taskPacket.requiredCapabilities: expected non-empty array")
    else:
        seen: set[str] = set()
        for index, item in enumerate(capabilities):
            label = f"taskPacket.requiredCapabilities[{index}]"
            if not isinstance(item, Mapping):
                errors.append(f"{label}: expected object")
                continue
            errors.extend(_errors_for_keys(item, {"capabilityId", "reason"}, label))
            capability_id = item.get("capabilityId")
            if capability_id not in CAPABILITY_IDS:
                errors.append(f"{label}.capabilityId: unsupported value")
            elif capability_id in seen:
                errors.append(f"{label}.capabilityId: duplicate value")
            else:
                seen.add(capability_id)
            errors.extend(_text(item.get("reason"), f"{label}.reason", max_length=500))

    evidence = value.get("requiredEvidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("taskPacket.requiredEvidence: expected non-empty array")
    else:
        for index, item in enumerate(evidence):
            if item not in PROOF_TIERS:
                errors.append(f"taskPacket.requiredEvidence[{index}]: unsupported value")

    actions = value.get("allowedActions")
    action_keys = {"read", "write", "execute", "external"}
    if not isinstance(actions, Mapping):
        errors.append("taskPacket.allowedActions: expected object")
    else:
        errors.extend(_errors_for_keys(actions, action_keys, "taskPacket.allowedActions"))
        for key in action_keys:
            if not isinstance(actions.get(key), bool):
                errors.append(f"taskPacket.allowedActions.{key}: expected boolean")

    approval = value.get("approval")
    approval_keys = {"status", "approvalId", "reason"}
    if not isinstance(approval, Mapping):
        errors.append("taskPacket.approval: expected object")
    else:
        errors.extend(_errors_for_keys(approval, approval_keys, "taskPacket.approval"))
        status = approval.get("status")
        if status not in APPROVAL_STATUSES:
            errors.append("taskPacket.approval.status: unsupported value")
        approval_id = approval.get("approvalId")
        if approval_id is not None and (not isinstance(approval_id, str) or not approval_id.strip()):
            errors.append("taskPacket.approval.approvalId: expected null or non-empty text")
        if status == "APPROVED" and not isinstance(approval_id, str):
            errors.append("taskPacket.approval.approvalId: required when approved")
        errors.extend(_text(approval.get("reason"), "taskPacket.approval.reason", max_length=500))

    scope = value.get("scope")
    scope_keys = {"maxRepositories", "maxPaths", "writable", "externalSideEffects"}
    if not isinstance(scope, Mapping):
        errors.append("taskPacket.scope: expected object")
    else:
        errors.extend(_errors_for_keys(scope, scope_keys, "taskPacket.scope"))
        for key in ("maxRepositories", "maxPaths"):
            if not isinstance(scope.get(key), int) or isinstance(scope.get(key), bool) or scope.get(key, 0) < 1:
                errors.append(f"taskPacket.scope.{key}: expected positive integer")
        for key in ("writable", "externalSideEffects"):
            if not isinstance(scope.get(key), bool):
                errors.append(f"taskPacket.scope.{key}: expected boolean")
        if isinstance(actions, Mapping) and isinstance(scope, Mapping):
            if actions.get("write") is True and scope.get("writable") is not True:
                errors.append("taskPacket.scope.writable: must be true when write is allowed")
            if actions.get("external") is True and scope.get("externalSideEffects") is not True:
                errors.append("taskPacket.scope.externalSideEffects: must be true when external actions are allowed")

    optional_text_fields = {
        "currentBehavior": 2000,
        "expectedBehavior": 2000,
        "reproduction": 2000,
        "dependencies": 1000,
        "missingDecisions": 1000,
    }
    for field, limit in optional_text_fields.items():
        if field in value and value[field] is not None:
            errors.extend(_text(value[field], f"taskPacket.{field}", max_length=limit))
    if "sourceBinding" in value:
        binding = value.get("sourceBinding")
        if not isinstance(binding, Mapping):
            errors.append("taskPacket.sourceBinding: expected object")
        else:
            allowed_binding = {"repository", "sourceFingerprint", "branch", "worktree"}
            errors.extend(
                f"taskPacket.sourceBinding: unknown field {key}"
                for key in sorted(set(binding) - allowed_binding)
            )
            for key in allowed_binding & set(binding):
                errors.extend(_text(binding[key], f"taskPacket.sourceBinding.{key}", max_length=500))

    return {"valid": not errors, "errors": errors}


HIGH_RISK_SIGNALS = {
    "authentication",
    "authorization",
    "privacy",
    "security",
    "release",
    "production",
    "data-loss",
    "offline-sync",
    "queue",
    "persistence",
    "migration",
    "native",
    "device",
    "lifecycle",
    "conflict",
    "deployment",
}
DESTRUCTIVE_SIGNALS = {
    "data-loss",
    "production",
    "deployment",
    "activation",
    "external-message",
    "delete",
    "migration",
}


def profile_task(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Choose the smallest useful execution mode and bounded effort budget."""

    validation = validate_task_packet(packet)
    if not validation["valid"]:
        return {
            "status": "INVALID",
            "route": "BLOCKED",
            "reasons": validation["errors"],
            "budget": {"maxDiscoverySteps": 0, "maxWorkers": 0, "maxVerificationCommands": 0},
            "approvalGate": {"status": "BLOCKED", "required": True, "reason": "invalid task packet"},
        }

    signals = {str(item).strip().casefold() for item in packet.get("riskSignals", [])}
    actions = packet["allowedActions"]
    request_type = packet["requestType"]
    complexity = packet["complexity"]
    high_risk = bool(signals & HIGH_RISK_SIGNALS) or complexity == "COUPLED" or request_type in {"SECURITY", "MIGRATION", "DEPLOYMENT"}
    destructive = bool(signals & DESTRUCTIVE_SIGNALS) or bool(actions.get("external"))

    if request_type in {"QUESTION", "CONVERSATION", "STATUS"} and complexity == "LIGHTWEIGHT" and not actions.get("write") and not actions.get("external") and not high_risk:
        route = "DIRECT"
        budget = {"maxDiscoverySteps": 3, "maxWorkers": 0, "maxVerificationCommands": 2}
        reasons = ["informational request has no mutation or high-risk boundary"]
    elif high_risk:
        route = "FULL_SAFETY"
        budget = {"maxDiscoverySteps": 16, "maxWorkers": 3, "maxVerificationCommands": 12}
        reasons = ["verified high-risk or coupled boundary requires full safety coverage"]
    elif complexity == "LIGHTWEIGHT":
        route = "FAST_DAILY"
        budget = {"maxDiscoverySteps": 6, "maxWorkers": 0, "maxVerificationCommands": 4}
        reasons = ["small bounded work can use the fast path"]
    else:
        route = "NORMAL"
        budget = {"maxDiscoverySteps": 10, "maxWorkers": 1, "maxVerificationCommands": 8}
        reasons = ["normal work needs bounded inspection, review, and verification"]

    requested_approval = packet["approval"]["status"]
    approval_required = requested_approval in {"REQUIRED", "APPROVED", "REJECTED"} or destructive
    if not approval_required:
        approval = {"status": "NOT_REQUIRED", "required": False, "reason": "no destructive or external action detected"}
    elif requested_approval == "APPROVED" and packet["approval"].get("approvalId"):
        approval = {"status": "APPROVED", "required": True, "reason": packet["approval"]["reason"]}
    elif requested_approval == "REJECTED":
        approval = {"status": "BLOCKED", "required": True, "reason": "human approval was rejected"}
    else:
        approval = {"status": "BLOCKED", "required": True, "reason": "human approval is required before the requested side effect"}

    return {
        "status": "READY" if approval["status"] != "BLOCKED" else "BLOCKED",
        "route": route,
        "reasons": reasons,
        "budget": budget,
        "approvalGate": approval,
        "riskSignals": sorted(signals),
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }
