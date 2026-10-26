"""Bounded native-agent bridge for AE-selected assignments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .model_selection import SUPPORTED_MODELS, validate_assignment


MANIFEST_SCHEMA_VERSION = "native-agent-bridge-manifest-v1"
RECEIPT_SCHEMA_VERSION = "native-agent-receipt-v1"
STATUSES = {"AVAILABLE", "UNAVAILABLE", "MISCONFIGURED", "NOT_VERIFIED"}


class NativeBridgeError(ValueError):
    """Raised when a native-agent request violates the AE authority boundary."""


class NativeAgentTransport(Protocol):
    """Host-owned spawn transport; AE never invokes a process directly."""

    def spawn(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Spawn one bounded agent and return sanitized state."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_native_manifest(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["nativeManifest: expected object"]}
    required = {"schemaVersion", "bridgeId", "hostId", "version", "status", "capabilities", "sourceBound", "externalSideEffects"}
    errors.extend(f"nativeManifest: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"nativeManifest: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"nativeManifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")
    for key in ("bridgeId", "hostId", "version"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"nativeManifest.{key}: expected non-empty string")
    if value.get("status") not in STATUSES:
        errors.append("nativeManifest.status: unsupported value")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or not all(isinstance(item, str) and item.strip() for item in capabilities):
        errors.append("nativeManifest.capabilities: expected non-empty string array")
    if value.get("sourceBound") is not True:
        errors.append("nativeManifest.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("nativeManifest.externalSideEffects: must be HOST_CONTROLLED")
    return {"valid": not errors, "errors": errors}


def validate_native_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["nativeReceipt: expected object"]}
    required = {"schemaVersion", "receiptId", "assignmentId", "taskId", "canonicalName", "role", "model", "status", "sourceFingerprint", "live", "claim", "checkpoints", "blockers", "sideEffects", "generatedAt"}
    errors.extend(f"nativeReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"nativeReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"nativeReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("receiptId", "assignmentId", "taskId", "canonicalName", "role", "sourceFingerprint", "generatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"nativeReceipt.{key}: expected non-empty string")
    if value.get("model") not in SUPPORTED_MODELS:
        errors.append("nativeReceipt.model: unsupported model")
    if value.get("status") not in {"STARTED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "CAPABILITY_UNAVAILABLE", "REJECTED"}:
        errors.append("nativeReceipt.status: unsupported value")
    if not isinstance(value.get("live"), bool):
        errors.append("nativeReceipt.live: expected boolean")
    claim = value.get("claim")
    if not isinstance(claim, Mapping) or set(claim) != {"controller", "owner", "scopeId", "resourceLimit", "replayKey"}:
        errors.append("nativeReceipt.claim: expected exact bounded claim")
    if not isinstance(value.get("checkpoints"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("checkpoints", [])):
        errors.append("nativeReceipt.checkpoints: expected string array")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("blockers", [])):
        errors.append("nativeReceipt.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("nativeReceipt.sideEffects: must be empty")
    if value.get("status") == "COMPLETED" and not value.get("live"):
        errors.append("nativeReceipt: COMPLETED requires live host proof")
    return {"valid": not errors, "errors": errors}


def _receipt(assignment: Mapping[str, Any], slot: Mapping[str, Any], *, status: str, reason: str, live: bool, checkpoints: list[str] | None = None) -> dict[str, Any]:
    scope = slot.get("scope", {}) if isinstance(slot.get("scope"), Mapping) else {}
    resource = slot.get("resourceLimit", {}) if isinstance(slot.get("resourceLimit"), Mapping) else {}
    receipt = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "receiptId": f"{assignment.get('assignmentId', 'assignment')}:{slot.get('canonicalName', 'agent')}",
        "assignmentId": str(assignment.get("assignmentId", "unknown-assignment")),
        "taskId": str(assignment.get("taskId", "unknown-task")),
        "canonicalName": str(slot.get("canonicalName", "unknown-agent")),
        "role": str(assignment.get("role", "worker")),
        "model": str(assignment.get("resolvedModel") or assignment.get("desiredModel") or "model-fast"),
        "status": status,
        "sourceFingerprint": str(assignment.get("sourceFingerprint", "unknown-source")),
        "live": live,
        "claim": {
            "controller": "AE",
            "owner": str(slot.get("owner", "AE")),
            "scopeId": str(scope.get("scopeId", "unknown-scope")),
            "resourceLimit": {"maxSeconds": int(resource.get("maxSeconds", 1)), "maxOutputTokens": int(resource.get("maxOutputTokens", 1))},
            "replayKey": f"{assignment.get('assignmentId', 'assignment')}:{slot.get('canonicalName', 'agent')}",
        },
        "checkpoints": list(checkpoints or []),
        "blockers": [reason],
        "sideEffects": [],
        "generatedAt": _now(),
    }
    validation = validate_native_receipt(receipt)
    if not validation["valid"]:
        raise NativeBridgeError("native receipt failed validation: " + "; ".join(validation["errors"]))
    return receipt


class NativeAgentBridge:
    """Validate a selected assignment and ask an injected host to spawn it."""

    def __init__(self, manifest: Mapping[str, Any], transport: NativeAgentTransport | None = None):
        result = validate_native_manifest(manifest)
        if not result["valid"]:
            raise NativeBridgeError("invalid native bridge manifest: " + "; ".join(result["errors"]))
        self.manifest = dict(manifest)
        self.transport = transport

    def spawn(
        self,
        assignment: Mapping[str, Any],
        slot: Mapping[str, Any],
        *,
        expected_policy: Mapping[str, Any] | None = None,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        assignment_result = validate_assignment(
            assignment,
            expected_policy=expected_policy,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=now,
        )
        errors = list(assignment_result["errors"])
        if not isinstance(slot, Mapping):
            errors.append("agent slot: expected object")
            slot = {}
        required = {"canonicalName", "owner", "controller", "role", "scope", "resourceLimit", "stopCondition", "taskId", "sourceFingerprint"}
        errors.extend(f"agent slot: missing required field {key}" for key in sorted(required - set(slot)))
        errors.extend(f"agent slot: unknown field {key}" for key in sorted(set(slot) - required))
        if slot.get("controller") != "AE" or slot.get("owner") != "AE":
            errors.append("agent slot: AE must remain controller and owner")
        if slot.get("role") != assignment.get("role"):
            errors.append("agent slot.role: must match assignment role")
        if slot.get("taskId") != assignment.get("taskId"):
            errors.append("agent slot.taskId: must match assignment")
        if slot.get("sourceFingerprint") != assignment.get("sourceFingerprint"):
            errors.append("agent slot.sourceFingerprint: must match assignment")
        scope = slot.get("scope")
        if not isinstance(scope, Mapping) or set(scope) != {"scopeId", "paths", "authority"} or scope.get("authority") != "AE" or not isinstance(scope.get("paths"), list) or not scope.get("paths"):
            errors.append("agent slot.scope: expected bounded AE scope")
        resource = slot.get("resourceLimit")
        if not isinstance(resource, Mapping) or set(resource) != {"maxSeconds", "maxOutputTokens"} or not all(isinstance(resource.get(key), int) and resource.get(key) > 0 for key in ("maxSeconds", "maxOutputTokens")):
            errors.append("agent slot.resourceLimit: expected positive bounds")
        if not isinstance(slot.get("canonicalName"), str) or not slot["canonicalName"].strip():
            errors.append("agent slot.canonicalName: expected canonical name")
        if errors:
            receipt = _receipt(assignment, slot, status="REJECTED", reason="; ".join(errors), live=False)
            return {"status": "REJECTED", "receipt": receipt, "spawnAttempted": False}
        if assignment.get("status") not in {"SELECTED", "OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"} or not assignment.get("resolvedModel"):
            receipt = _receipt(assignment, slot, status="CAPABILITY_UNAVAILABLE", reason="assignment is not dispatchable", live=False)
            return {"status": "CAPABILITY_UNAVAILABLE", "receipt": receipt, "spawnAttempted": False}
        if self.manifest["status"] != "AVAILABLE" or self.transport is None:
            receipt = _receipt(assignment, slot, status="CAPABILITY_UNAVAILABLE", reason="native agent host bridge is unavailable", live=False)
            return {"status": "CAPABILITY_UNAVAILABLE", "receipt": receipt, "spawnAttempted": False}
        request = {
            "schemaVersion": "native-agent-spawn-request-v1",
            "assignmentId": assignment["assignmentId"],
            "taskId": assignment["taskId"],
            "role": assignment["role"],
            "model": assignment["resolvedModel"],
            "reasoningEffort": assignment["reasoningEffort"],
            "canonicalName": slot["canonicalName"],
            "scope": dict(slot["scope"]),
            "resourceLimit": dict(slot["resourceLimit"]),
            "stopCondition": slot["stopCondition"],
            "sourceFingerprint": assignment["sourceFingerprint"],
            "controller": "AE",
        }
        try:
            response = self.transport.spawn(request)
        except Exception as exc:
            response = {"status": "FAILED", "blockers": [f"native spawn failed: {type(exc).__name__}"], "checkpoints": [], "live": True}
        if not isinstance(response, Mapping):
            response = {"status": "FAILED", "blockers": ["native bridge returned a non-object response"], "checkpoints": [], "live": True}
        status = response.get("status") if response.get("status") in {"STARTED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "CAPABILITY_UNAVAILABLE"} else "FAILED"
        blockers = [str(item) for item in response.get("blockers", []) if isinstance(item, str) and item.strip()]
        checkpoints = [str(item) for item in response.get("checkpoints", []) if isinstance(item, str) and item.strip()]
        live = response.get("live", True) if isinstance(response.get("live", True), bool) else True
        receipt = _receipt(assignment, slot, status=status, reason="; ".join(blockers) if blockers else "host native bridge returned a bounded result", live=live, checkpoints=checkpoints)
        receipt["blockers"] = blockers
        validation = validate_native_receipt(receipt)
        if not validation["valid"]:
            raise NativeBridgeError("native host receipt failed validation: " + "; ".join(validation["errors"]))
        return {"status": status, "receipt": receipt, "spawnAttempted": True}


def unavailable_native_manifest(*, bridge_id: str = "native-bridge-unavailable", host_id: str = "host-unavailable") -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "bridgeId": bridge_id,
        "hostId": host_id,
        "version": "0.0.0",
        "status": "UNAVAILABLE",
        "capabilities": ["spawn", "checkpoint", "cancel"],
        "sourceBound": True,
        "externalSideEffects": "HOST_CONTROLLED",
    }
