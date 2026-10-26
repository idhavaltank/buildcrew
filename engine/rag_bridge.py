"""Provider boundary for live RAG retrieval and learning write-back."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


MANIFEST_SCHEMA_VERSION = "rag-provider-manifest-v1"
RETRIEVAL_RECEIPT_SCHEMA_VERSION = "rag-provider-retrieval-receipt-v1"
WRITEBACK_RECEIPT_SCHEMA_VERSION = "rag-provider-writeback-receipt-v1"
STATUSES = {"AVAILABLE", "UNAVAILABLE", "MISCONFIGURED", "NOT_VERIFIED"}


class RAGBridgeError(ValueError):
    """Raised when a retrieval or learning provider violates its boundary."""


class RAGProviderTransport(Protocol):
    def retrieve(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return scoped, sanitized records and provider evidence."""

    def writeback(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Acknowledge or reject an explicitly authorized learning write."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_rag_manifest(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["ragManifest: expected object"]}
    required = {"schemaVersion", "providerId", "hostId", "version", "status", "authState", "capabilities", "privacyModel", "sourceBound", "externalSideEffects"}
    errors.extend(f"ragManifest: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"ragManifest: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"ragManifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")
    for key in ("providerId", "hostId", "version"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"ragManifest.{key}: expected non-empty string")
    if value.get("status") not in STATUSES:
        errors.append("ragManifest.status: unsupported value")
    if value.get("authState") not in {"AUTHENTICATED", "UNAVAILABLE", "SANDBOXED"}:
        errors.append("ragManifest.authState: unsupported value")
    if not isinstance(value.get("capabilities"), list) or not value["capabilities"] or not all(item in {"retrieve", "writeback", "freshness", "access_evidence"} for item in value["capabilities"]):
        errors.append("ragManifest.capabilities: expected supported capability array")
    if value.get("privacyModel") not in {"REDACTED", "TENANT_SCOPED", "HOST_DEFINED"}:
        errors.append("ragManifest.privacyModel: unsupported value")
    if value.get("sourceBound") is not True:
        errors.append("ragManifest.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("ragManifest.externalSideEffects: must be HOST_CONTROLLED")
    if value.get("status") == "AVAILABLE" and value.get("authState") != "AUTHENTICATED":
        errors.append("ragManifest: AVAILABLE provider requires AUTHENTICATED authState")
    return {"valid": not errors, "errors": errors}


def _receipt(
    *,
    schema_version: str,
    receipt_id: str,
    task_id: str,
    source_fingerprint: str,
    provider_id: str,
    status: str,
    live: bool,
    scope: Mapping[str, Any],
    blockers: list[str],
    record_count: int = 0,
    acknowledged: bool = False,
) -> dict[str, Any]:
    return {
        "schemaVersion": schema_version,
        "receiptId": receipt_id,
        "taskId": task_id,
        "sourceFingerprint": source_fingerprint,
        "providerId": provider_id,
        "status": status,
        "live": live,
        "scope": dict(scope),
        "recordCount": record_count,
        "acknowledged": acknowledged,
        "blockers": sorted(set(blockers)),
        "sideEffects": [],
        "generatedAt": _now(),
    }


def validate_rag_receipt(value: Any, *, writeback: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["ragReceipt: expected object"]}
    expected_schema = WRITEBACK_RECEIPT_SCHEMA_VERSION if writeback else RETRIEVAL_RECEIPT_SCHEMA_VERSION
    required = {"schemaVersion", "receiptId", "taskId", "sourceFingerprint", "providerId", "status", "live", "scope", "recordCount", "acknowledged", "blockers", "sideEffects", "generatedAt"}
    errors.extend(f"ragReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"ragReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != expected_schema:
        errors.append(f"ragReceipt.schemaVersion: expected {expected_schema}")
    for key in ("receiptId", "taskId", "sourceFingerprint", "providerId", "generatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"ragReceipt.{key}: expected non-empty string")
    allowed_status = {"RETRIEVED", "NO_MATCH", "FAILED", "CAPABILITY_UNAVAILABLE", "REJECTED"} if not writeback else {"ACKNOWLEDGED", "REJECTED", "FAILED", "CAPABILITY_UNAVAILABLE"}
    if value.get("status") not in allowed_status:
        errors.append("ragReceipt.status: unsupported value")
    if not isinstance(value.get("live"), bool) or not isinstance(value.get("acknowledged"), bool):
        errors.append("ragReceipt.live/acknowledged: expected booleans")
    scope = value.get("scope")
    if not isinstance(scope, Mapping) or set(scope) != {"projectId", "repository", "branchOrTree", "accessScope", "tenantId"}:
        errors.append("ragReceipt.scope: expected exact project/repository/access scope")
    if not isinstance(value.get("recordCount"), int) or value["recordCount"] < 0:
        errors.append("ragReceipt.recordCount: expected non-negative integer")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value["blockers"]):
        errors.append("ragReceipt.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("ragReceipt.sideEffects: must be empty")
    if writeback and value.get("status") == "ACKNOWLEDGED" and value.get("acknowledged") is not True:
        errors.append("ragReceipt: acknowledged writeback requires acknowledged=true")
    if not writeback and value.get("acknowledged") is not False:
        errors.append("ragReceipt: retrieval receipt cannot acknowledge writeback")
    return {"valid": not errors, "errors": errors}


def _scope(request: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    required = {"projectId", "repository", "branchOrTree", "accessScope", "tenantId"}
    errors: list[str] = []
    if not required.issubset(request):
        errors.extend(f"request: missing scope field {key}" for key in sorted(required - set(request)))
    scope = {key: request.get(key) for key in required}
    for key in ("projectId", "repository", "branchOrTree"):
        if not isinstance(scope[key], str) or not scope[key].strip():
            errors.append(f"request.{key}: expected non-empty string")
    if not isinstance(scope["accessScope"], list) or not scope["accessScope"] or not all(isinstance(item, str) and item.strip() for item in scope["accessScope"]):
        errors.append("request.accessScope: expected non-empty string array")
    if scope["tenantId"] is not None and (not isinstance(scope["tenantId"], str) or not scope["tenantId"].strip()):
        errors.append("request.tenantId: expected non-empty string or null")
    return scope, errors


class RAGProviderBridge:
    """Run provider calls only through an explicit authenticated host transport."""

    def __init__(self, manifest: Mapping[str, Any], transport: RAGProviderTransport | None = None):
        result = validate_rag_manifest(manifest)
        if not result["valid"]:
            raise RAGBridgeError("invalid RAG provider manifest: " + "; ".join(result["errors"]))
        self.manifest = dict(manifest)
        self.transport = transport

    @property
    def provider_id(self) -> str:
        return str(self.manifest["providerId"])

    def retrieve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        scope, errors = _scope(request if isinstance(request, Mapping) else {})
        forbidden = {"rawPrompt", "transcript", "token", "password", "secret", "unredacted"}
        if isinstance(request, Mapping):
            errors.extend(f"request.{key}: forbidden raw field" for key in request if str(key).casefold() in forbidden)
        if errors:
            receipt = _receipt(schema_version=RETRIEVAL_RECEIPT_SCHEMA_VERSION, receipt_id="rag:rejected", task_id=str(request.get("taskId", "unknown-task")) if isinstance(request, Mapping) else "unknown-task", source_fingerprint=str(request.get("sourceFingerprint", "unknown-source")) if isinstance(request, Mapping) else "unknown-source", provider_id=self.provider_id, status="REJECTED", live=False, scope=scope, blockers=errors)
            return {"outcome": "invalid", "records": [], "receipt": receipt, "providerAttempted": False}
        task_id = str(request.get("taskId", "unknown-task"))
        source_fingerprint = str(request.get("sourceFingerprint", "unknown-source"))
        if self.manifest["status"] != "AVAILABLE" or self.manifest["authState"] != "AUTHENTICATED" or self.transport is None:
            reason = "RAG provider is unavailable or transport is not configured"
            receipt = _receipt(schema_version=RETRIEVAL_RECEIPT_SCHEMA_VERSION, receipt_id=f"{task_id}:rag-unavailable", task_id=task_id, source_fingerprint=source_fingerprint, provider_id=self.provider_id, status="CAPABILITY_UNAVAILABLE", live=False, scope=scope, blockers=[reason])
            validation = validate_rag_receipt(receipt)
            if not validation["valid"]:
                raise RAGBridgeError("RAG receipt failed validation: " + "; ".join(validation["errors"]))
            return {"outcome": "unavailable", "records": [], "receipt": receipt, "providerAttempted": False, "sourceFirst": True}
        outbound = {key: request[key] for key in ("taskId", "sourceFingerprint", "query", "fingerprints", "taskType") if key in request}
        outbound.update(scope)
        try:
            response = self.transport.retrieve(outbound)
        except Exception as exc:
            response = {"status": "FAILED", "blockers": [f"RAG retrieval failed: {type(exc).__name__}"], "records": []}
        if not isinstance(response, Mapping):
            response = {"status": "FAILED", "blockers": ["RAG provider returned a non-object response"], "records": []}
        status = response.get("status") if response.get("status") in {"RETRIEVED", "NO_MATCH", "FAILED"} else "FAILED"
        blockers = [str(item) for item in response.get("blockers", []) if isinstance(item, str) and item.strip()]
        records = response.get("records", [])
        if not isinstance(records, list):
            records = []
            blockers.append("RAG provider records must be an array")
        sanitized_records = [record for record in records if isinstance(record, Mapping)]
        receipt = _receipt(schema_version=RETRIEVAL_RECEIPT_SCHEMA_VERSION, receipt_id=f"{task_id}:{self.provider_id}:retrieval", task_id=task_id, source_fingerprint=source_fingerprint, provider_id=self.provider_id, status=status, live=True, scope=scope, blockers=blockers, record_count=len(sanitized_records))
        validation = validate_rag_receipt(receipt)
        if not validation["valid"]:
            raise RAGBridgeError("RAG retrieval receipt failed validation: " + "; ".join(validation["errors"]))
        return {"outcome": "matched" if status == "RETRIEVED" and sanitized_records else "no_match" if status == "NO_MATCH" else "failed", "records": sanitized_records, "receipt": receipt, "providerAttempted": True, "sourceFirst": False}

    def writeback(self, request: Mapping[str, Any], *, authorized: bool = False) -> dict[str, Any]:
        scope, errors = _scope(request if isinstance(request, Mapping) else {})
        if not authorized:
            errors.append("writeback requires explicit AE authorization")
        task_id = str(request.get("taskId", "unknown-task")) if isinstance(request, Mapping) else "unknown-task"
        source_fingerprint = str(request.get("sourceFingerprint", "unknown-source")) if isinstance(request, Mapping) else "unknown-source"
        if errors or self.manifest["status"] != "AVAILABLE" or self.manifest["authState"] != "AUTHENTICATED" or self.transport is None:
            reason = errors or ["RAG writeback provider is unavailable or transport is not configured"]
            receipt = _receipt(schema_version=WRITEBACK_RECEIPT_SCHEMA_VERSION, receipt_id=f"{task_id}:rag-writeback-blocked", task_id=task_id, source_fingerprint=source_fingerprint, provider_id=self.provider_id, status="CAPABILITY_UNAVAILABLE" if not errors else "REJECTED", live=False, scope=scope, blockers=reason)
            validation = validate_rag_receipt(receipt, writeback=True)
            if not validation["valid"]:
                raise RAGBridgeError("RAG writeback receipt failed validation: " + "; ".join(validation["errors"]))
            return {"outcome": "blocked", "receipt": receipt, "providerAttempted": False}
        outbound = {key: request[key] for key in ("taskId", "sourceFingerprint", "recordId", "contentHash", "evidence") if key in request}
        outbound.update(scope)
        try:
            response = self.transport.writeback(outbound)
        except Exception as exc:
            response = {"status": "FAILED", "blockers": [f"RAG writeback failed: {type(exc).__name__}"]}
        if not isinstance(response, Mapping):
            response = {"status": "FAILED", "blockers": ["RAG provider returned a non-object writeback response"]}
        status = response.get("status") if response.get("status") in {"ACKNOWLEDGED", "REJECTED", "FAILED"} else "FAILED"
        acknowledged = status == "ACKNOWLEDGED" and response.get("acknowledged") is True
        if status == "ACKNOWLEDGED" and not acknowledged:
            status = "FAILED"
        blockers = [str(item) for item in response.get("blockers", []) if isinstance(item, str) and item.strip()]
        if status == "FAILED" and not blockers:
            blockers.append("provider did not acknowledge writeback")
        receipt = _receipt(schema_version=WRITEBACK_RECEIPT_SCHEMA_VERSION, receipt_id=f"{task_id}:{self.provider_id}:writeback", task_id=task_id, source_fingerprint=source_fingerprint, provider_id=self.provider_id, status=status, live=True, scope=scope, blockers=blockers, acknowledged=acknowledged)
        validation = validate_rag_receipt(receipt, writeback=True)
        if not validation["valid"]:
            raise RAGBridgeError("RAG writeback receipt failed validation: " + "; ".join(validation["errors"]))
        return {"outcome": "acknowledged" if acknowledged else "failed", "receipt": receipt, "providerAttempted": True}


def unavailable_rag_manifest(*, provider_id: str = "rag-provider-unavailable", host_id: str = "host-unavailable") -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "providerId": provider_id,
        "hostId": host_id,
        "version": "0.0.0",
        "status": "UNAVAILABLE",
        "authState": "UNAVAILABLE",
        "capabilities": ["retrieve", "writeback", "freshness", "access_evidence"],
        "privacyModel": "REDACTED",
        "sourceBound": True,
        "externalSideEffects": "HOST_CONTROLLED",
    }
