#!/usr/bin/env python3
"""Dependency-free validators and receipt helpers for context sidecars."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .project_context import (
    DISCOVERY_VERSION,
    MANIFEST_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from .policy_resolver import POLICY_SCHEMA_VERSION

CONTEXT_STATUSES = {"UNSEEN", "DISCOVERING", "CURRENT", "STALE", "INCOMPLETE", "CONFLICT", "UNAVAILABLE", "CORRUPT"}
PLATFORMS = {"web", "backend", "mobile", "flutter", "desktop", "extension", "cross_platform", "unknown"}
SOURCE_KINDS = {"instruction", "architecture", "product", "plan", "reference", "quality", "manifest", "test", "ci", "workflow", "generated", "runtime_candidate", "contract", "unknown"}
ENFORCEMENT = {"BLOCK", "REQUIRED", "ADVISORY", "DISCOVERED"}
POLICY_SCOPES = {"COMMON", "SHARED_APPLICATION", "WEB", "MOBILE", "BACKEND", "FLUTTER", "PROJECT", "TASK"}
POLICY_SOURCES = {"v3_builtin", "shared_profile", "platform_profile", "repository_instruction", "project_manifest", "task_requirement", "retrieved_lesson"}
PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _keys(value: Mapping[str, Any], required: set[str], allowed: set[str], label: str, errors: list[str]) -> None:
    for key in sorted(required - set(value)):
        errors.append(f"{label}: missing required field {key}")
    for key in sorted(set(value) - allowed):
        errors.append(f"{label}: unknown field {key}")


def _dict(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    return value


def _string(value: Any, label: str, errors: list[str], *, allow_null: bool = False) -> None:
    if allow_null and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _list(value: Any, label: str, errors: list[str]) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return None
    return value


def _string_list(value: Any, label: str, errors: list[str]) -> None:
    items = _list(value, label, errors)
    if items is not None:
        for index, item in enumerate(items):
            _string(item, f"{label}[{index}]", errors)


def _relative_path(value: Any, label: str, errors: list[str]) -> None:
    """Validate a generated repository-relative path without filesystem access."""

    _string(value, label, errors)
    if not isinstance(value, str) or not value.strip():
        return
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or (len(value) > 1 and value[1] == ":")
    ):
        errors.append(f"{label}: expected repository-relative path")


def _validate_repository(value: Any, label: str, errors: list[str]) -> None:
    repository = _dict(value, label, errors)
    if repository is None:
        return
    allowed = {"projectId", "repositoryId", "repositoryPathHint", "worktreeIdentity", "sourceRef", "identityConfidence"}
    _keys(repository, {"projectId", "repositoryId", "repositoryPathHint", "identityConfidence"}, allowed, label, errors)
    for key in ("projectId", "repositoryId", "repositoryPathHint"):
        _string(repository.get(key), f"{label}.{key}", errors)
    for key in ("worktreeIdentity", "sourceRef"):
        if key in repository:
            _string(repository.get(key), f"{label}.{key}", errors, allow_null=True)
    if repository.get("identityConfidence") not in {"explicit", "repository_metadata", "local_fallback"}:
        errors.append(f"{label}.identityConfidence: unsupported value")


def _validate_fingerprints(value: Any, label: str, errors: list[str]) -> None:
    fingerprints = _dict(value, label, errors)
    if fingerprints is None:
        return
    allowed = {"sourceFingerprint", "contextFingerprint", "policyFingerprint", "taskScopeFingerprint"}
    _keys(fingerprints, allowed, allowed, label, errors)
    for key in allowed:
        _string(fingerprints.get(key), f"{label}.{key}", errors)


def _validate_source(value: Any, label: str, errors: list[str]) -> None:
    source = _dict(value, label, errors)
    if source is None:
        return
    allowed = {"path", "kind", "scope", "scopeKind", "contentHash", "sizeBytes", "modifiedAt", "classificationConfidence", "usability", "evidenceSpans", "limitation"}
    _keys(source, {"path", "kind", "scope", "scopeKind", "contentHash", "sizeBytes", "classificationConfidence", "usability"}, allowed, label, errors)
    _relative_path(source.get("path"), f"{label}.path", errors)
    for key in ("kind", "scope", "scopeKind", "contentHash", "classificationConfidence", "usability"):
        _string(source.get(key), f"{label}.{key}", errors)
    if source.get("kind") not in SOURCE_KINDS:
        errors.append(f"{label}.kind: unsupported value")
    if source.get("scopeKind") not in {"root", "ancestor", "target", "global_candidate", "unknown"}:
        errors.append(f"{label}.scopeKind: unsupported value")
    if source.get("classificationConfidence") not in {"high", "medium", "low"}:
        errors.append(f"{label}.classificationConfidence: unsupported value")
    if source.get("usability") not in {"authoritative", "contextual", "candidate", "ignored"}:
        errors.append(f"{label}.usability: unsupported value")
    if not isinstance(source.get("sizeBytes"), int) or isinstance(source.get("sizeBytes"), bool) or source.get("sizeBytes") < 0:
        errors.append(f"{label}.sizeBytes: expected non-negative integer")
    for key in ("modifiedAt", "limitation"):
        if key in source:
            _string(source.get(key), f"{label}.{key}", errors, allow_null=True)
    if "evidenceSpans" in source:
        spans = _list(source.get("evidenceSpans"), f"{label}.evidenceSpans", errors)
        if spans is not None:
            for index, span_value in enumerate(spans):
                span = _dict(span_value, f"{label}.evidenceSpans[{index}]", errors)
                if span is None:
                    continue
                _keys(span, {"startLine", "endLine"}, {"startLine", "endLine"}, f"{label}.evidenceSpans[{index}]", errors)
                for key in ("startLine", "endLine"):
                    if not isinstance(span.get(key), int) or span.get(key) < 1:
                        errors.append(f"{label}.evidenceSpans[{index}].{key}: expected positive integer")


def _validate_signal(value: Any, label: str, errors: list[str], *, product: bool = False) -> None:
    signal = _dict(value, label, errors)
    if signal is None:
        return
    if product:
        allowed = {"product", "signal", "sourcePath", "confidence", "detectionMode"}
        _keys(signal, allowed, allowed, label, errors)
        _string(signal.get("product"), f"{label}.product", errors)
        product = signal.get("product")
        if product != "unknown" and (not isinstance(product, str) or not PRODUCT_ID.fullmatch(product)):
            errors.append(f"{label}.product: expected a safe project product/profile identifier")
    else:
        allowed = {"platform", "signal", "sourcePath", "confidence", "detectionMode"}
        _keys(signal, allowed, allowed, label, errors)
        _string(signal.get("platform"), f"{label}.platform", errors)
        if signal.get("platform") not in PLATFORMS:
            errors.append(f"{label}.platform: unsupported value")
    for key in ("signal", "sourcePath"):
        _string(signal.get(key), f"{label}.{key}", errors)
    if signal.get("confidence") not in {"high", "medium", "low"}:
        errors.append(f"{label}.confidence: unsupported value")
    if signal.get("detectionMode") not in {"explicit", "manifest", "heuristic"}:
        errors.append(f"{label}.detectionMode: unsupported value")


def _validate_gap_or_conflict(value: Any, label: str, errors: list[str], *, conflict: bool = False) -> None:
    item = _dict(value, label, errors)
    if item is None:
        return
    if conflict:
        allowed = {"conflictId", "sourcePaths", "subject", "blocking", "reason"}
        required = allowed
    else:
        allowed = {"gapId", "category", "expected", "blocking", "reason"}
        required = allowed
    _keys(item, required, allowed, label, errors)
    id_key = "conflictId" if conflict else "gapId"
    _string(item.get(id_key), f"{label}.{id_key}", errors)
    if conflict:
        source_paths = _list(item.get("sourcePaths"), f"{label}.sourcePaths", errors)
        if source_paths is not None:
            for index, source_path in enumerate(source_paths):
                _relative_path(source_path, f"{label}.sourcePaths[{index}]", errors)
        _string(item.get("subject"), f"{label}.subject", errors)
    else:
        _string(item.get("category"), f"{label}.category", errors)
        _string(item.get("expected"), f"{label}.expected", errors)
    _string(item.get("reason"), f"{label}.reason", errors)
    if not isinstance(item.get("blocking"), bool):
        errors.append(f"{label}.blocking: expected boolean")


def validate_context_map(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    context = _dict(value, "contextMap", errors)
    if context is None:
        return {"valid": False, "errors": errors}
    allowed = {"schemaVersion", "repository", "status", "fingerprints", "sources", "platformSignals", "productSignals", "manifestProfiles", "planRoots", "commandSources", "gaps", "conflicts", "persistence", "generatedAt", "discoveryVersion"}
    _keys(context, allowed, allowed, "contextMap", errors)
    if context.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"contextMap.schemaVersion: expected {SCHEMA_VERSION}")
    _validate_repository(context.get("repository"), "contextMap.repository", errors)
    if context.get("status") not in CONTEXT_STATUSES:
        errors.append("contextMap.status: unsupported value")
    _validate_fingerprints(context.get("fingerprints"), "contextMap.fingerprints", errors)
    sources = _list(context.get("sources"), "contextMap.sources", errors)
    if sources is not None:
        for index, item in enumerate(sources):
            _validate_source(item, f"contextMap.sources[{index}]", errors)
    signals = _list(context.get("platformSignals"), "contextMap.platformSignals", errors)
    if signals is not None:
        for index, item in enumerate(signals):
            _validate_signal(item, f"contextMap.platformSignals[{index}]", errors)
    products = _list(context.get("productSignals"), "contextMap.productSignals", errors)
    if products is not None:
        for index, item in enumerate(products):
            _validate_signal(item, f"contextMap.productSignals[{index}]", errors, product=True)
    _string_list(context.get("manifestProfiles"), "contextMap.manifestProfiles", errors)
    plans = _list(context.get("planRoots"), "contextMap.planRoots", errors)
    if plans is not None:
        for index, item in enumerate(plans):
            plan = _dict(item, f"contextMap.planRoots[{index}]", errors)
            if plan is None:
                continue
            _keys(plan, {"path", "status"}, {"path", "status"}, f"contextMap.planRoots[{index}]", errors)
            _relative_path(plan.get("path"), f"contextMap.planRoots[{index}].path", errors)
            if plan.get("status") not in {"canonical", "legacy", "unknown"}:
                errors.append(f"contextMap.planRoots[{index}].status: unsupported value")
    command_sources = _list(context.get("commandSources"), "contextMap.commandSources", errors)
    if command_sources is not None:
        for index, value in enumerate(command_sources):
            _relative_path(value, f"contextMap.commandSources[{index}]", errors)
    gaps = _list(context.get("gaps"), "contextMap.gaps", errors)
    if gaps is not None:
        for index, item in enumerate(gaps):
            _validate_gap_or_conflict(item, f"contextMap.gaps[{index}]", errors)
    conflicts = _list(context.get("conflicts"), "contextMap.conflicts", errors)
    if conflicts is not None:
        for index, item in enumerate(conflicts):
            _validate_gap_or_conflict(item, f"contextMap.conflicts[{index}]", errors, conflict=True)
    if context.get("persistence") not in {"external", "session_only", "unavailable"}:
        errors.append("contextMap.persistence: unsupported value")
    _string(context.get("generatedAt"), "contextMap.generatedAt", errors)
    _string(context.get("discoveryVersion"), "contextMap.discoveryVersion", errors)
    if context.get("discoveryVersion") != DISCOVERY_VERSION:
        errors.append(f"contextMap.discoveryVersion: expected {DISCOVERY_VERSION}")
    return {"valid": not errors, "errors": errors}


def validate_manifest(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    manifest = _dict(value, "manifest", errors)
    if manifest is None:
        return {"valid": False, "errors": errors}
    allowed = {"schemaVersion", "projectId", "profiles", "products", "productProfiles", "applications", "instructionSources", "planRoots", "policyRules", "notes"}
    _keys(manifest, {"schemaVersion", "projectId", "profiles"}, allowed, "manifest", errors)
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")
    _string(manifest.get("projectId"), "manifest.projectId", errors)
    _string_list(manifest.get("profiles"), "manifest.profiles", errors)
    for key in ("products", "productProfiles", "applications"):
        if key in manifest:
            _string_list(manifest.get(key), f"manifest.{key}", errors)
    for key in ("notes",):
        if key in manifest:
            _string_list(manifest.get(key), f"manifest.{key}", errors)
    if "instructionSources" in manifest:
        items = _list(manifest.get("instructionSources"), "manifest.instructionSources", errors)
        if items is not None:
            for index, value_item in enumerate(items):
                item = _dict(value_item, f"manifest.instructionSources[{index}]", errors)
                if item is None:
                    continue
                _keys(item, {"path", "authority"}, {"path", "authority"}, f"manifest.instructionSources[{index}]", errors)
                _relative_path(item.get("path"), f"manifest.instructionSources[{index}].path", errors)
                _string(item.get("authority"), f"manifest.instructionSources[{index}].authority", errors)
    if "planRoots" in manifest:
        roots = _dict(manifest.get("planRoots"), "manifest.planRoots", errors)
        if roots is not None:
            for key, item in roots.items():
                _relative_path(item, f"manifest.planRoots.{key}", errors)
    if "policyRules" in manifest:
        rules = _list(manifest.get("policyRules"), "manifest.policyRules", errors)
        if rules is not None:
            for index, value_item in enumerate(rules):
                item = _dict(value_item, f"manifest.policyRules[{index}]", errors)
                if item is None:
                    continue
                allowed_rule = {"ruleId", "title", "requirement", "enforcement", "appliesTo", "riskSignals", "requiredCapabilities", "requiredEvidence", "conflictsWith"}
                _keys(item, {"ruleId", "enforcement", "appliesTo"}, allowed_rule, f"manifest.policyRules[{index}]", errors)
                _string(item.get("ruleId"), f"manifest.policyRules[{index}].ruleId", errors)
                if "title" in item:
                    _string(item.get("title"), f"manifest.policyRules[{index}].title", errors)
                if "requirement" in item:
                    _string(item.get("requirement"), f"manifest.policyRules[{index}].requirement", errors)
                if item.get("enforcement") not in ENFORCEMENT:
                    errors.append(f"manifest.policyRules[{index}].enforcement: unsupported value")
                for key in ("appliesTo", "riskSignals", "requiredCapabilities", "requiredEvidence", "conflictsWith"):
                    if key in item:
                        _string_list(item.get(key), f"manifest.policyRules[{index}].{key}", errors)
    return {"valid": not errors, "errors": errors}


def validate_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    receipt = _dict(value, "receipt", errors)
    if receipt is None:
        return {"valid": False, "errors": errors}
    allowed = {"schemaVersion", "taskId", "repository", "fingerprints", "status", "inspectedSources", "selectedSources", "gaps", "conflicts", "evidence", "persistence", "terminalStatus", "issuedAt", "expiresAt"}
    _keys(receipt, {"schemaVersion", "taskId", "repository", "fingerprints", "status", "inspectedSources", "selectedSources", "gaps", "conflicts", "evidence", "persistence", "terminalStatus", "issuedAt"}, allowed, "receipt", errors)
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"receipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    _string(receipt.get("taskId"), "receipt.taskId", errors)
    _validate_repository(receipt.get("repository"), "receipt.repository", errors)
    _validate_fingerprints(receipt.get("fingerprints"), "receipt.fingerprints", errors)
    if receipt.get("status") not in CONTEXT_STATUSES:
        errors.append("receipt.status: unsupported value")
    inspected = _list(receipt.get("inspectedSources"), "receipt.inspectedSources", errors)
    if inspected is not None:
        for index, value_item in enumerate(inspected):
            item = _dict(value_item, f"receipt.inspectedSources[{index}]", errors)
            if item is None:
                continue
            _keys(item, {"path", "kind", "contentHash"}, {"path", "kind", "contentHash"}, f"receipt.inspectedSources[{index}]", errors)
            _relative_path(item.get("path"), f"receipt.inspectedSources[{index}].path", errors)
            _string(item.get("kind"), f"receipt.inspectedSources[{index}].kind", errors)
            _string(item.get("contentHash"), f"receipt.inspectedSources[{index}].contentHash", errors)
    selected_sources = _list(receipt.get("selectedSources"), "receipt.selectedSources", errors)
    if selected_sources is not None:
        for index, value in enumerate(selected_sources):
            _relative_path(value, f"receipt.selectedSources[{index}]", errors)
    gaps = _list(receipt.get("gaps"), "receipt.gaps", errors)
    if gaps is not None:
        for index, item in enumerate(gaps):
            _validate_gap_or_conflict(item, f"receipt.gaps[{index}]", errors)
    conflicts = _list(receipt.get("conflicts"), "receipt.conflicts", errors)
    if conflicts is not None:
        for index, item in enumerate(conflicts):
            _validate_gap_or_conflict(item, f"receipt.conflicts[{index}]", errors, conflict=True)
    evidence = _list(receipt.get("evidence"), "receipt.evidence", errors)
    if evidence is not None:
        for index, value_item in enumerate(evidence):
            item = _dict(value_item, f"receipt.evidence[{index}]", errors)
            if item is None:
                continue
            _keys(item, {"operation", "result", "summary"}, {"operation", "result", "summary"}, f"receipt.evidence[{index}]", errors)
            _string(item.get("operation"), f"receipt.evidence[{index}].operation", errors)
            if item.get("result") not in {"pass", "partial", "failed"}:
                errors.append(f"receipt.evidence[{index}].result: unsupported value")
            _string(item.get("summary"), f"receipt.evidence[{index}].summary", errors)
    if receipt.get("persistence") not in {"external", "session_only", "unavailable"}:
        errors.append("receipt.persistence: unsupported value")
    if receipt.get("terminalStatus") not in {"COMPLETED", "MISSING_COVERAGE", "BLOCKED"}:
        errors.append("receipt.terminalStatus: unsupported value")
    _string(receipt.get("issuedAt"), "receipt.issuedAt", errors)
    if "expiresAt" in receipt:
        _string(receipt.get("expiresAt"), "receipt.expiresAt", errors, allow_null=True)
    return {"valid": not errors, "errors": errors}


def _validate_rule(value: Any, label: str, errors: list[str]) -> None:
    rule = _dict(value, label, errors)
    if rule is None:
        return
    allowed = {"ruleId", "scope", "title", "requirement", "enforcement", "source", "sourceRef", "sourceEvidence", "appliesTo", "riskSignals", "requiredCapabilities", "requiredEvidence", "conflictsWith", "condition"}
    _keys(rule, {"ruleId", "scope", "title", "requirement", "enforcement", "source", "sourceRef", "appliesTo", "riskSignals", "requiredCapabilities", "requiredEvidence", "conflictsWith"}, allowed, label, errors)
    for key in ("ruleId", "title", "requirement", "source", "sourceRef"):
        _string(rule.get(key), f"{label}.{key}", errors)
    if rule.get("scope") not in POLICY_SCOPES:
        errors.append(f"{label}.scope: unsupported value")
    if rule.get("enforcement") not in ENFORCEMENT:
        errors.append(f"{label}.enforcement: unsupported value")
    if rule.get("source") not in POLICY_SOURCES:
        errors.append(f"{label}.source: unsupported value")
    if "sourceEvidence" in rule:
        _string(rule.get("sourceEvidence"), f"{label}.sourceEvidence", errors, allow_null=True)
    if "condition" in rule:
        _string(rule.get("condition"), f"{label}.condition", errors, allow_null=True)
    for key in ("appliesTo", "riskSignals", "requiredCapabilities", "requiredEvidence", "conflictsWith"):
        _string_list(rule.get(key), f"{label}.{key}", errors)


def validate_policy_bundle(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    bundle = _dict(value, "policyBundle", errors)
    if bundle is None:
        return {"valid": False, "errors": errors}
    allowed = {"schemaVersion", "repository", "fingerprints", "selectedProfiles", "rules", "requiredCapabilities", "requiredEvidence", "riskTier", "riskReasons", "conflicts", "mutationStatus", "policyVersion", "generatedAt"}
    _keys(bundle, allowed, allowed, "policyBundle", errors)
    if bundle.get("schemaVersion") != POLICY_SCHEMA_VERSION:
        errors.append(f"policyBundle.schemaVersion: expected {POLICY_SCHEMA_VERSION}")
    _validate_repository(bundle.get("repository"), "policyBundle.repository", errors)
    _validate_fingerprints(bundle.get("fingerprints"), "policyBundle.fingerprints", errors)
    _string_list(bundle.get("selectedProfiles"), "policyBundle.selectedProfiles", errors)
    rules = _list(bundle.get("rules"), "policyBundle.rules", errors)
    if rules is not None:
        for index, item in enumerate(rules):
            _validate_rule(item, f"policyBundle.rules[{index}]", errors)
    _string_list(bundle.get("requiredCapabilities"), "policyBundle.requiredCapabilities", errors)
    _string_list(bundle.get("requiredEvidence"), "policyBundle.requiredEvidence", errors)
    if bundle.get("riskTier") not in {"lightweight", "normal", "coupled", "high"}:
        errors.append("policyBundle.riskTier: unsupported value")
    _string_list(bundle.get("riskReasons"), "policyBundle.riskReasons", errors)
    conflicts = _list(bundle.get("conflicts"), "policyBundle.conflicts", errors)
    if conflicts is not None:
        for index, item in enumerate(conflicts):
            _validate_gap_or_conflict(item, f"policyBundle.conflicts[{index}]", errors, conflict=True)
    if bundle.get("mutationStatus") not in {"READY", "BLOCKED", "REVIEW_REQUIRED"}:
        errors.append("policyBundle.mutationStatus: unsupported value")
    _string(bundle.get("policyVersion"), "policyBundle.policyVersion", errors)
    _string(bundle.get("generatedAt"), "policyBundle.generatedAt", errors)
    return {"valid": not errors, "errors": errors}
