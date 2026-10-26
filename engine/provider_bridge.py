"""Host-owned model/provider execution boundary for AE assignments."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Protocol

from .model_selection import (
    SUPPORTED_MODELS,
    validate_assignment,
    validate_host_catalog,
)


MANIFEST_SCHEMA_VERSION = "host-model-provider-manifest-v1"
RECEIPT_SCHEMA_VERSION = "provider-execution-receipt-v1"
STATUSES = {"AVAILABLE", "UNAVAILABLE", "MISCONFIGURED", "NOT_VERIFIED"}
EXECUTION_STATUSES = {"EXECUTED", "FAILED", "CANCELLED", "TIMED_OUT", "CAPABILITY_UNAVAILABLE", "REJECTED"}


class ProviderBridgeError(ValueError):
    """Raised when an assignment or provider response violates the boundary."""


class ProviderTransport(Protocol):
    """Authenticated host provider transport; credentials never enter AE."""

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Execute one already-bound assignment and return sanitized output."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_provider_manifest(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["providerManifest: expected object"]}
    required = {"schemaVersion", "providerId", "hostId", "version", "status", "authState", "models", "sourceBound", "externalSideEffects"}
    errors.extend(f"providerManifest: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"providerManifest: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"providerManifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")
    for key in ("providerId", "hostId", "version"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"providerManifest.{key}: expected non-empty string")
    if value.get("status") not in STATUSES:
        errors.append("providerManifest.status: unsupported value")
    if value.get("authState") not in {"AUTHENTICATED", "UNAVAILABLE", "SANDBOXED"}:
        errors.append("providerManifest.authState: unsupported value")
    models = value.get("models")
    if not isinstance(models, list) or not models:
        errors.append("providerManifest.models: expected non-empty array")
    else:
        seen: set[str] = set()
        for index, item in enumerate(models):
            label = f"providerManifest.models[{index}]"
            if not isinstance(item, Mapping) or set(item) != {"modelId", "reasoningEfforts", "status"}:
                errors.append(f"{label}: expected exact model entry")
                continue
            model_id = item.get("modelId")
            if model_id not in SUPPORTED_MODELS:
                errors.append(f"{label}.modelId: unsupported model")
            if model_id in seen:
                errors.append(f"{label}.modelId: duplicate model")
            seen.add(str(model_id))
            efforts = item.get("reasoningEfforts")
            if not isinstance(efforts, list) or not efforts or not all(isinstance(effort, str) and effort in {"low", "medium", "high", "max"} for effort in efforts):
                errors.append(f"{label}.reasoningEfforts: expected supported effort array")
            if item.get("status") not in {"AVAILABLE", "UNAVAILABLE"}:
                errors.append(f"{label}.status: unsupported value")
    if value.get("sourceBound") is not True:
        errors.append("providerManifest.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("providerManifest.externalSideEffects: must be HOST_CONTROLLED")
    if value.get("status") == "AVAILABLE" and value.get("authState") != "AUTHENTICATED":
        errors.append("providerManifest: AVAILABLE provider requires AUTHENTICATED authState")
    return {"valid": not errors, "errors": errors}


def validate_provider_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["providerReceipt: expected object"]}
    required = {"schemaVersion", "executionId", "assignmentId", "taskId", "providerId", "model", "status", "sourceFingerprint", "live", "evidenceRefs", "checkpoint", "blockers", "sideEffects", "generatedAt"}
    errors.extend(f"providerReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"providerReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"providerReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("executionId", "assignmentId", "taskId", "providerId", "model", "sourceFingerprint", "generatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"providerReceipt.{key}: expected non-empty string")
    if value.get("model") not in SUPPORTED_MODELS:
        errors.append("providerReceipt.model: unsupported model")
    if value.get("status") not in EXECUTION_STATUSES:
        errors.append("providerReceipt.status: unsupported status")
    if not isinstance(value.get("live"), bool):
        errors.append("providerReceipt.live: expected boolean")
    if not isinstance(value.get("evidenceRefs"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("evidenceRefs", [])):
        errors.append("providerReceipt.evidenceRefs: expected string array")
    checkpoint = value.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or set(checkpoint) != {"sequence", "state"} or not isinstance(checkpoint.get("sequence"), int) or checkpoint.get("sequence") < 0 or checkpoint.get("state") not in {"NONE", "SAVED", "PARTIAL", "FINAL"}:
        errors.append("providerReceipt.checkpoint: expected sequence/state")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("blockers", [])):
        errors.append("providerReceipt.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("providerReceipt.sideEffects: must be empty")
    if value.get("status") == "EXECUTED" and not value.get("live"):
        errors.append("providerReceipt: EXECUTED requires live host proof")
    return {"valid": not errors, "errors": errors}


def _unavailable_receipt(assignment: Mapping[str, Any], *, provider_id: str, reason: str, status: str = "CAPABILITY_UNAVAILABLE") -> dict[str, Any]:
    receipt = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "executionId": f"{assignment.get('assignmentId', 'assignment')}:unavailable",
        "assignmentId": str(assignment.get("assignmentId", "unknown-assignment")),
        "taskId": str(assignment.get("taskId", "unknown-task")),
        "providerId": provider_id,
        "model": str(assignment.get("resolvedModel") or assignment.get("desiredModel") or "model-fast"),
        "status": status,
        "sourceFingerprint": str(assignment.get("sourceFingerprint", "unknown-source")),
        "live": False,
        "evidenceRefs": [],
        "checkpoint": {"sequence": 0, "state": "NONE"},
        "blockers": [reason],
        "sideEffects": [],
        "generatedAt": _now(),
    }
    validation = validate_provider_receipt(receipt)
    if not validation["valid"]:
        raise ProviderBridgeError("unavailable provider receipt failed validation: " + "; ".join(validation["errors"]))
    return receipt


class ModelProviderBridge:
    """Validate and execute one AE assignment through an injected provider."""

    def __init__(self, manifest: Mapping[str, Any], transport: ProviderTransport | None = None):
        result = validate_provider_manifest(manifest)
        if not result["valid"]:
            raise ProviderBridgeError("invalid provider manifest: " + "; ".join(result["errors"]))
        self.manifest = dict(manifest)
        self.transport = transport

    @property
    def provider_id(self) -> str:
        return str(self.manifest["providerId"])

    def catalog(self) -> dict[str, Any]:
        available: dict[str, list[str]] = {}
        for model in self.manifest["models"]:
            if isinstance(model, Mapping) and model.get("status") == "AVAILABLE":
                available[str(model["modelId"])] = [str(item) for item in model["reasoningEfforts"]]
        catalog = {
            "schemaVersion": "host-model-catalog-v1",
            "hostId": self.manifest["hostId"],
            "availableModels": available,
            "inheritedModel": None,
            "inheritedReason": None,
        }
        validation = validate_host_catalog(catalog)
        if not validation["valid"]:
            return {"status": "NOT_VERIFIED", "catalog": None, "blockers": validation["errors"]}
        if self.manifest["status"] != "AVAILABLE" or self.manifest["authState"] != "AUTHENTICATED":
            return {"status": "CAPABILITY_UNAVAILABLE", "catalog": catalog, "blockers": ["provider is not authenticated and available"]}
        return {"status": "AVAILABLE", "catalog": catalog, "blockers": []}

    def execute(
        self,
        assignment: Mapping[str, Any],
        *,
        expected_policy: Mapping[str, Any] | None = None,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        validation = validate_assignment(
            assignment,
            expected_policy=expected_policy,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=now,
        )
        if not validation["valid"]:
            receipt = _unavailable_receipt(assignment, provider_id=self.provider_id, reason="assignment rejected: " + "; ".join(validation["errors"]), status="REJECTED")
            return {"status": "REJECTED", "receipt": receipt, "providerAttempted": False}
        if assignment.get("status") not in {"SELECTED", "OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"} or not assignment.get("resolvedModel"):
            receipt = _unavailable_receipt(assignment, provider_id=self.provider_id, reason=f"assignment status is not dispatchable: {assignment.get('status')}")
            return {"status": "CAPABILITY_UNAVAILABLE", "receipt": receipt, "providerAttempted": False}
        catalog_result = self.catalog()
        model = str(assignment["resolvedModel"])
        efforts = catalog_result.get("catalog", {}).get("availableModels", {}).get(model, []) if isinstance(catalog_result.get("catalog"), Mapping) else []
        if catalog_result.get("status") != "AVAILABLE" or assignment.get("reasoningEffort") not in efforts:
            receipt = _unavailable_receipt(assignment, provider_id=self.provider_id, reason=f"model or reasoning capability unavailable: {model}/{assignment.get('reasoningEffort')}")
            return {"status": "CAPABILITY_UNAVAILABLE", "receipt": receipt, "providerAttempted": False, "catalog": catalog_result}
        if self.transport is None:
            receipt = _unavailable_receipt(assignment, provider_id=self.provider_id, reason="authenticated provider transport is not configured")
            return {"status": "CAPABILITY_UNAVAILABLE", "receipt": receipt, "providerAttempted": False, "catalog": catalog_result}
        request = {
            "schemaVersion": "provider-execution-request-v1",
            "assignmentId": assignment["assignmentId"],
            "taskId": assignment["taskId"],
            "model": model,
            "reasoningEffort": assignment["reasoningEffort"],
            "role": assignment["role"],
            "scope": dict(assignment["scope"]),
            "sourceFingerprint": assignment["sourceFingerprint"],
            "hostId": assignment["hostId"],
        }
        try:
            response = self.transport.execute(request)
        except Exception as exc:
            response = {"status": "FAILED", "blockers": [f"provider call failed: {type(exc).__name__}"], "checkpoint": {"sequence": 0, "state": "NONE"}, "live": True}
        if not isinstance(response, Mapping):
            response = {"status": "NOT_VERIFIED", "blockers": ["provider returned a non-object response"], "checkpoint": {"sequence": 0, "state": "NONE"}, "live": True}
        response_model = response.get("model", model)
        blockers = [str(item) for item in response.get("blockers", []) if isinstance(item, str) and item.strip()]
        if response_model != model:
            blockers.append("provider response model does not match the assigned model")
        status = response.get("status") if response.get("status") in {"EXECUTED", "FAILED", "CANCELLED", "TIMED_OUT", "CAPABILITY_UNAVAILABLE"} else "FAILED"
        if response_model != model:
            status = "REJECTED"
        refs = [str(item) for item in response.get("evidenceRefs", []) if isinstance(item, str) and item.strip()]
        checkpoint = response.get("checkpoint")
        if not isinstance(checkpoint, Mapping) or set(checkpoint) != {"sequence", "state"}:
            checkpoint = {"sequence": 0, "state": "NONE"}
            blockers.append("provider response checkpoint is missing or invalid")
        receipt = {
            "schemaVersion": RECEIPT_SCHEMA_VERSION,
            "executionId": f"{assignment['assignmentId']}:{self.provider_id}",
            "assignmentId": assignment["assignmentId"],
            "taskId": assignment["taskId"],
            "providerId": self.provider_id,
            "model": model,
            "status": status,
            "sourceFingerprint": assignment["sourceFingerprint"],
            "live": response.get("live", True) if isinstance(response.get("live", True), bool) else True,
            "evidenceRefs": refs,
            "checkpoint": {"sequence": int(checkpoint.get("sequence", 0)), "state": checkpoint.get("state", "NONE")},
            "blockers": sorted(set(blockers)),
            "sideEffects": [],
            "generatedAt": _now(),
        }
        validation = validate_provider_receipt(receipt)
        if not validation["valid"]:
            raise ProviderBridgeError("provider receipt failed validation: " + "; ".join(validation["errors"]))
        return {"status": status, "receipt": receipt, "providerAttempted": True, "catalog": catalog_result}


def unavailable_provider_manifest(*, provider_id: str = "provider-unavailable", host_id: str = "host-unavailable") -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "providerId": provider_id,
        "hostId": host_id,
        "version": "0.0.0",
        "status": "UNAVAILABLE",
        "authState": "UNAVAILABLE",
        "models": [{"modelId": model, "reasoningEfforts": ["low"], "status": "UNAVAILABLE"} for model in SUPPORTED_MODELS],
        "sourceBound": True,
        "externalSideEffects": "HOST_CONTROLLED",
    }
