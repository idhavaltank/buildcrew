#!/usr/bin/env python3
"""Pure, report-only review invalidation and reuse planner."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .named_reviewer import artifact_digest
from .review_contracts import REVIEW_LENSES, validate_finding_ledger, validate_review_shard_manifest


FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CHANGE_FIELDS = {
    "paths",
    "symbols",
    "criteriaIds",
    "boundaries",
    "findingIds",
    "acceptanceChanged",
    "criterionMap",
}
RECEIPT_FIELDS = {
    "receiptId",
    "taskId",
    "shardId",
    "shardFingerprint",
    "overallDiffFingerprint",
    "receiptDigest",
    "terminal",
    "reviewReceipt",
    "wrapperDigest",
}
REUSE_DECISION_FIELDS = {
    "schemaVersion",
    "decisionId",
    "planId",
    "taskId",
    "shardId",
    "shardFingerprint",
    "currentOverallDiffFingerprint",
    "manifestDigest",
    "changeSetDigest",
    "priorReceiptDigest",
    "priorReceiptId",
    "decision",
    "reasons",
}
INVALIDATION_PLAN_FIELDS = {
    "schemaVersion",
    "planId",
    "planDigest",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "sourceFingerprint",
    "currentOverallDiffFingerprint",
    "manifestDigest",
    "normalizedChangeSet",
    "changeSetDigest",
    "producerIdentity",
    "producerAuthority",
    "issuedAt",
    "expiresAt",
    "decisionsDigest",
    "rerunShards",
    "reusedShards",
    "decisions",
    "reuseDecisions",
    "deltaPackets",
    "reportOnly",
    "terminal",
}


class ReviewInvalidationError(ValueError):
    """Raised when invalidation inputs are incomplete or unsafe."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(f"{domain}\0{_canonical_json(value)}".encode("utf-8")).hexdigest()


def compute_review_manifest_digest(manifest: Mapping[str, Any]) -> str:
    return _digest("review-shard-manifest-v1", manifest)


def compute_change_set_digest(change_set: Mapping[str, Any]) -> str:
    return _digest("review-normalized-change-set-v1", change_set)


def compute_invalidation_plan_digest(plan: Mapping[str, Any]) -> str:
    stable = {key: plan.get(key) for key in sorted(INVALIDATION_PLAN_FIELDS - {"planDigest"})}
    return _digest("review-invalidation-plan-v1", stable)


def _parse_timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value:
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


def compute_prior_receipt_wrapper_digest(value: Mapping[str, Any]) -> str:
    """Bind immutable shard metadata to the digest of the embedded receipt."""

    if not isinstance(value, Mapping):
        raise ReviewInvalidationError("prior receipt wrapper: expected object")
    stable = {
        "receiptId": value.get("receiptId"),
        "taskId": value.get("taskId"),
        "shardId": value.get("shardId"),
        "shardFingerprint": value.get("shardFingerprint"),
        "overallDiffFingerprint": value.get("overallDiffFingerprint"),
        "receiptDigest": value.get("receiptDigest"),
        "terminal": value.get("terminal"),
    }
    return _digest("review-prior-receipt-wrapper-v1", stable)


def _safe_path(path: Any) -> str:
    if not isinstance(path, str) or not path or "\\" in path or any(ord(character) < 32 for character in path):
        raise ReviewInvalidationError(f"unsafe changed path: {path!r}")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        raise ReviewInvalidationError(f"unsafe changed path: {path!r}")
    return str(parsed)


def _unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ReviewInvalidationError(f"{label}: expected string array")
    if len(value) != len(set(value)):
        raise ReviewInvalidationError(f"{label}: duplicate values")
    return sorted(value)


def normalize_change_set(value: Any) -> dict[str, Any]:
    """Normalize a bounded caller-supplied change set without mutating it."""

    if not isinstance(value, Mapping) or set(value) != CHANGE_FIELDS:
        raise ReviewInvalidationError("changeSet: unknown or missing fields")
    paths = [_safe_path(path) for path in _unique_strings(value.get("paths"), "changeSet.paths")]
    folded: set[str] = set()
    for path in paths:
        key = path.casefold()
        if key in folded:
            raise ReviewInvalidationError("changeSet.paths: case-colliding normalized paths")
        folded.add(key)
    criterion_map = value.get("criterionMap")
    if not isinstance(criterion_map, Mapping):
        raise ReviewInvalidationError("changeSet.criterionMap: expected object")
    normalized_map: dict[str, list[str]] = {}
    for criterion, shard_ids in sorted(criterion_map.items()):
        if not isinstance(criterion, str) or not criterion:
            raise ReviewInvalidationError("changeSet.criterionMap: criterion IDs must be strings")
        normalized_map[criterion] = _unique_strings(shard_ids, f"changeSet.criterionMap.{criterion}")
    if not isinstance(value.get("acceptanceChanged"), bool):
        raise ReviewInvalidationError("changeSet.acceptanceChanged: expected boolean")
    return {
        "paths": sorted(paths),
        "symbols": _unique_strings(value.get("symbols"), "changeSet.symbols"),
        "criteriaIds": _unique_strings(value.get("criteriaIds"), "changeSet.criteriaIds"),
        "boundaries": _unique_strings(value.get("boundaries"), "changeSet.boundaries"),
        "findingIds": _unique_strings(value.get("findingIds"), "changeSet.findingIds"),
        "acceptanceChanged": value["acceptanceChanged"],
        "criterionMap": normalized_map,
    }


def _plan_identity_seed(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "taskId": plan.get("taskId"),
        "projectId": plan.get("projectId"),
        "repositoryId": plan.get("repositoryId"),
        "tenantId": plan.get("tenantId"),
        "sourceFingerprint": plan.get("sourceFingerprint"),
        "currentOverallDiffFingerprint": plan.get("currentOverallDiffFingerprint"),
        "manifestDigest": plan.get("manifestDigest"),
        "changeSetDigest": plan.get("changeSetDigest"),
        "producerIdentity": plan.get("producerIdentity"),
        "producerAuthority": plan.get("producerAuthority"),
        "issuedAt": plan.get("issuedAt"),
        "expiresAt": plan.get("expiresAt"),
        "decisionsDigest": plan.get("decisionsDigest"),
    }


def _reuse_identity_seed(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "planId": decision.get("planId"),
        "taskId": decision.get("taskId"),
        "shardId": decision.get("shardId"),
        "shardFingerprint": decision.get("shardFingerprint"),
        "currentOverallDiffFingerprint": decision.get("currentOverallDiffFingerprint"),
        "manifestDigest": decision.get("manifestDigest"),
        "changeSetDigest": decision.get("changeSetDigest"),
        "priorReceiptDigest": decision.get("priorReceiptDigest"),
    }


def validate_reuse_decision(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping) or set(value) != REUSE_DECISION_FIELDS:
        return {"valid": False, "errors": ["reuseDecision: unknown or missing fields"]}
    if value.get("schemaVersion") != "review-coverage-reuse-decision-v1":
        errors.append("reuseDecision.schemaVersion: unsupported value")
    for key in ("decisionId", "planId", "taskId", "shardId", "priorReceiptId"):
        if not isinstance(value.get(key), str) or not value.get(key):
            errors.append(f"reuseDecision.{key}: expected non-empty string")
    for key in (
        "shardFingerprint",
        "currentOverallDiffFingerprint",
        "manifestDigest",
        "changeSetDigest",
        "priorReceiptDigest",
    ):
        if not isinstance(value.get(key), str) or not FINGERPRINT_RE.fullmatch(value[key]):
            errors.append(f"reuseDecision.{key}: expected sha256 fingerprint")
    if value.get("decision") != "REUSE":
        errors.append("reuseDecision.decision: expected REUSE")
    reasons = value.get("reasons")
    if (
        not isinstance(reasons, list)
        or not reasons
        or reasons != sorted(set(reasons))
        or any(not isinstance(item, str) or not item for item in reasons)
    ):
        errors.append("reuseDecision.reasons: expected sorted unique non-empty strings")
    expected_id = "reuse-" + _digest(
        "review-coverage-reuse-decision-v1", _reuse_identity_seed(value)
    ).split(":", 1)[1][:40]
    if value.get("decisionId") != expected_id:
        errors.append("reuseDecision.decisionId: does not match bound plan content")
    return {"valid": not errors, "errors": errors}


def validate_invalidation_plan(
    value: Any,
    manifest: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate a protected-store invalidation plan and its exact change set."""

    errors: list[str] = []
    if not isinstance(value, Mapping) or set(value) != INVALIDATION_PLAN_FIELDS:
        return {"valid": False, "errors": ["invalidationPlan: unknown or missing fields"]}
    manifest_result = validate_review_shard_manifest(manifest)
    errors.extend(f"manifest: {error}" for error in manifest_result.get("errors", []))
    if value.get("schemaVersion") != "review-invalidation-plan-v1":
        errors.append("invalidationPlan.schemaVersion: unsupported value")
    for key in (
        "planId",
        "taskId",
        "projectId",
        "repositoryId",
        "sourceFingerprint",
        "producerIdentity",
    ):
        if not isinstance(value.get(key), str) or not value.get(key):
            errors.append(f"invalidationPlan.{key}: expected non-empty string")
    if value.get("tenantId") is not None and (
        not isinstance(value.get("tenantId"), str) or not value.get("tenantId")
    ):
        errors.append("invalidationPlan.tenantId: expected non-empty string or null")
    for key in (
        "planDigest",
        "currentOverallDiffFingerprint",
        "manifestDigest",
        "changeSetDigest",
        "decisionsDigest",
    ):
        if not isinstance(value.get(key), str) or not FINGERPRINT_RE.fullmatch(value[key]):
            errors.append(f"invalidationPlan.{key}: expected sha256 fingerprint")
    if value.get("producerAuthority") not in {"review-host", "controller"}:
        errors.append("invalidationPlan.producerAuthority: unsupported authority")
    issued = _parse_timestamp(value.get("issuedAt"), "invalidationPlan.issuedAt", errors)
    expires = _parse_timestamp(value.get("expiresAt"), "invalidationPlan.expiresAt", errors)
    current = _parse_timestamp(now, "now", errors) if now is not None else None
    if issued is not None and expires is not None and expires <= issued:
        errors.append("invalidationPlan: expiresAt must be after issuedAt")
    if current is not None and issued is not None and current < issued:
        errors.append("invalidationPlan: issuedAt is in the future")
    if current is not None and expires is not None and current >= expires:
        errors.append("invalidationPlan: plan is expired")
    changes = value.get("normalizedChangeSet")
    try:
        normalized = normalize_change_set(changes)
    except ReviewInvalidationError as exc:
        errors.append(f"invalidationPlan.normalizedChangeSet: {exc}")
        normalized = None
    if normalized is not None and changes != normalized:
        errors.append("invalidationPlan.normalizedChangeSet: expected canonical form")
    if normalized is not None and value.get("changeSetDigest") != compute_change_set_digest(normalized):
        errors.append("invalidationPlan.changeSetDigest: does not match exact change set")
    if value.get("manifestDigest") != compute_review_manifest_digest(manifest):
        errors.append("invalidationPlan.manifestDigest: does not match current manifest")
    for key in ("taskId", "projectId", "repositoryId", "tenantId", "sourceFingerprint"):
        if value.get(key) != manifest.get(key):
            errors.append(f"invalidationPlan.{key}: does not match current manifest")
    if value.get("currentOverallDiffFingerprint") != manifest.get("overallDiffFingerprint"):
        errors.append("invalidationPlan.currentOverallDiffFingerprint: does not match current manifest")
    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        errors.append("invalidationPlan.decisions: expected array")
        decisions = []
    else:
        for index, decision in enumerate(decisions):
            if not isinstance(decision, Mapping) or set(decision) != {"shardId", "decision", "reasons"}:
                errors.append(f"invalidationPlan.decisions[{index}]: invalid shape")
    if value.get("decisionsDigest") != _digest("review-invalidation-decisions-v1", decisions):
        errors.append("invalidationPlan.decisionsDigest: does not match decisions")
    for key in ("rerunShards", "reusedShards"):
        items = value.get(key)
        if (
            not isinstance(items, list)
            or items != sorted(set(items))
            or any(not isinstance(item, str) or not item for item in items)
        ):
            errors.append(f"invalidationPlan.{key}: expected sorted unique strings")
    if set(value.get("rerunShards", [])) & set(value.get("reusedShards", [])):
        errors.append("invalidationPlan: rerun and reused shard sets overlap")
    reuse_decisions = value.get("reuseDecisions")
    if not isinstance(reuse_decisions, list):
        errors.append("invalidationPlan.reuseDecisions: expected array")
        reuse_decisions = []
    else:
        for index, decision in enumerate(reuse_decisions):
            result = validate_reuse_decision(decision)
            errors.extend(
                f"invalidationPlan.reuseDecisions[{index}]: {error}"
                for error in result.get("errors", [])
            )
            if isinstance(decision, Mapping):
                if decision.get("planId") != value.get("planId"):
                    errors.append(f"invalidationPlan.reuseDecisions[{index}]: wrong planId")
                if decision.get("manifestDigest") != value.get("manifestDigest"):
                    errors.append(f"invalidationPlan.reuseDecisions[{index}]: wrong manifestDigest")
                if decision.get("changeSetDigest") != value.get("changeSetDigest"):
                    errors.append(f"invalidationPlan.reuseDecisions[{index}]: wrong changeSetDigest")
    if sorted(
        item.get("shardId")
        for item in reuse_decisions
        if isinstance(item, Mapping) and isinstance(item.get("shardId"), str)
    ) != value.get("reusedShards"):
        errors.append("invalidationPlan.reuseDecisions: does not match reusedShards")
    if not isinstance(value.get("deltaPackets"), list):
        errors.append("invalidationPlan.deltaPackets: expected array")
    if value.get("reportOnly") is not True or value.get("terminal") is not True:
        errors.append("invalidationPlan: reportOnly and terminal must be exactly true")
    expected_plan_id = "invalidation-" + _digest(
        "review-invalidation-plan-v1-id", _plan_identity_seed(value)
    ).split(":", 1)[1][:40]
    if value.get("planId") != expected_plan_id:
        errors.append("invalidationPlan.planId: does not match bound evidence")
    if value.get("planDigest") != compute_invalidation_plan_digest(value):
        errors.append("invalidationPlan.planDigest: does not match content")
    return {"valid": not errors, "errors": errors}


def _paths_overlap(changed: str, owned: str) -> bool:
    return changed == owned or changed.startswith(owned.rstrip("/") + "/") or owned.startswith(changed.rstrip("/") + "/")


def _validate_receipts(receipts: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes, bytearray)):
        raise ReviewInvalidationError("priorReceipts: expected array")
    by_shard: dict[str, Mapping[str, Any]] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, Mapping) or set(receipt) != RECEIPT_FIELDS:
            raise ReviewInvalidationError(f"priorReceipts[{index}]: unknown or missing fields")
        for key in ("receiptId", "taskId", "shardId", "shardFingerprint", "overallDiffFingerprint", "receiptDigest"):
            if not isinstance(receipt.get(key), str) or not receipt.get(key):
                raise ReviewInvalidationError(f"priorReceipts[{index}].{key}: expected non-empty string")
        for key in ("shardFingerprint", "overallDiffFingerprint", "receiptDigest", "wrapperDigest"):
            if not FINGERPRINT_RE.fullmatch(receipt[key]):
                raise ReviewInvalidationError(f"priorReceipts[{index}].{key}: expected sha256 fingerprint")
        if receipt.get("terminal") is not True:
            raise ReviewInvalidationError(f"priorReceipts[{index}].terminal: must be exactly true")
        if receipt.get("taskId") != manifest.get("taskId"):
            raise ReviewInvalidationError(f"priorReceipts[{index}].taskId: wrong task")
        review_receipt = receipt.get("reviewReceipt")
        try:
            from scripts.validate_hybrid_contracts import validate_receipt
        except ImportError as exc:  # pragma: no cover - package import boundary
            raise ReviewInvalidationError(f"full receipt validator unavailable ({type(exc).__name__})") from exc
        full = validate_receipt(
            review_receipt,
            expected_task_id=manifest.get("taskId"),
            expected_fingerprint=manifest.get("sourceFingerprint"),
        )
        if not full.get("valid"):
            raise ReviewInvalidationError(
                f"priorReceipts[{index}].reviewReceipt: full validation failed: "
                + "; ".join(full.get("errors", []))
            )
        if full.get("completionEligible") is not True:
            raise ReviewInvalidationError(
                f"priorReceipts[{index}].reviewReceipt: receipt is not completion eligible"
            )
        if not isinstance(review_receipt, Mapping) or review_receipt.get("receiptId") != receipt.get("receiptId"):
            raise ReviewInvalidationError(f"priorReceipts[{index}].receiptId: does not match review receipt")
        scope = review_receipt.get("scope") if isinstance(review_receipt, Mapping) else None
        if not isinstance(scope, Mapping) or scope.get("lane") != receipt.get("shardId"):
            raise ReviewInvalidationError(
                f"priorReceipts[{index}].reviewReceipt.scope.lane: does not match shard"
            )
        if scope.get("repository") != manifest.get("repositoryId"):
            raise ReviewInvalidationError(
                f"priorReceipts[{index}].reviewReceipt.scope.repository: does not match manifest"
            )
        if receipt.get("receiptDigest") != artifact_digest("review-receipt-v2", review_receipt):
            raise ReviewInvalidationError(f"priorReceipts[{index}].receiptDigest: does not match review receipt")
        if receipt.get("wrapperDigest") != compute_prior_receipt_wrapper_digest(receipt):
            raise ReviewInvalidationError(
                f"priorReceipts[{index}].wrapperDigest: does not match immutable wrapper metadata"
            )
        shard_id = receipt["shardId"]
        if shard_id in by_shard:
            raise ReviewInvalidationError(f"priorReceipts: duplicate receipt for shard {shard_id}")
        by_shard[shard_id] = receipt
    return by_shard


def validate_prior_review_receipt(
    receipt: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one completion-eligible immutable prior-receipt wrapper."""

    try:
        validated = _validate_receipts([receipt], manifest)
    except ReviewInvalidationError as exc:
        return {"valid": False, "errors": [str(exc)]}
    return {"valid": True, "errors": [], "receipt": next(iter(validated.values()))}


def _affected_reasons(shard: Mapping[str, Any], changes: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if changes["acceptanceChanged"]:
        if (
            not changes["criterionMap"]
            or not changes["criteriaIds"]
            or any(criterion not in changes["criterionMap"] for criterion in changes["criteriaIds"])
        ):
            reasons.append("acceptance-boundary-changed-without-narrow-map")
        else:
            mapped = {
                shard_id
                for criterion in changes["criteriaIds"]
                for shard_id in changes["criterionMap"].get(criterion, [])
            }
            if shard.get("shardId") in mapped:
                reasons.append("mapped-acceptance-criterion-changed")
    if any(
        _paths_overlap(changed, owned)
        for changed in changes["paths"]
        for owned in shard.get("paths", [])
    ):
        reasons.append("owned-path-changed")
    if set(changes["symbols"]) & set(shard.get("symbols", [])):
        reasons.append("owned-symbol-changed")
    if set(changes["criteriaIds"]) & set(shard.get("criteriaIds", [])):
        reasons.append("acceptance-criterion-changed")
    if set(changes["boundaries"]) & set(shard.get("dependencies", [])):
        reasons.append("declared-boundary-dependency-changed")
    return sorted(set(reasons))


def build_review_delta_packet(
    manifest: Mapping[str, Any],
    shard: Mapping[str, Any],
    changes: Mapping[str, Any],
    reasons: Sequence[str],
    prior_receipt: Mapping[str, Any] | None,
    current_overall_diff_fingerprint: str,
) -> dict[str, Any]:
    relevant_paths = sorted(
        changed
        for changed in changes["paths"]
        if any(_paths_overlap(changed, owned) for owned in shard.get("paths", []))
    )
    relevant_symbols = sorted(set(changes["symbols"]) & set(shard.get("symbols", [])))
    relevant_criteria = sorted(set(changes["criteriaIds"]) & set(shard.get("criteriaIds", [])))
    seed = {
        "taskId": manifest["taskId"],
        "shardId": shard["shardId"],
        "currentOverallDiffFingerprint": current_overall_diff_fingerprint,
        "reasons": sorted(reasons),
    }
    return {
        "schemaVersion": "review-delta-packet-v1",
        "packetId": "delta-" + _digest("review-delta-packet-v1", seed).split(":", 1)[1][:40],
        "taskId": manifest["taskId"],
        "shardId": shard["shardId"],
        "lens": shard["lens"],
        "shardFingerprint": shard["shardFingerprint"],
        "currentOverallDiffFingerprint": current_overall_diff_fingerprint,
        "changedPaths": relevant_paths,
        "changedSymbols": relevant_symbols,
        "changedCriteriaIds": relevant_criteria,
        "changedBoundaries": sorted(set(changes["boundaries"]) & set(shard.get("dependencies", []))),
        "findingIds": list(changes["findingIds"]),
        "priorReceiptId": prior_receipt.get("receiptId") if prior_receipt else None,
        "evidenceRefs": list(shard.get("evidenceRefs", [])),
        "reasons": sorted(reasons),
        "stopConditions": ["scope-expansion", "source-fingerprint-change", "shard-fingerprint-change"],
    }


def plan_review_invalidation(
    manifest: Mapping[str, Any],
    prior_receipts: Sequence[Mapping[str, Any]],
    change_set: Mapping[str, Any],
    *,
    current_overall_diff_fingerprint: str,
    producer_identity: str,
    producer_authority: str,
    issued_at: str,
    expires_at: str,
    finding_ledger: Mapping[str, Any] | None = None,
    trusted_risk_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    trusted_resolution_receipts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return deterministic affected-only rerun and immutable reuse decisions."""

    manifest_result = validate_review_shard_manifest(manifest)
    if not manifest_result["valid"]:
        raise ReviewInvalidationError("manifest is invalid: " + "; ".join(manifest_result["errors"]))
    if not isinstance(current_overall_diff_fingerprint, str) or not FINGERPRINT_RE.fullmatch(current_overall_diff_fingerprint):
        raise ReviewInvalidationError("currentOverallDiffFingerprint: expected sha256 fingerprint")
    if current_overall_diff_fingerprint != manifest.get("overallDiffFingerprint"):
        raise ReviewInvalidationError(
            "currentOverallDiffFingerprint: must equal the current review manifest"
        )
    if not isinstance(producer_identity, str) or not producer_identity:
        raise ReviewInvalidationError("producerIdentity: expected non-empty string")
    if producer_authority not in {"review-host", "controller"}:
        raise ReviewInvalidationError("producerAuthority: unsupported authority")
    timestamp_errors: list[str] = []
    issued = _parse_timestamp(issued_at, "issuedAt", timestamp_errors)
    expires = _parse_timestamp(expires_at, "expiresAt", timestamp_errors)
    if issued is not None and expires is not None and expires <= issued:
        timestamp_errors.append("expiresAt must be after issuedAt")
    if timestamp_errors:
        raise ReviewInvalidationError("; ".join(timestamp_errors))
    changes = normalize_change_set(change_set)
    manifest_digest = compute_review_manifest_digest(manifest)
    change_set_digest = compute_change_set_digest(changes)
    receipts = _validate_receipts(prior_receipts, manifest)
    known_shards = {shard["shardId"] for shard in manifest["shards"]}
    if set(receipts) - known_shards:
        raise ReviewInvalidationError("priorReceipts: receipt references unknown shard")

    shards_by_id = {shard["shardId"]: shard for shard in manifest["shards"]}
    reason_map: dict[str, list[str]] = {
        shard_id: _affected_reasons(shard, changes)
        for shard_id, shard in shards_by_id.items()
        if shard.get("required") is True
    }
    if changes["acceptanceChanged"]:
        required_shards = set(reason_map)
        changed_criteria = set(changes["criteriaIds"])
        criterion_map = changes["criterionMap"]
        mapping_is_complete = (
            bool(changed_criteria)
            and set(criterion_map) == changed_criteria
            and all(criterion_map[criterion] for criterion in changed_criteria)
            and all(
                shard_id in required_shards
                for criterion in changed_criteria
                for shard_id in criterion_map[criterion]
            )
        )
        if not mapping_is_complete:
            for reasons in reason_map.values():
                reasons.append("acceptance-boundary-changed-without-complete-narrow-map")
    for shard_id, shard in shards_by_id.items():
        if shard.get("required") is not True:
            continue
        receipt = receipts.get(shard_id)
        if receipt is None:
            reason_map[shard_id].append("missing-prior-terminal-receipt")
        elif receipt.get("shardFingerprint") != shard.get("shardFingerprint"):
            reason_map[shard_id].append("shard-fingerprint-changed")

    if changes["findingIds"]:
        if finding_ledger is None:
            for reasons in reason_map.values():
                reasons.append("changed-finding-without-ledger")
        else:
            ledger_result = validate_finding_ledger(
                finding_ledger,
                trusted_risk_authorizations=trusted_risk_authorizations,
                trusted_resolution_receipts=trusted_resolution_receipts,
            )
            if not ledger_result["valid"]:
                raise ReviewInvalidationError("finding ledger is invalid: " + "; ".join(ledger_result["errors"]))
            for key in (
                "taskId",
                "projectId",
                "repositoryId",
                "tenantId",
                "sourceFingerprint",
                "overallDiffFingerprint",
            ):
                if finding_ledger.get(key) != manifest.get(key):
                    raise ReviewInvalidationError(
                        f"finding ledger {key} does not match review manifest"
                    )
            findings = {
                item["findingId"]: item
                for item in finding_ledger.get("findings", [])
                if isinstance(item, Mapping) and isinstance(item.get("findingId"), str)
            }
            unknown_findings = set(changes["findingIds"]) - set(findings)
            if unknown_findings:
                raise ReviewInvalidationError("change set references unknown findings")
            affected_lenses: set[str] = set()
            for finding_id in changes["findingIds"]:
                item = findings[finding_id]
                affected_lenses.add(item["ownerLens"])
                affected_lenses.update(item.get("dependentLenses", []))
            for shard_id, shard in shards_by_id.items():
                if shard.get("required") is True and shard.get("lens") in affected_lenses:
                    reason_map[shard_id].append("owned-or-dependent-finding-changed")

    known_boundaries = set(shards_by_id) | REVIEW_LENSES
    unknown_boundaries = set(changes["boundaries"]) - known_boundaries
    if unknown_boundaries:
        for reasons in reason_map.values():
            reasons.append("unmapped-boundary-change")
    for boundary in set(changes["boundaries"]) & known_boundaries:
        for shard_id, shard in shards_by_id.items():
            if shard.get("required") is not True:
                continue
            if boundary == shard_id or boundary == shard.get("lens"):
                reason_map[shard_id].append("declared-boundary-changed")

    affected_ids = {shard_id for shard_id, reasons in reason_map.items() if reasons}
    changed = True
    while changed:
        changed = False
        for shard_id, shard in shards_by_id.items():
            if shard.get("required") is not True or shard_id in affected_ids:
                continue
            if set(shard.get("dependencies", [])) & affected_ids:
                reason_map[shard_id].append("dependent-shard-invalidated")
                affected_ids.add(shard_id)
                changed = True

    rerun: list[str] = []
    reused: list[str] = []
    reuse_contexts: list[dict[str, Any]] = []
    delta_packets: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for shard in sorted(manifest["shards"], key=lambda item: item["shardId"]):
        shard_id = shard["shardId"]
        if shard.get("required") is not True:
            decisions.append({"shardId": shard_id, "decision": "NOT_REQUIRED", "reasons": []})
            continue
        receipt = receipts.get(shard_id)
        reasons = reason_map.get(shard_id, [])
        if reasons:
            reasons = sorted(set(reasons))
            rerun.append(shard_id)
            decisions.append({"shardId": shard_id, "decision": "REVIEW_REQUIRED", "reasons": reasons})
            delta_packets.append(
                build_review_delta_packet(
                    manifest,
                    shard,
                    changes,
                    reasons,
                    receipt,
                    current_overall_diff_fingerprint,
                )
            )
            continue
        reused.append(shard_id)
        reuse_contexts.append({
            "taskId": manifest["taskId"],
            "shardId": shard_id,
            "shardFingerprint": shard["shardFingerprint"],
            "currentOverallDiffFingerprint": current_overall_diff_fingerprint,
            "manifestDigest": manifest_digest,
            "changeSetDigest": change_set_digest,
            "priorReceiptDigest": receipt["wrapperDigest"],
            "priorReceiptId": receipt["receiptId"],
            "reasons": ["same-task", "terminal-valid-receipt", "unchanged-shard-fingerprint", "unaffected-scope"],
        })
        decisions.append({
            "shardId": shard_id,
            "decision": "REUSE",
            "reasons": list(reuse_contexts[-1]["reasons"]),
        })
    decisions_digest = _digest("review-invalidation-decisions-v1", decisions)
    plan_seed = {
        "taskId": manifest["taskId"],
        "projectId": manifest["projectId"],
        "repositoryId": manifest["repositoryId"],
        "tenantId": manifest.get("tenantId"),
        "sourceFingerprint": manifest["sourceFingerprint"],
        "currentOverallDiffFingerprint": current_overall_diff_fingerprint,
        "manifestDigest": manifest_digest,
        "changeSetDigest": change_set_digest,
        "producerIdentity": producer_identity,
        "producerAuthority": producer_authority,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "decisionsDigest": decisions_digest,
    }
    plan_id = "invalidation-" + _digest(
        "review-invalidation-plan-v1-id", plan_seed
    ).split(":", 1)[1][:40]
    reuse_decisions: list[dict[str, Any]] = []
    for context in reuse_contexts:
        decision_seed = {"planId": plan_id, **{key: context[key] for key in (
            "taskId", "shardId", "shardFingerprint", "currentOverallDiffFingerprint",
            "manifestDigest", "changeSetDigest", "priorReceiptDigest",
        )}}
        reuse_decisions.append(
            {
                "schemaVersion": "review-coverage-reuse-decision-v1",
                "decisionId": "reuse-" + _digest(
                    "review-coverage-reuse-decision-v1", decision_seed
                ).split(":", 1)[1][:40],
                **decision_seed,
                "priorReceiptId": context["priorReceiptId"],
                "decision": "REUSE",
                "reasons": sorted(context["reasons"]),
            }
        )
    plan = {
        "schemaVersion": "review-invalidation-plan-v1",
        "planId": plan_id,
        "planDigest": "sha256:" + "0" * 64,
        "taskId": manifest["taskId"],
        "projectId": manifest["projectId"],
        "repositoryId": manifest["repositoryId"],
        "tenantId": manifest.get("tenantId"),
        "sourceFingerprint": manifest["sourceFingerprint"],
        "currentOverallDiffFingerprint": current_overall_diff_fingerprint,
        "manifestDigest": manifest_digest,
        "normalizedChangeSet": changes,
        "changeSetDigest": change_set_digest,
        "producerIdentity": producer_identity,
        "producerAuthority": producer_authority,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "decisionsDigest": decisions_digest,
        "rerunShards": sorted(rerun),
        "reusedShards": sorted(reused),
        "decisions": decisions,
        "reuseDecisions": reuse_decisions,
        "deltaPackets": delta_packets,
        "reportOnly": True,
        "terminal": True,
    }
    plan["planDigest"] = compute_invalidation_plan_digest(plan)
    return plan
