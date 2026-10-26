#!/usr/bin/env python3
"""Pure contracts for review shards, finding state, and source closure."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


SHARD_SCHEMA_VERSION = "review-shard-manifest-v1"
FINDING_SCHEMA_VERSION = "review-finding-ledger-v1"
REUSE_SCHEMA_VERSION = "review-coverage-reuse-decision-v1"
DELTA_SCHEMA_VERSION = "review-delta-packet-v1"
RISK_AUTHORIZATION_SCHEMA_VERSION = "review-risk-authorization-v1"
RESOLUTION_RECEIPT_SCHEMA_VERSION = "review-finding-resolution-v1"

REVIEW_LENSES = {"work", "architecture", "security", "runtime", "visual"}
SHARD_STATUSES = {"PENDING", "PASS", "FINDINGS", "MISSING_COVERAGE", "BLOCKED", "NOT_REQUIRED"}
FINDING_STATES = {
    "OPEN",
    "ACCEPTED_FOR_REPAIR",
    "REPAIR_IN_PROGRESS",
    "DELTA_REVIEW_PENDING",
    "RESOLVED",
    "ACCEPTED_RISK",
    "BLOCKED",
    "DUPLICATE",
}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
TERMINAL_FINDING_STATES = {"RESOLVED", "ACCEPTED_RISK", "DUPLICATE"}
TRANSITIONS = {
    "OPEN": {"ACCEPTED_FOR_REPAIR", "DUPLICATE"},
    "ACCEPTED_FOR_REPAIR": {"REPAIR_IN_PROGRESS"},
    "REPAIR_IN_PROGRESS": {"DELTA_REVIEW_PENDING"},
    "DELTA_REVIEW_PENDING": {"RESOLVED", "ACCEPTED_RISK", "BLOCKED"},
    "RESOLVED": set(),
    "ACCEPTED_RISK": set(),
    "BLOCKED": set(),
    "DUPLICATE": set(),
}

FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

MANIFEST_FIELDS = {
    "schemaVersion",
    "manifestId",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "acceptanceFingerprint",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "requiredLenses",
    "shards",
    "generatedAt",
}
SHARD_FIELDS = {
    "shardId",
    "lens",
    "required",
    "namedReviewRequired",
    "criteriaIds",
    "paths",
    "symbols",
    "dependencies",
    "evidenceRefs",
    "shardFingerprint",
    "status",
}
LEDGER_FIELDS = {
    "schemaVersion",
    "ledgerId",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "findings",
    "updatedAt",
}
FINDING_FIELDS = {
    "findingId",
    "ownerLens",
    "dependentLenses",
    "shardId",
    "severity",
    "state",
    "title",
    "evidenceRefs",
    "sourceReceiptIds",
    "repairScope",
    "resolutionKind",
    "duplicateOf",
    "riskAcceptance",
    "resolutionReceiptId",
    "resolutionReceiptDigest",
}
RISK_ACCEPTANCE_FIELDS = {
    "authorizationId",
    "authorizationDigest",
    "approvalId",
    "approverIdentity",
    "approverAuthority",
    "scope",
    "rationale",
    "decidedAt",
    "expiresAt",
    "nextReviewAt",
    "residualRisk",
}
RISK_AUTHORIZATION_FIELDS = {
    "schemaVersion",
    "authorizationId",
    "approvalId",
    "approverIdentity",
    "approverAuthority",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "findingId",
    "scope",
    "policyFingerprint",
    "issuerId",
    "issuedAt",
    "expiresAt",
    "terminal",
}
RESOLUTION_RECEIPT_FIELDS = {
    "schemaVersion",
    "receiptId",
    "receiptDigest",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "findingId",
    "shardId",
    "severity",
    "resolutionKind",
    "repairScope",
    "duplicateOf",
    "reviewerIdentity",
    "reviewerAuthority",
    "evidenceRefs",
    "evidenceDigest",
    "policyFingerprint",
    "issuedAt",
    "expiresAt",
    "status",
    "terminal",
}


class ReviewContractError(ValueError):
    """Raised when an immutable finding transition is not allowed."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(f"{domain}\0{_canonical_json(value)}".encode("utf-8")).hexdigest()


def _timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected ISO-8601 timestamp")
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: expected ISO-8601 timestamp")
        return None
    if result.tzinfo is None:
        errors.append(f"{label}: timezone is required")
        return None
    return result


def _strict(value: Any, fields: set[str], label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    errors.extend(f"{label}: missing required field {key}" for key in sorted(fields - set(value)))
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - fields))
    return value


def _string(value: Any, label: str, errors: list[str], *, allow_null: bool = False) -> None:
    if allow_null and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _fingerprint(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not FINGERPRINT_RE.fullmatch(value):
        errors.append(f"{label}: expected sha256:<64 lowercase hex>")


def _strings(value: Any, label: str, errors: list[str], *, unique: bool = True) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label}: expected non-empty strings")
        return []
    if unique and len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    return list(value)


def _relative_path(path: str) -> bool:
    if not path or "\\" in path or any(ord(character) < 32 for character in path):
        return False
    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def _validate_paths(value: Any, label: str, errors: list[str]) -> list[str]:
    paths = _strings(value, label, errors)
    normalized: set[str] = set()
    for path in paths:
        if not _relative_path(path):
            errors.append(f"{label}: unsafe relative path {path!r}")
        folded = path.casefold()
        if folded in normalized:
            errors.append(f"{label}: case-colliding path {path!r}")
        normalized.add(folded)
    return paths


def compute_risk_authorization_id(authorization: Mapping[str, Any]) -> str:
    """Return the immutable identity for one host-issued risk authorization."""

    normalized = {
        key: value for key, value in authorization.items() if key != "authorizationId"
    }
    return "risk-auth-" + _digest(
        RISK_AUTHORIZATION_SCHEMA_VERSION, normalized
    ).split(":", 1)[1][:40]


def compute_risk_authorization_digest(authorization: Mapping[str, Any]) -> str:
    return _digest(RISK_AUTHORIZATION_SCHEMA_VERSION, authorization)


def validate_risk_authorization(
    value: Any,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate a host/controller supplied accepted-risk authorization."""

    errors: list[str] = []
    authorization = _strict(
        value, RISK_AUTHORIZATION_FIELDS, "riskAuthorization", errors
    )
    if authorization is None:
        return {"valid": False, "errors": errors}
    if authorization.get("schemaVersion") != RISK_AUTHORIZATION_SCHEMA_VERSION:
        errors.append(
            f"riskAuthorization.schemaVersion: expected {RISK_AUTHORIZATION_SCHEMA_VERSION}"
        )
    for key in (
        "authorizationId",
        "approvalId",
        "approverIdentity",
        "approverAuthority",
        "taskId",
        "projectId",
        "repositoryId",
        "sourceFingerprint",
        "findingId",
        "issuerId",
    ):
        _string(authorization.get(key), f"riskAuthorization.{key}", errors)
    _string(
        authorization.get("tenantId"),
        "riskAuthorization.tenantId",
        errors,
        allow_null=True,
    )
    _fingerprint(
        authorization.get("overallDiffFingerprint"),
        "riskAuthorization.overallDiffFingerprint",
        errors,
    )
    _fingerprint(
        authorization.get("policyFingerprint"),
        "riskAuthorization.policyFingerprint",
        errors,
    )
    scope = _validate_paths(
        authorization.get("scope"), "riskAuthorization.scope", errors
    )
    if not scope:
        errors.append("riskAuthorization.scope: expected bounded non-empty scope")
    issued = _timestamp(
        authorization.get("issuedAt"), "riskAuthorization.issuedAt", errors
    )
    expires = _timestamp(
        authorization.get("expiresAt"), "riskAuthorization.expiresAt", errors
    )
    current = _timestamp(now, "now", errors) if now is not None else None
    if issued is not None and expires is not None and expires <= issued:
        errors.append("riskAuthorization: expiresAt must be after issuedAt")
    if current is not None and issued is not None and current < issued:
        errors.append("riskAuthorization: issuedAt is in the future")
    if current is not None and expires is not None and current >= expires:
        errors.append("riskAuthorization: authorization is expired")
    if authorization.get("terminal") is not True:
        errors.append("riskAuthorization.terminal: must be exactly true")
    if authorization.get("authorizationId") != compute_risk_authorization_id(
        authorization
    ):
        errors.append("riskAuthorization.authorizationId: does not match content")
    return {"valid": not errors, "errors": errors}


def _resolution_receipt_body(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: receipt.get(key)
        for key in sorted(RESOLUTION_RECEIPT_FIELDS - {"receiptId", "receiptDigest"})
    }


def compute_resolution_receipt_id(receipt: Mapping[str, Any]) -> str:
    """Return the immutable identity for a host-issued terminal finding receipt."""

    return "finding-resolution-" + _digest(
        f"{RESOLUTION_RECEIPT_SCHEMA_VERSION}-id", _resolution_receipt_body(receipt)
    ).split(":", 1)[1][:40]


def compute_resolution_receipt_digest(receipt: Mapping[str, Any]) -> str:
    body = _resolution_receipt_body(receipt)
    body["receiptId"] = receipt.get("receiptId")
    return _digest(RESOLUTION_RECEIPT_SCHEMA_VERSION, body)


def compute_resolution_evidence_digest(evidence_refs: Sequence[str]) -> str:
    return _digest("review-finding-resolution-evidence-v1", sorted(evidence_refs))


def validate_resolution_receipt(
    value: Any,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate a trusted fixed-repair or duplicate-equivalence receipt."""

    errors: list[str] = []
    receipt = _strict(
        value, RESOLUTION_RECEIPT_FIELDS, "resolutionReceipt", errors
    )
    if receipt is None:
        return {"valid": False, "errors": errors}
    if receipt.get("schemaVersion") != RESOLUTION_RECEIPT_SCHEMA_VERSION:
        errors.append(
            "resolutionReceipt.schemaVersion: expected "
            + RESOLUTION_RECEIPT_SCHEMA_VERSION
        )
    for key in (
        "receiptId",
        "taskId",
        "projectId",
        "repositoryId",
        "sourceFingerprint",
        "findingId",
        "shardId",
        "reviewerIdentity",
    ):
        _string(receipt.get(key), f"resolutionReceipt.{key}", errors)
    _string(
        receipt.get("tenantId"),
        "resolutionReceipt.tenantId",
        errors,
        allow_null=True,
    )
    for key in (
        "receiptDigest",
        "overallDiffFingerprint",
        "evidenceDigest",
        "policyFingerprint",
    ):
        _fingerprint(receipt.get(key), f"resolutionReceipt.{key}", errors)
    if receipt.get("severity") not in SEVERITIES:
        errors.append("resolutionReceipt.severity: unsupported value")
    resolution_kind = receipt.get("resolutionKind")
    if resolution_kind not in {"fixed", "duplicate"}:
        errors.append("resolutionReceipt.resolutionKind: expected fixed or duplicate")
    scope = _validate_paths(
        receipt.get("repairScope"), "resolutionReceipt.repairScope", errors
    )
    duplicate_of = receipt.get("duplicateOf")
    if resolution_kind == "fixed":
        if not scope:
            errors.append("resolutionReceipt.repairScope: fixed repair requires bounded scope")
        if duplicate_of is not None:
            errors.append("resolutionReceipt.duplicateOf: fixed repair must use null")
    elif resolution_kind == "duplicate":
        if scope:
            errors.append("resolutionReceipt.repairScope: duplicate receipt must use empty scope")
        _string(duplicate_of, "resolutionReceipt.duplicateOf", errors)
    if receipt.get("reviewerAuthority") not in {"review-host", "controller"}:
        errors.append("resolutionReceipt.reviewerAuthority: unsupported authority")
    evidence_refs = _strings(
        receipt.get("evidenceRefs"), "resolutionReceipt.evidenceRefs", errors
    )
    if not evidence_refs:
        errors.append("resolutionReceipt.evidenceRefs: trusted evidence is required")
    elif receipt.get("evidenceDigest") != compute_resolution_evidence_digest(
        evidence_refs
    ):
        errors.append("resolutionReceipt.evidenceDigest: does not match evidenceRefs")
    issued = _timestamp(receipt.get("issuedAt"), "resolutionReceipt.issuedAt", errors)
    expires = _timestamp(receipt.get("expiresAt"), "resolutionReceipt.expiresAt", errors)
    current = _timestamp(now, "now", errors) if now is not None else None
    if issued is not None and expires is not None and expires <= issued:
        errors.append("resolutionReceipt: expiresAt must be after issuedAt")
    if current is not None and issued is not None and current < issued:
        errors.append("resolutionReceipt: issuedAt is in the future")
    if current is not None and expires is not None and current >= expires:
        errors.append("resolutionReceipt: receipt is expired")
    if receipt.get("status") != "PASS":
        errors.append("resolutionReceipt.status: expected PASS")
    if receipt.get("terminal") is not True:
        errors.append("resolutionReceipt.terminal: must be exactly true")
    if receipt.get("receiptId") != compute_resolution_receipt_id(receipt):
        errors.append("resolutionReceipt.receiptId: does not match content")
    if receipt.get("receiptDigest") != compute_resolution_receipt_digest(receipt):
        errors.append("resolutionReceipt.receiptDigest: does not match content")
    return {"valid": not errors, "errors": errors}


def compute_shard_fingerprint(shard: Mapping[str, Any]) -> str:
    """Fingerprint stable coverage scope; mutable result/evidence are excluded."""

    if not isinstance(shard, Mapping):
        raise ReviewContractError("reviewShard: expected object")
    stable = {
        "shardId": shard.get("shardId"),
        "lens": shard.get("lens"),
        "required": shard.get("required"),
        "namedReviewRequired": shard.get("namedReviewRequired"),
        "criteriaIds": sorted(shard.get("criteriaIds", [])),
        "paths": sorted(shard.get("paths", [])),
        "symbols": sorted(shard.get("symbols", [])),
        "dependencies": sorted(shard.get("dependencies", [])),
    }
    return _digest("review-shard-scope-v1", stable)


def _validate_shard(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    shard = _strict(value, SHARD_FIELDS, label, errors)
    if shard is None:
        return None
    _string(shard.get("shardId"), f"{label}.shardId", errors)
    if shard.get("lens") not in REVIEW_LENSES:
        errors.append(f"{label}.lens: unsupported review lens")
    if not isinstance(shard.get("required"), bool):
        errors.append(f"{label}.required: expected boolean")
    if not isinstance(shard.get("namedReviewRequired"), bool):
        errors.append(f"{label}.namedReviewRequired: expected boolean")
    if shard.get("namedReviewRequired") is True and shard.get("required") is not True:
        errors.append(f"{label}.namedReviewRequired: named review requires a required shard")
    _strings(shard.get("criteriaIds"), f"{label}.criteriaIds", errors)
    _validate_paths(shard.get("paths"), f"{label}.paths", errors)
    _strings(shard.get("symbols"), f"{label}.symbols", errors)
    dependencies = _strings(shard.get("dependencies"), f"{label}.dependencies", errors)
    _strings(shard.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
    _fingerprint(shard.get("shardFingerprint"), f"{label}.shardFingerprint", errors)
    if shard.get("status") not in SHARD_STATUSES:
        errors.append(f"{label}.status: unsupported value")
    if shard.get("shardFingerprint") != compute_shard_fingerprint(shard):
        errors.append(f"{label}.shardFingerprint: does not match stable shard scope")
    if shard.get("shardId") in dependencies:
        errors.append(f"{label}.dependencies: shard cannot depend on itself")
    if shard.get("required") is False and shard.get("status") != "NOT_REQUIRED":
        errors.append(f"{label}.status: optional shard must be NOT_REQUIRED")
    if shard.get("status") in {"PASS", "FINDINGS"} and not shard.get("evidenceRefs"):
        errors.append(f"{label}.evidenceRefs: completed shard requires evidence")
    return shard


def validate_review_shard_manifest(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    manifest = _strict(value, MANIFEST_FIELDS, "reviewManifest", errors)
    if manifest is None:
        return {"valid": False, "errors": errors}
    if manifest.get("schemaVersion") != SHARD_SCHEMA_VERSION:
        errors.append(f"reviewManifest.schemaVersion: expected {SHARD_SCHEMA_VERSION}")
    for key in ("manifestId", "taskId", "projectId", "repositoryId", "sourceFingerprint"):
        _string(manifest.get(key), f"reviewManifest.{key}", errors)
    _string(manifest.get("tenantId"), "reviewManifest.tenantId", errors, allow_null=True)
    _fingerprint(manifest.get("acceptanceFingerprint"), "reviewManifest.acceptanceFingerprint", errors)
    _fingerprint(manifest.get("overallDiffFingerprint"), "reviewManifest.overallDiffFingerprint", errors)
    required_lenses = _strings(manifest.get("requiredLenses"), "reviewManifest.requiredLenses", errors)
    for lens in required_lenses:
        if lens not in REVIEW_LENSES:
            errors.append(f"reviewManifest.requiredLenses: unsupported lens {lens!r}")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        errors.append("reviewManifest.shards: expected non-empty array")
        shards = []
    shard_ids: list[str] = []
    lenses: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    for index, raw_shard in enumerate(shards):
        shard = _validate_shard(raw_shard, f"reviewManifest.shards[{index}]", errors)
        if shard is not None:
            shard_id = shard.get("shardId")
            if isinstance(shard_id, str):
                shard_ids.append(shard_id)
                dependencies[shard_id] = list(shard.get("dependencies", []))
            if shard.get("required") is True and isinstance(shard.get("lens"), str):
                lenses.add(shard["lens"])
    if len(shard_ids) != len(set(shard_ids)):
        errors.append("reviewManifest.shards: duplicate shardId")
    known = set(shard_ids)
    for shard_id, refs in dependencies.items():
        for dependency in refs:
            if dependency not in known:
                errors.append(f"reviewManifest.shards[{shard_id}]: unknown dependency {dependency}")
    if set(required_lenses) - lenses:
        errors.append("reviewManifest.requiredLenses: each required lens needs a required shard")
    _timestamp(manifest.get("generatedAt"), "reviewManifest.generatedAt", errors)
    return {"valid": not errors, "errors": errors}


def _validate_risk_acceptance(value: Any, label: str, errors: list[str], *, now: str | None = None) -> None:
    acceptance = _strict(value, RISK_ACCEPTANCE_FIELDS, label, errors)
    if acceptance is None:
        return
    for key in (
        "authorizationId",
        "approvalId",
        "approverIdentity",
        "approverAuthority",
        "rationale",
    ):
        _string(acceptance.get(key), f"{label}.{key}", errors)
    _fingerprint(
        acceptance.get("authorizationDigest"),
        f"{label}.authorizationDigest",
        errors,
    )
    scope = _validate_paths(acceptance.get("scope"), f"{label}.scope", errors)
    if not scope:
        errors.append(f"{label}.scope: expected bounded non-empty scope")
    residual = _strings(acceptance.get("residualRisk"), f"{label}.residualRisk", errors)
    if not residual:
        errors.append(f"{label}.residualRisk: expected non-empty residual risk")
    decided = _timestamp(acceptance.get("decidedAt"), f"{label}.decidedAt", errors)
    expires = _timestamp(acceptance.get("expiresAt"), f"{label}.expiresAt", errors)
    next_review = _timestamp(acceptance.get("nextReviewAt"), f"{label}.nextReviewAt", errors)
    current = _timestamp(now, "now", errors) if now is not None else None
    if decided is not None and expires is not None and expires <= decided:
        errors.append(f"{label}: expiresAt must be after decidedAt")
    if decided is not None and next_review is not None and next_review <= decided:
        errors.append(f"{label}: nextReviewAt must be after decidedAt")
    if expires is not None and next_review is not None and next_review > expires:
        errors.append(f"{label}: nextReviewAt must not exceed expiresAt")
    if current is not None and expires is not None and current >= expires:
        errors.append(f"{label}: risk acceptance is expired")


def _validate_risk_authority_binding(
    finding: Mapping[str, Any],
    ledger: Mapping[str, Any],
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None,
    errors: list[str],
    *,
    now: str | None = None,
) -> None:
    label = f"findingLedger.findings[{finding.get('findingId')}].riskAcceptance"
    acceptance = finding.get("riskAcceptance")
    if not isinstance(acceptance, Mapping):
        return
    authorization_id = acceptance.get("authorizationId")
    if not isinstance(trusted_risk_authorizations, Mapping):
        errors.append(f"{label}: trusted risk authorization lookup is required")
        return
    authorization = trusted_risk_authorizations.get(authorization_id)
    if not isinstance(authorization, Mapping):
        errors.append(f"{label}: trusted risk authorization is missing")
        return
    authorization_result = validate_risk_authorization(authorization, now=now)
    errors.extend(
        f"{label}: {error}" for error in authorization_result.get("errors", [])
    )
    if acceptance.get("authorizationDigest") != compute_risk_authorization_digest(
        authorization
    ):
        errors.append(f"{label}: authorization digest does not match trusted record")
    for key in (
        "approvalId",
        "approverIdentity",
        "approverAuthority",
    ):
        if acceptance.get(key) != authorization.get(key):
            errors.append(f"{label}.{key}: does not match trusted authorization")
    for key in (
        "taskId",
        "projectId",
        "repositoryId",
        "tenantId",
        "sourceFingerprint",
        "overallDiffFingerprint",
    ):
        if ledger.get(key) != authorization.get(key):
            errors.append(f"{label}: authorization {key} does not match ledger")
    if authorization.get("findingId") != finding.get("findingId"):
        errors.append(f"{label}: authorization findingId does not match finding")
    accepted_scope = acceptance.get("scope")
    repair_scope = finding.get("repairScope")
    authorization_scope = authorization.get("scope")
    if not (
        isinstance(accepted_scope, list)
        and isinstance(repair_scope, list)
        and isinstance(authorization_scope, list)
        and accepted_scope == repair_scope == authorization_scope
    ):
        errors.append(
            f"{label}.scope: accepted, repair, and trusted authorization scopes must match exactly"
        )
    accepted_expiry = _timestamp(
        acceptance.get("expiresAt"), f"{label}.expiresAtBinding", errors
    )
    authorization_expiry = _timestamp(
        authorization.get("expiresAt"), f"{label}.authorizationExpiresAt", errors
    )
    if (
        accepted_expiry is not None
        and authorization_expiry is not None
        and accepted_expiry > authorization_expiry
    ):
        errors.append(f"{label}.expiresAt: exceeds trusted authorization expiry")


def _validate_resolution_receipt_binding(
    finding: Mapping[str, Any],
    ledger: Mapping[str, Any],
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None,
    errors: list[str],
    *,
    now: str | None = None,
) -> None:
    label = f"findingLedger.findings[{finding.get('findingId')}].resolutionReceipt"
    receipt_id = finding.get("resolutionReceiptId")
    receipt_digest = finding.get("resolutionReceiptDigest")
    if not isinstance(trusted_resolution_receipts, Mapping):
        errors.append(f"{label}: trusted resolution receipt lookup is required")
        return
    receipt = trusted_resolution_receipts.get(receipt_id)
    if not isinstance(receipt, Mapping):
        errors.append(f"{label}: trusted resolution receipt is missing")
        return
    result = validate_resolution_receipt(receipt, now=now)
    errors.extend(f"{label}: {error}" for error in result.get("errors", []))
    if receipt_digest != compute_resolution_receipt_digest(receipt):
        errors.append(f"{label}: digest does not match trusted receipt")
    for key in (
        "taskId",
        "projectId",
        "repositoryId",
        "tenantId",
        "sourceFingerprint",
        "overallDiffFingerprint",
    ):
        if ledger.get(key) != receipt.get(key):
            errors.append(f"{label}: receipt {key} does not match ledger")
    expected_kind = "fixed" if finding.get("state") == "RESOLVED" else "duplicate"
    for key in ("findingId", "shardId", "severity"):
        if finding.get(key) != receipt.get(key):
            errors.append(f"{label}: receipt {key} does not match finding")
    if receipt.get("resolutionKind") != expected_kind:
        errors.append(f"{label}: receipt resolutionKind does not match terminal state")
    if receipt.get("repairScope") != finding.get("repairScope"):
        errors.append(f"{label}: receipt repairScope does not match finding")
    if receipt.get("duplicateOf") != finding.get("duplicateOf"):
        errors.append(f"{label}: receipt duplicateOf does not match finding")
    if receipt.get("evidenceRefs") != finding.get("evidenceRefs"):
        errors.append(f"{label}: receipt evidenceRefs do not match finding")
    source_receipts = finding.get("sourceReceiptIds")
    if not isinstance(source_receipts, list) or receipt_id not in source_receipts:
        errors.append(f"{label}: receiptId is not bound in sourceReceiptIds")


def _validate_finding(value: Any, label: str, errors: list[str], *, now: str | None = None) -> Mapping[str, Any] | None:
    finding = _strict(value, FINDING_FIELDS, label, errors)
    if finding is None:
        return None
    for key in ("findingId", "shardId", "title"):
        _string(finding.get(key), f"{label}.{key}", errors)
    owner = finding.get("ownerLens")
    if owner not in REVIEW_LENSES:
        errors.append(f"{label}.ownerLens: unsupported review lens")
    dependents = _strings(finding.get("dependentLenses"), f"{label}.dependentLenses", errors)
    for lens in dependents:
        if lens not in REVIEW_LENSES:
            errors.append(f"{label}.dependentLenses: unsupported lens {lens!r}")
    if owner in dependents:
        errors.append(f"{label}.dependentLenses: owner lens cannot depend on itself")
    if finding.get("severity") not in SEVERITIES:
        errors.append(f"{label}.severity: unsupported value")
    state = finding.get("state")
    if state not in FINDING_STATES:
        errors.append(f"{label}.state: unsupported value")
    evidence = _strings(finding.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
    receipts = _strings(finding.get("sourceReceiptIds"), f"{label}.sourceReceiptIds", errors)
    if not evidence or not receipts:
        errors.append(f"{label}: evidenceRefs and sourceReceiptIds must be non-empty")
    repair_scope = _validate_paths(finding.get("repairScope"), f"{label}.repairScope", errors)
    resolution = finding.get("resolutionKind")
    duplicate_of = finding.get("duplicateOf")
    risk = finding.get("riskAcceptance")
    resolution_receipt_id = finding.get("resolutionReceiptId")
    resolution_receipt_digest = finding.get("resolutionReceiptDigest")
    if state not in {"RESOLVED", "DUPLICATE"} and (
        resolution_receipt_id is not None or resolution_receipt_digest is not None
    ):
        errors.append(f"{label}: state cannot contain terminal resolution receipt")

    if state in {"ACCEPTED_FOR_REPAIR", "REPAIR_IN_PROGRESS", "DELTA_REVIEW_PENDING", "RESOLVED", "ACCEPTED_RISK", "BLOCKED"} and not repair_scope:
        errors.append(f"{label}.repairScope: repair lifecycle state requires non-empty scope")
    if state == "RESOLVED":
        if resolution != "fixed":
            errors.append(f"{label}.resolutionKind: RESOLVED requires fixed")
        if risk is not None or duplicate_of is not None:
            errors.append(f"{label}: resolved finding cannot contain risk or duplicate authority")
        _string(resolution_receipt_id, f"{label}.resolutionReceiptId", errors)
        _fingerprint(
            resolution_receipt_digest,
            f"{label}.resolutionReceiptDigest",
            errors,
        )
    elif state == "ACCEPTED_RISK":
        if resolution != "accepted-risk":
            errors.append(f"{label}.resolutionKind: ACCEPTED_RISK requires accepted-risk")
        if finding.get("severity") not in {"MEDIUM", "LOW"}:
            errors.append(f"{label}.severity: CRITICAL/HIGH risk cannot be accepted")
        _validate_risk_acceptance(risk, f"{label}.riskAcceptance", errors, now=now)
        if isinstance(risk, Mapping):
            approved_scope = risk.get("scope")
            if isinstance(approved_scope, list) and approved_scope != repair_scope:
                errors.append(
                    f"{label}.riskAcceptance.scope: must match repairScope exactly"
                )
        if duplicate_of is not None:
            errors.append(f"{label}.duplicateOf: accepted risk cannot be duplicate")
    elif state == "BLOCKED":
        if resolution != "blocked":
            errors.append(f"{label}.resolutionKind: BLOCKED requires blocked")
        if risk is not None or duplicate_of is not None:
            errors.append(f"{label}: blocked finding cannot contain risk or duplicate authority")
    elif state == "DUPLICATE":
        if resolution != "duplicate":
            errors.append(f"{label}.resolutionKind: DUPLICATE requires duplicate")
        _string(duplicate_of, f"{label}.duplicateOf", errors)
        if risk is not None or repair_scope:
            errors.append(f"{label}: duplicate finding cannot contain repair/risk state")
        _string(resolution_receipt_id, f"{label}.resolutionReceiptId", errors)
        _fingerprint(
            resolution_receipt_digest,
            f"{label}.resolutionReceiptDigest",
            errors,
        )
    else:
        if resolution is not None or duplicate_of is not None or risk is not None:
            errors.append(f"{label}: non-terminal repair state cannot contain resolution authority")
    return finding


def validate_finding_ledger(
    value: Any,
    *,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    ledger = _strict(value, LEDGER_FIELDS, "findingLedger", errors)
    if ledger is None:
        return {"valid": False, "errors": errors}
    if ledger.get("schemaVersion") != FINDING_SCHEMA_VERSION:
        errors.append(f"findingLedger.schemaVersion: expected {FINDING_SCHEMA_VERSION}")
    for key in ("ledgerId", "taskId", "projectId", "repositoryId", "sourceFingerprint"):
        _string(ledger.get(key), f"findingLedger.{key}", errors)
    _string(ledger.get("tenantId"), "findingLedger.tenantId", errors, allow_null=True)
    _fingerprint(ledger.get("overallDiffFingerprint"), "findingLedger.overallDiffFingerprint", errors)
    findings = ledger.get("findings")
    if not isinstance(findings, list):
        errors.append("findingLedger.findings: expected array")
        findings = []
    finding_ids: list[str] = []
    finding_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_finding in enumerate(findings):
        finding = _validate_finding(raw_finding, f"findingLedger.findings[{index}]", errors, now=now)
        if finding is not None and isinstance(finding.get("findingId"), str):
            finding_id = finding["findingId"]
            finding_ids.append(finding_id)
            finding_by_id[finding_id] = finding
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("findingLedger.findings: duplicate findingId")
    for finding_id, finding in finding_by_id.items():
        if finding.get("state") == "ACCEPTED_RISK":
            _validate_risk_authority_binding(
                finding,
                ledger,
                trusted_risk_authorizations,
                errors,
                now=now,
            )
        if finding.get("state") in {"RESOLVED", "DUPLICATE"}:
            _validate_resolution_receipt_binding(
                finding,
                ledger,
                trusted_resolution_receipts,
                errors,
                now=now,
            )
        if finding.get("state") != "DUPLICATE":
            continue
        seen = {finding_id}
        target_id = finding.get("duplicateOf")
        while isinstance(target_id, str):
            if target_id in seen:
                errors.append(f"findingLedger.findings[{finding_id}]: duplicate chain contains a cycle")
                break
            seen.add(target_id)
            target = finding_by_id.get(target_id)
            if target is None:
                errors.append(f"findingLedger.findings[{finding_id}]: duplicate target is missing")
                break
            target_state = target.get("state")
            if target_state == "DUPLICATE":
                target_id = target.get("duplicateOf")
                continue
            if target_state not in {"RESOLVED", "ACCEPTED_RISK"}:
                errors.append(
                    f"findingLedger.findings[{finding_id}]: duplicate chain does not end at a resolved terminal finding"
                )
            break
        else:
            errors.append(f"findingLedger.findings[{finding_id}]: duplicate target is invalid")
    _timestamp(ledger.get("updatedAt"), "findingLedger.updatedAt", errors)
    return {"valid": not errors, "errors": errors}


def transition_finding(
    finding: Mapping[str, Any],
    target_state: str,
    evidence: Mapping[str, Any] | None = None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Return a transitioned copy; never mutate the caller's finding."""

    if not isinstance(finding, Mapping):
        raise ReviewContractError("finding: expected object")
    current = finding.get("state")
    if current not in TRANSITIONS or target_state not in TRANSITIONS[current]:
        raise ReviewContractError(f"illegal finding transition: {current} -> {target_state}")
    data = copy.deepcopy(dict(finding))
    supplied = dict(evidence or {})
    if target_state == "ACCEPTED_FOR_REPAIR":
        repair_scope = supplied.get("repairScope")
        errors: list[str] = []
        if not _validate_paths(repair_scope, "transition.repairScope", errors):
            errors.append("transition.repairScope: expected non-empty repair scope")
        if errors:
            raise ReviewContractError("; ".join(errors))
        data["repairScope"] = list(repair_scope)
    elif target_state == "RESOLVED":
        if supplied.get("resolutionKind") != "fixed":
            raise ReviewContractError("RESOLVED transition requires resolutionKind=fixed")
        references = supplied.get("evidenceRefs")
        if not isinstance(references, list) or not references:
            raise ReviewContractError("RESOLVED transition requires evidenceRefs")
        receipt_id = supplied.get("resolutionReceiptId")
        receipt_digest = supplied.get("resolutionReceiptDigest")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ReviewContractError("RESOLVED transition requires resolutionReceiptId")
        if not isinstance(receipt_digest, str) or not FINGERPRINT_RE.fullmatch(receipt_digest):
            raise ReviewContractError("RESOLVED transition requires resolutionReceiptDigest")
        data["evidenceRefs"] = sorted(set(data.get("evidenceRefs", []) + list(references)))
        data["sourceReceiptIds"] = sorted(
            set(data.get("sourceReceiptIds", []) + [receipt_id])
        )
        data["resolutionKind"] = "fixed"
        data["resolutionReceiptId"] = receipt_id
        data["resolutionReceiptDigest"] = receipt_digest
    elif target_state == "ACCEPTED_RISK":
        errors = []
        _validate_risk_acceptance(supplied.get("riskAcceptance"), "transition.riskAcceptance", errors, now=now)
        if data.get("severity") not in {"MEDIUM", "LOW"}:
            errors.append("transition: CRITICAL/HIGH risk cannot be accepted")
        if errors:
            raise ReviewContractError("; ".join(errors))
        data["riskAcceptance"] = copy.deepcopy(supplied["riskAcceptance"])
        data["resolutionKind"] = "accepted-risk"
    elif target_state == "BLOCKED":
        data["resolutionKind"] = "blocked"
    elif target_state == "DUPLICATE":
        duplicate_of = supplied.get("duplicateOf")
        if not isinstance(duplicate_of, str) or not duplicate_of:
            raise ReviewContractError("DUPLICATE transition requires duplicateOf")
        receipt_id = supplied.get("resolutionReceiptId")
        receipt_digest = supplied.get("resolutionReceiptDigest")
        references = supplied.get("evidenceRefs")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ReviewContractError("DUPLICATE transition requires resolutionReceiptId")
        if not isinstance(receipt_digest, str) or not FINGERPRINT_RE.fullmatch(receipt_digest):
            raise ReviewContractError("DUPLICATE transition requires resolutionReceiptDigest")
        if not isinstance(references, list) or not references:
            raise ReviewContractError("DUPLICATE transition requires evidenceRefs")
        data["duplicateOf"] = duplicate_of
        data["resolutionKind"] = "duplicate"
        data["repairScope"] = []
        data["evidenceRefs"] = sorted(set(data.get("evidenceRefs", []) + list(references)))
        data["sourceReceiptIds"] = sorted(
            set(data.get("sourceReceiptIds", []) + [receipt_id])
        )
        data["resolutionReceiptId"] = receipt_id
        data["resolutionReceiptDigest"] = receipt_digest
    data["state"] = target_state
    return data


def _validate_named_review_closure_bundle(
    bundle: Any,
    *,
    manifest: Mapping[str, Any],
    shard: Mapping[str, Any],
    current_receipt_wrapper: Mapping[str, Any],
    now: str | None = None,
) -> list[str]:
    """Require live named provenance in addition to the generic shard receipt."""

    try:
        from .model_selection import validate_assignment
        from .named_reviewer import (
            artifact_digest,
            validate_authorization_evidence_envelope,
            validate_named_reviewer_assignment,
            validate_named_reviewer_receipt,
            validate_task_review_binding,
        )
    except ImportError as exc:  # pragma: no cover - package import boundary
        return [f"named review validators unavailable ({type(exc).__name__})"]

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
        return ["namedReviewBundle: expected object"]
    errors: list[str] = []
    errors.extend(
        f"namedReviewBundle: missing required field {key}"
        for key in sorted(fields - set(bundle))
    )
    errors.extend(
        f"namedReviewBundle: unknown field {key}"
        for key in sorted(set(bundle) - fields)
    )
    generic = bundle.get("genericAssignment")
    binding = bundle.get("taskReviewBinding")
    envelope = bundle.get("authorizationEnvelope")
    profile = bundle.get("roleProfile")
    named = bundle.get("namedAssignment")
    named_receipt = bundle.get("namedReceipt")
    observation = bundle.get("hostObservation")

    generic_result = validate_assignment(generic, now=now)
    errors.extend(
        f"genericAssignment: {error}" for error in generic_result.get("errors", [])
    )
    binding_result = validate_task_review_binding(binding, now=now)
    errors.extend(
        f"taskReviewBinding: {error}" for error in binding_result.get("errors", [])
    )
    envelope_result = validate_authorization_evidence_envelope(
        envelope,
        wrapped_artifact=generic if isinstance(generic, Mapping) else None,
        task_review_binding=binding if isinstance(binding, Mapping) else None,
        now=now,
    )
    errors.extend(
        f"authorizationEnvelope: {error}"
        for error in envelope_result.get("errors", [])
    )
    named_result = validate_named_reviewer_assignment(
        named,
        expected_generic_assignment=generic if isinstance(generic, Mapping) else None,
        expected_task_review_binding=binding if isinstance(binding, Mapping) else None,
        expected_authorization_envelope=envelope if isinstance(envelope, Mapping) else None,
        expected_role_profile=profile if isinstance(profile, Mapping) else None,
        now=now,
    )
    errors.extend(
        f"namedAssignment: {error}" for error in named_result.get("errors", [])
    )
    receipt_result = validate_named_reviewer_receipt(
        named_receipt,
        expected_assignment=named if isinstance(named, Mapping) else {},
        expected_host_observation=observation if isinstance(observation, Mapping) else None,
        now=now,
        require_live=True,
    )
    errors.extend(
        f"namedReceipt: {error}" for error in receipt_result.get("errors", [])
    )
    if receipt_result.get("liveReady") is not True:
        errors.append("namedReceipt: live completion evidence is required for this shard")

    for key in (
        "taskId",
        "projectId",
        "repositoryId",
        "tenantId",
        "sourceFingerprint",
        "overallDiffFingerprint",
    ):
        if isinstance(named, Mapping) and named.get(key) != manifest.get(key):
            errors.append(f"namedAssignment.{key}: does not match review manifest")
    if isinstance(named, Mapping):
        if named.get("shardFingerprint") != shard.get("shardFingerprint"):
            errors.append("namedAssignment.shardFingerprint: does not match required shard")
        scope = named.get("scope")
        if not isinstance(scope, Mapping) or scope.get("scopeId") != shard.get("shardId"):
            errors.append("namedAssignment.scope: does not match required shard identity")
    inner_receipt = current_receipt_wrapper.get("reviewReceipt")
    if isinstance(named_receipt, Mapping) and isinstance(inner_receipt, Mapping):
        expected_digest = artifact_digest("review-receipt-v2", inner_receipt)
        if named_receipt.get("baseReviewReceiptDigest") != expected_digest:
            errors.append("namedReceipt: does not supplement the current generic receipt")
    else:
        errors.append("namedReceipt: current generic receipt binding is unavailable")
    return errors


def validate_source_contract_closure(
    manifest: Mapping[str, Any],
    shard_results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_invalidation_plans: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Require evidence-backed closure for every required review shard."""

    errors: list[str] = []
    manifest_result = validate_review_shard_manifest(manifest)
    ledger_result = validate_finding_ledger(
        ledger,
        trusted_risk_authorizations=trusted_risk_authorizations,
        trusted_resolution_receipts=trusted_resolution_receipts,
        now=now,
    )
    errors.extend(f"manifest: {error}" for error in manifest_result["errors"])
    errors.extend(f"ledger: {error}" for error in ledger_result["errors"])
    for key in (
        "taskId",
        "projectId",
        "repositoryId",
        "tenantId",
        "sourceFingerprint",
        "overallDiffFingerprint",
    ):
        if manifest.get(key) != ledger.get(key):
            errors.append(f"closure: manifest and ledger {key} differ")
    if not isinstance(shard_results, Mapping):
        errors.append("closure.shardResults: expected object")
        shard_results = {}
    manifest_shards = {
        shard.get("shardId"): shard
        for shard in manifest.get("shards", [])
        if isinstance(shard, Mapping) and isinstance(shard.get("shardId"), str)
    }
    required_ids = sorted(
        shard_id
        for shard_id, shard in manifest_shards.items()
        if shard.get("required") is True
    )
    unexpected = set(shard_results) - set(manifest_shards)
    if unexpected:
        errors.append("closure.shardResults: contains unknown shard IDs")
    findings_by_id = {
        item.get("findingId"): item
        for item in ledger.get("findings", [])
        if isinstance(item, Mapping) and isinstance(item.get("findingId"), str)
    } if isinstance(ledger.get("findings"), list) else {}

    try:
        from .review_invalidation import (
            validate_invalidation_plan,
            validate_prior_review_receipt,
            validate_reuse_decision,
        )
    except ImportError as exc:  # pragma: no cover - package import boundary
        validate_prior_review_receipt = None
        validate_invalidation_plan = None
        validate_reuse_decision = None
        errors.append(f"closure: prior receipt validator unavailable ({type(exc).__name__})")

    for shard_id in required_ids:
        shard = manifest_shards[shard_id]
        evidence = shard_results.get(shard_id)
        label = f"closure.shardResults.{shard_id}"
        if not isinstance(evidence, Mapping):
            errors.append(f"{label}: required shard needs evidence object")
            continue
        evidence_type = evidence.get("evidenceType")
        named_required = shard.get("namedReviewRequired") is True
        if named_required and evidence_type != "CURRENT_RECEIPT":
            errors.append(
                f"{label}: named-review-required shard needs a current generic and live named receipt"
            )
            continue
        if evidence_type == "CURRENT_RECEIPT":
            expected_fields = (
                {"evidenceType", "receipt", "namedReviewBundle"}
                if named_required
                else {"evidenceType", "receipt"}
            )
            if set(evidence) != expected_fields:
                errors.append(f"{label}: invalid CURRENT_RECEIPT evidence shape")
                continue
            receipt = evidence.get("receipt")
            result = (
                validate_prior_review_receipt(receipt, manifest)
                if validate_prior_review_receipt is not None and isinstance(receipt, Mapping)
                else {"valid": False, "errors": ["receipt is unavailable"]}
            )
            errors.extend(f"{label}: {error}" for error in result.get("errors", []))
            if isinstance(receipt, Mapping):
                if receipt.get("shardId") != shard_id:
                    errors.append(f"{label}: receipt shard does not match")
                if receipt.get("shardFingerprint") != shard.get("shardFingerprint"):
                    errors.append(f"{label}: receipt shard fingerprint is stale")
                if receipt.get("overallDiffFingerprint") != manifest.get("overallDiffFingerprint"):
                    errors.append(f"{label}: current receipt is not bound to the current diff")
                if named_required:
                    errors.extend(
                        f"{label}: {error}"
                        for error in _validate_named_review_closure_bundle(
                            evidence.get("namedReviewBundle"),
                            manifest=manifest,
                            shard=shard,
                            current_receipt_wrapper=receipt,
                            now=now,
                        )
                    )
        elif evidence_type == "REUSE_DECISION":
            if set(evidence) != {"evidenceType", "decision", "priorReceipt"}:
                errors.append(f"{label}: invalid REUSE_DECISION evidence shape")
                continue
            decision = evidence.get("decision")
            prior = evidence.get("priorReceipt")
            result = (
                validate_prior_review_receipt(prior, manifest)
                if validate_prior_review_receipt is not None and isinstance(prior, Mapping)
                else {"valid": False, "errors": ["prior receipt is unavailable"]}
            )
            errors.extend(f"{label}: {error}" for error in result.get("errors", []))
            decision_result = (
                validate_reuse_decision(decision)
                if validate_reuse_decision is not None
                else {"valid": False, "errors": ["reuse decision validator is unavailable"]}
            )
            errors.extend(
                f"{label}: {error}" for error in decision_result.get("errors", [])
            )
            if isinstance(decision, Mapping):
                if decision.get("taskId") != manifest.get("taskId"):
                    errors.append(f"{label}: reuse task does not match")
                if decision.get("shardId") != shard_id:
                    errors.append(f"{label}: reuse shard does not match")
                if decision.get("shardFingerprint") != shard.get("shardFingerprint"):
                    errors.append(f"{label}: reuse shard fingerprint is stale")
                if decision.get("currentOverallDiffFingerprint") != manifest.get("overallDiffFingerprint"):
                    errors.append(f"{label}: reuse decision is not bound to current diff")
                if not isinstance(decision.get("reasons"), list) or not decision.get("reasons"):
                    errors.append(f"{label}: reuse reasons are required")
                plan_id = decision.get("planId")
                if not isinstance(trusted_invalidation_plans, Mapping):
                    errors.append(f"{label}: trusted invalidation plan lookup is required")
                    trusted_plan = None
                else:
                    trusted_plan = trusted_invalidation_plans.get(plan_id)
                    if not isinstance(trusted_plan, Mapping):
                        errors.append(f"{label}: trusted invalidation plan is missing")
                if isinstance(trusted_plan, Mapping):
                    plan_result = (
                        validate_invalidation_plan(trusted_plan, manifest, now=now)
                        if validate_invalidation_plan is not None
                        else {"valid": False, "errors": ["invalidation plan validator is unavailable"]}
                    )
                    errors.extend(
                        f"{label}: {error}"
                        for error in plan_result.get("errors", [])
                    )
                    trusted_decisions = trusted_plan.get("reuseDecisions", [])
                    if not isinstance(trusted_decisions, list) or dict(decision) not in trusted_decisions:
                        errors.append(f"{label}: decision is absent from trusted invalidation plan")
                if isinstance(prior, Mapping):
                    if decision.get("priorReceiptDigest") != prior.get("wrapperDigest"):
                        errors.append(f"{label}: reuse digest does not match immutable prior wrapper")
                    if decision.get("priorReceiptId") != prior.get("receiptId"):
                        errors.append(f"{label}: reuse receipt identity does not match")
                    if prior.get("shardId") != shard_id or prior.get("shardFingerprint") != shard.get("shardFingerprint"):
                        errors.append(f"{label}: prior receipt does not cover current shard scope")
        elif evidence_type == "ACCEPTED_RISK":
            if set(evidence) != {"evidenceType", "findingId"}:
                errors.append(f"{label}: invalid ACCEPTED_RISK evidence shape")
                continue
            finding = findings_by_id.get(evidence.get("findingId"))
            if not isinstance(finding, Mapping):
                errors.append(f"{label}: accepted-risk finding is missing")
            elif finding.get("state") != "ACCEPTED_RISK" or finding.get("shardId") != shard_id:
                errors.append(f"{label}: finding is not accepted risk for this shard")
            else:
                risk = finding.get("riskAcceptance")
                risk_scope = risk.get("scope", []) if isinstance(risk, Mapping) else []
                shard_paths = shard.get("paths", [])
                repair_scope = finding.get("repairScope", [])
                if (
                    not risk_scope
                    or risk_scope != repair_scope
                    or any(scope not in shard_paths for scope in risk_scope)
                ):
                    errors.append(
                        f"{label}: accepted-risk, repair, and shard scopes must match exactly"
                    )
        else:
            errors.append(f"{label}: unsupported evidence type")

    for item in ledger.get("findings", []) if isinstance(ledger.get("findings"), list) else []:
        if not isinstance(item, Mapping):
            continue
        finding_id = item.get("findingId")
        finding_shard = manifest_shards.get(item.get("shardId"))
        if finding_shard is None:
            errors.append(f"closure.finding.{finding_id}: finding references an unknown shard")
        elif finding_shard.get("lens") != item.get("ownerLens"):
            errors.append(f"closure.finding.{finding_id}: owner lens does not match its shard")
        if item.get("state") not in TERMINAL_FINDING_STATES:
            errors.append(f"closure.finding.{finding_id}: finding is not resolved")
    return {
        "valid": not errors,
        "closureEligible": not errors,
        "requiredShardIds": required_ids,
        "errors": errors,
    }
