#!/usr/bin/env python3
"""Pure, dependency-free model policy selection for AE vNext.

The selector accepts already-normalized task facts, a host-owned capability
catalog, and a validated policy mapping.  It does not read files, inspect the
environment, call models, spawn workers, or persist state.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Mapping, Sequence

MODEL_SELECTION_SCHEMA_VERSION = "model-selection-policy-v1"
DECISION_SCHEMA_VERSION = "model-selection-decision-v1"
ASSIGNMENT_SCHEMA_VERSION = "model-assignment-v1"
TASK_SCHEMA_VERSION = "model-selection-task-v1"
HOST_CATALOG_SCHEMA_VERSION = "host-model-catalog-v1"
SELECTION_BINDING_KEYS = (
    "taskId",
    "taskClass",
    "projectId",
    "repositoryId",
    "tenantId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "sourceFingerprint",
    "policyFingerprint",
    "hostId",
)
ASSIGNMENT_DISPATCH_BINDING_KEYS = (
    "assignmentId",
    "status",
    "role",
    "scope",
    "desiredModel",
    "resolvedModel",
    "reasoningEffort",
    "capabilityStatus",
    "overrideEvidence",
    "task",
    "terminalStatus",
)

SUPPORTED_MODELS = ("model-fast", "model-balanced", "model-advanced")
REASONING_EFFORTS = ("low", "medium", "high", "max")
ROLES = ("worker", "normal-coordinator", "high-risk-coordinator", "final-audit")
ASSIGNMENT_STATUSES = (
    "DIRECT_NO_ASSIGNMENT",
    "SELECTED",
    "HOST_INHERITED",
    "CAPABILITY_UNAVAILABLE",
    "OVERRIDE_EVIDENCED",
    "FALLBACK_EVIDENCED",
    "INVALIDATED",
    "REJECTED",
)
TRUSTED_OVERRIDE_ORIGINS = ("explicit_user", "host_policy")
EXPLICIT_FALLBACK_TASK_CLASSES = {
    "coupled",
    "high-risk",
    "security",
    "cross-repository",
    "architecture",
    "difficult-root-cause",
}
DIRECT_ROUTES = {
    "DIRECT",
    "DIRECT_VALIDATION",
    "PLAN_ONLY",
    "QUESTION",
    "LOOKUP",
    "NARROW_READ_ONLY_VALIDATION",
}
ROUTES = DIRECT_ROUTES | {
    "DIRECT_WORK",
    "NEW_TASK",
    "CONTINUE_TASK",
    "EXECUTE_READY_PLAN",
    "REVIEW_ONLY",
}
TASK_CLASSES = {
    "lightweight",
    "small",
    "normal",
    "coupled",
    "high-risk",
    "security",
    "cross-repository",
    "architecture",
    "difficult-root-cause",
    "final-audit",
}
POLICY_FIELDS = {
    "schemaVersion",
    "policyId",
    "policyVersion",
    "policyFingerprint",
    "supportedModels",
    "workerDefault",
    "workerRules",
    "coordinatorRules",
    "fallbackRules",
    "maximumParallelism",
    "capabilityAllowlist",
    "exceptionRequirements",
    "expirySemantics",
}
ASSIGNMENT_FIELDS = {
    "schemaVersion",
    "assignmentId",
    "status",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "sourceFingerprint",
    "policyFingerprint",
    "role",
    "scope",
    "hostId",
    "desiredModel",
    "resolvedModel",
    "reasoningEffort",
    "selectionRationale",
    "capabilityStatus",
    "overrideEvidence",
    "issuedAt",
    "expiresAt",
    "terminalStatus",
    "task",
}
ASSIGNMENT_REQUIRED_FIELDS = ASSIGNMENT_FIELDS - {"task"}
TASK_FIELDS = {
    "schemaVersion",
    "route",
    "taskClass",
    "taskId",
    "projectId",
    "repositoryId",
    "tenantId",
    "packetVersion",
    "planVersion",
    "waveId",
    "attempt",
    "sourceFingerprint",
    "hostId",
    "issuedAt",
    "expiresAt",
    "needsCoordinator",
    "workerScopes",
    "coordinatorScope",
    "requestedRoles",
    "role",
    "scope",
    "override",
    "validationTime",
}
HOST_CATALOG_FIELDS = {
    "schemaVersion",
    "hostId",
    "availableModels",
    "inheritedModel",
    "inheritedReason",
}
OVERRIDE_FIELDS = {
    "origin",
    "model",
    "reasoningEffort",
    "taskId",
    "scopeId",
    "evidence",
    "approvalId",
    "issuedAt",
    "expiresAt",
    "policyFingerprint",
}
SCOPE_FIELDS = {"scopeId", "paths", "authority"}
WORKER_RULE_FIELDS = {"taskClasses", "model", "reasoningEffort"}
FALLBACK_RULE_FIELDS = {
    "ruleId",
    "enabled",
    "automatic",
    "fromModel",
    "toModel",
    "reasoningEffort",
    "roles",
    "taskClasses",
    "requiredEvidence",
    "approvalOrigin",
    "approvalId",
    "maxScopes",
    "maxLifetimeSeconds",
}


class ModelSelectionError(ValueError):
    """Raised when policy input is invalid and selection must fail closed."""


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used for policy fingerprints."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def policy_fingerprint(policy: Mapping[str, Any]) -> str:
    """Hash policy content without its self-referential fingerprint field."""

    if not isinstance(policy, Mapping):
        raise ModelSelectionError("policy: expected object")
    content = dict(policy)
    content.pop("policyFingerprint", None)
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def _keys(value: Mapping[str, Any], required: set[str], allowed: set[str], label: str, errors: list[str]) -> None:
    for key in sorted(required - set(value)):
        errors.append(f"{label}: missing required field {key}")
    for key in sorted(set(value) - allowed):
        errors.append(f"{label}: unknown field {key}")


def _object(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    return value


def _nonempty(value: Any, label: str, errors: list[str], *, allow_null: bool = False) -> None:
    if allow_null and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")


def _boolean(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, bool):
        errors.append(f"{label}: expected boolean")


def _positive_integer(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        errors.append(f"{label}: expected positive integer")


def _string_list(value: Any, label: str, errors: list[str], *, nonempty: bool = True, unique: bool = False) -> None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return
    if nonempty and not value:
        errors.append(f"{label}: expected non-empty array")
    if unique and all(isinstance(item, str) for item in value) and len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    for index, item in enumerate(value):
        _nonempty(item, f"{label}[{index}]", errors)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected timestamp")
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{label}: invalid timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label}: timestamp must include timezone")
        return None
    return parsed.astimezone(timezone.utc)


def _safe_path(value: Any, label: str, errors: list[str]) -> None:
    _nonempty(value, label, errors)
    if not isinstance(value, str) or not value.strip():
        return
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":") or ".." in normalized.split("/"):
        errors.append(f"{label}: expected repository-relative path")


def _validate_scope(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    scope = _object(value, label, errors)
    if scope is None:
        return None
    _keys(scope, SCOPE_FIELDS, SCOPE_FIELDS, label, errors)
    _nonempty(scope.get("scopeId"), f"{label}.scopeId", errors)
    paths = scope.get("paths")
    if not isinstance(paths, list) or not paths:
        errors.append(f"{label}.paths: expected non-empty array")
    else:
        if len(paths) != len(set(paths)):
            errors.append(f"{label}.paths: duplicate values are not allowed")
        for index, path in enumerate(paths):
            _safe_path(path, f"{label}.paths[{index}]", errors)
    if scope.get("authority") not in {"read", "write"}:
        errors.append(f"{label}.authority: unsupported value")
    return scope


def _validate_override_shape(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    override = _object(value, label, errors)
    if override is None:
        return None
    _keys(override, OVERRIDE_FIELDS - {"policyFingerprint"}, OVERRIDE_FIELDS, label, errors)
    if override.get("origin") not in TRUSTED_OVERRIDE_ORIGINS:
        errors.append(f"{label}.origin: untrusted override origin")
    if override.get("model") not in SUPPORTED_MODELS:
        errors.append(f"{label}.model: unsupported model")
    if override.get("reasoningEffort") not in REASONING_EFFORTS:
        errors.append(f"{label}.reasoningEffort: unsupported value")
    for key in ("taskId", "scopeId", "approvalId", "issuedAt", "expiresAt"):
        _nonempty(override.get(key), f"{label}.{key}", errors)
    _string_list(override.get("evidence"), f"{label}.evidence", errors, unique=True)
    issued = _timestamp(override.get("issuedAt"), f"{label}.issuedAt", errors)
    expires = _timestamp(override.get("expiresAt"), f"{label}.expiresAt", errors)
    if issued is not None and expires is not None and expires <= issued:
        errors.append(f"{label}: expiresAt must be after issuedAt")
    if "policyFingerprint" in override and not _sha256(override.get("policyFingerprint")):
        errors.append(f"{label}.policyFingerprint: expected lowercase SHA-256 fingerprint")
    return override


def _validate_policy_uncached(value: Any) -> dict[str, Any]:
    """Validate a model-selection policy and its deterministic fingerprint."""

    errors: list[str] = []
    policy = _object(value, "policy", errors)
    if policy is None:
        return {"valid": False, "errors": errors}
    _keys(policy, POLICY_FIELDS, POLICY_FIELDS, "policy", errors)
    if policy.get("schemaVersion") != MODEL_SELECTION_SCHEMA_VERSION:
        errors.append(f"policy.schemaVersion: expected {MODEL_SELECTION_SCHEMA_VERSION}")
    for key in ("policyId", "policyVersion"):
        _nonempty(policy.get(key), f"policy.{key}", errors)
    if not _sha256(policy.get("policyFingerprint")):
        errors.append("policy.policyFingerprint: expected lowercase SHA-256 fingerprint")

    supported = policy.get("supportedModels")
    _string_list(supported, "policy.supportedModels", errors, unique=True)
    if isinstance(supported, list):
        if all(isinstance(item, str) for item in supported) and set(supported) != set(SUPPORTED_MODELS):
            errors.append("policy.supportedModels: must contain the supported client model IDs")

    worker = _object(policy.get("workerDefault"), "policy.workerDefault", errors)
    if worker is not None:
        _keys(worker, {"model", "reasoningEffort"}, {"model", "reasoningEffort"}, "policy.workerDefault", errors)
        if worker.get("model") not in SUPPORTED_MODELS:
            errors.append("policy.workerDefault.model: unsupported model")
        if worker.get("reasoningEffort") not in REASONING_EFFORTS:
            errors.append("policy.workerDefault.reasoningEffort: unsupported reasoning effort")
        if isinstance(supported, list) and worker.get("model") not in supported:
            errors.append("policy.workerDefault.model: unsupported model")

    worker_rules = policy.get("workerRules")
    if not isinstance(worker_rules, list) or not worker_rules:
        errors.append("policy.workerRules: expected non-empty array")
        worker_rules = []
    covered_worker_classes: set[str] = set()
    for index, raw_rule in enumerate(worker_rules):
        label = f"policy.workerRules[{index}]"
        rule = _object(raw_rule, label, errors)
        if rule is None:
            continue
        _keys(rule, WORKER_RULE_FIELDS, WORKER_RULE_FIELDS, label, errors)
        model = rule.get("model")
        if model not in SUPPORTED_MODELS:
            errors.append(f"{label}.model: unsupported model")
        elif isinstance(supported, list) and model not in supported:
            errors.append(f"{label}.model: unsupported model")
        if rule.get("reasoningEffort") not in REASONING_EFFORTS:
            errors.append(f"{label}.reasoningEffort: unsupported reasoning effort")
        task_classes = rule.get("taskClasses")
        _string_list(task_classes, f"{label}.taskClasses", errors, unique=True)
        if isinstance(task_classes, list):
            for task_class in task_classes:
                if task_class not in TASK_CLASSES:
                    errors.append(f"{label}.taskClasses: unsupported task class")
                elif task_class in covered_worker_classes:
                    errors.append(f"{label}.taskClasses: duplicate task class {task_class}")
                else:
                    covered_worker_classes.add(task_class)
    missing_worker_classes = TASK_CLASSES - covered_worker_classes
    if missing_worker_classes:
        errors.append(
            "policy.workerRules: missing task classes " + ", ".join(sorted(missing_worker_classes))
        )

    rules = policy.get("coordinatorRules")
    if not isinstance(rules, list) or not rules:
        errors.append("policy.coordinatorRules: expected non-empty array")
        rules = []
    seen_roles: set[str] = set()
    for index, raw_rule in enumerate(rules):
        label = f"policy.coordinatorRules[{index}]"
        rule = _object(raw_rule, label, errors)
        if rule is None:
            continue
        allowed = {"role", "model", "reasoningEffort", "taskClasses"}
        _keys(rule, allowed, allowed, label, errors)
        role = rule.get("role")
        if role not in {"normal-coordinator", "high-risk-coordinator", "final-audit"}:
            errors.append(f"{label}.role: unsupported coordinator role")
        elif role in seen_roles:
            errors.append(f"{label}.role: duplicate coordinator role")
        else:
            seen_roles.add(role)
        model = rule.get("model")
        if model not in {"model-balanced", "model-advanced"}:
            errors.append(f"{label}.model: coordinators must use BalancedModel or AdvancedModel")
        if role == "normal-coordinator" and model != "model-balanced":
            errors.append(f"{label}: normal coordination must use model-balanced")
        if role in {"high-risk-coordinator", "final-audit"} and model != "model-advanced":
            errors.append(f"{label}: coupled/high-risk coordination must use model-advanced")
        if rule.get("reasoningEffort") not in REASONING_EFFORTS:
            errors.append(f"{label}.reasoningEffort: unsupported value")
        _string_list(rule.get("taskClasses"), f"{label}.taskClasses", errors, unique=True)
    if "normal-coordinator" not in seen_roles:
        errors.append("policy.coordinatorRules: missing normal-coordinator rule")
    if not ({"high-risk-coordinator", "final-audit"} & seen_roles):
        errors.append("policy.coordinatorRules: missing high-risk coordinator or final-audit rule")

    fallback_rules = policy.get("fallbackRules")
    if not isinstance(fallback_rules, list) or not fallback_rules:
        errors.append("policy.fallbackRules: expected non-empty array")
        fallback_rules = []
    seen_fallback_ids: set[str] = set()
    seen_normal_automatic = False
    seen_high_risk_manual = False
    for index, raw_rule in enumerate(fallback_rules):
        label = f"policy.fallbackRules[{index}]"
        rule = _object(raw_rule, label, errors)
        if rule is None:
            continue
        _keys(rule, FALLBACK_RULE_FIELDS, FALLBACK_RULE_FIELDS, label, errors)
        rule_id = rule.get("ruleId")
        _nonempty(rule_id, f"{label}.ruleId", errors)
        if isinstance(rule_id, str):
            if rule_id in seen_fallback_ids:
                errors.append(f"{label}.ruleId: duplicate fallback rule")
            seen_fallback_ids.add(rule_id)
        _boolean(rule.get("enabled"), f"{label}.enabled", errors)
        _boolean(rule.get("automatic"), f"{label}.automatic", errors)
        if rule.get("fromModel") not in SUPPORTED_MODELS:
            errors.append(f"{label}.fromModel: unsupported model")
        target = rule.get("toModel")
        if target not in {"model-balanced", "model-advanced"}:
            errors.append(f"{label}.toModel: fallback target must be BalancedModel or AdvancedModel")
        if target == rule.get("fromModel"):
            errors.append(f"{label}: fallback source and target must differ")
        effort = rule.get("reasoningEffort")
        if effort not in REASONING_EFFORTS:
            errors.append(f"{label}.reasoningEffort: unsupported value")
        elif target == "model-balanced" and effort != "high":
            errors.append(f"{label}: BalancedModel fallback must use high reasoning")
        elif target == "model-advanced" and effort != "max":
            errors.append(f"{label}: AdvancedModel fallback must use max reasoning")
        roles = rule.get("roles")
        _string_list(roles, f"{label}.roles", errors, unique=True)
        if isinstance(roles, list) and roles != ["worker"]:
            errors.append(f"{label}.roles: fallbacks are limited to worker roles")
        task_classes = rule.get("taskClasses")
        _string_list(task_classes, f"{label}.taskClasses", errors, unique=True)
        if isinstance(task_classes, list):
            for task_class in task_classes:
                if task_class not in TASK_CLASSES:
                    errors.append(f"{label}.taskClasses: unsupported task class")
                if task_class == "final-audit":
                    errors.append(f"{label}.taskClasses: final-audit cannot use fallback")
        _string_list(rule.get("requiredEvidence"), f"{label}.requiredEvidence", errors, unique=True)
        if rule.get("approvalOrigin") not in TRUSTED_OVERRIDE_ORIGINS:
            errors.append(f"{label}.approvalOrigin: untrusted origin")
        _nonempty(rule.get("approvalId"), f"{label}.approvalId", errors)
        if rule.get("maxScopes") != 1:
            errors.append(f"{label}.maxScopes: fallbacks are limited to one scope")
        _positive_integer(rule.get("maxLifetimeSeconds"), f"{label}.maxLifetimeSeconds", errors)
        if isinstance(rule.get("maxLifetimeSeconds"), int) and rule.get("maxLifetimeSeconds") > 3600:
            errors.append(f"{label}.maxLifetimeSeconds: cannot exceed 3600 seconds")
        if target == "model-balanced" and rule.get("automatic") is True:
            if rule.get("approvalOrigin") != "host_policy":
                errors.append(f"{label}: automatic BalancedModel fallback requires host_policy approval")
            if isinstance(task_classes, list) and not set(task_classes).issubset({"lightweight", "small", "normal"}):
                errors.append(f"{label}: automatic fallback is limited to lightweight/small/normal work")
            seen_normal_automatic = True
        elif target == "model-advanced" and rule.get("automatic") is True:
            if rule.get("approvalOrigin") != "host_policy":
                errors.append(f"{label}: automatic AdvancedModel fallback requires host_policy approval")
            if isinstance(task_classes, list) and set(task_classes) != {"normal"}:
                errors.append(f"{label}: automatic AdvancedModel fallback is limited to normal work")
        elif rule.get("automatic") is False:
            if rule.get("approvalOrigin") != "explicit_user":
                errors.append(f"{label}: explicit fallback requires explicit_user approval")
            if isinstance(task_classes, list) and not set(task_classes).issubset(EXPLICIT_FALLBACK_TASK_CLASSES):
                errors.append(f"{label}: explicit fallback is limited to high-risk work")
            if isinstance(task_classes, list) and set(task_classes) & EXPLICIT_FALLBACK_TASK_CLASSES:
                seen_high_risk_manual = True
    if not seen_normal_automatic:
        errors.append("policy.fallbackRules: missing automatic FastModel-to-BalancedModel worker fallback")
    if not seen_high_risk_manual:
        errors.append("policy.fallbackRules: missing explicit high-risk worker fallback")

    maximum = policy.get("maximumParallelism")
    _positive_integer(maximum, "policy.maximumParallelism", errors)
    if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum > 4:
        errors.append("policy.maximumParallelism: cannot exceed four workers")
    _string_list(policy.get("capabilityAllowlist"), "policy.capabilityAllowlist", errors, unique=True)

    exceptions = _object(policy.get("exceptionRequirements"), "policy.exceptionRequirements", errors)
    if exceptions is not None:
        allowed = {
            "allowedModels",
            "trustedOrigins",
            "requiredEvidence",
            "allowedTaskClasses",
            "maxScopes",
            "requiresTaskBinding",
            "requiresApproval",
            "requiresExpiry",
        }
        _keys(exceptions, allowed, allowed, "policy.exceptionRequirements", errors)
        _string_list(exceptions.get("allowedModels"), "policy.exceptionRequirements.allowedModels", errors, unique=True)
        if isinstance(exceptions.get("allowedModels"), list) and all(isinstance(item, str) for item in exceptions["allowedModels"]) and not set(exceptions["allowedModels"]).issubset({"model-balanced", "model-advanced"}):
            errors.append("policy.exceptionRequirements.allowedModels: unsupported exception model")
        _string_list(exceptions.get("trustedOrigins"), "policy.exceptionRequirements.trustedOrigins", errors, unique=True)
        if isinstance(exceptions.get("trustedOrigins"), list) and all(isinstance(item, str) for item in exceptions["trustedOrigins"]) and not set(exceptions["trustedOrigins"]).issubset(set(TRUSTED_OVERRIDE_ORIGINS)):
            errors.append("policy.exceptionRequirements.trustedOrigins: contains untrusted origin")
        _string_list(exceptions.get("requiredEvidence"), "policy.exceptionRequirements.requiredEvidence", errors, unique=True)
        _string_list(exceptions.get("allowedTaskClasses"), "policy.exceptionRequirements.allowedTaskClasses", errors, unique=True)
        if isinstance(exceptions.get("allowedTaskClasses"), list) and not set(exceptions["allowedTaskClasses"]).issubset(EXPLICIT_FALLBACK_TASK_CLASSES):
            errors.append("policy.exceptionRequirements.allowedTaskClasses: must contain only explicit high-risk task classes")
        if exceptions.get("maxScopes") != 1:
            errors.append("policy.exceptionRequirements.maxScopes: exceptions are limited to one scope")
        for key in ("requiresTaskBinding", "requiresApproval", "requiresExpiry"):
            if exceptions.get(key) is not True:
                errors.append(f"policy.exceptionRequirements.{key}: must be true")

    expiry = _object(policy.get("expirySemantics"), "policy.expirySemantics", errors)
    if expiry is not None:
        allowed = {"required", "maxLifetimeSeconds", "clock"}
        _keys(expiry, allowed, allowed, "policy.expirySemantics", errors)
        if expiry.get("required") is not True:
            errors.append("policy.expirySemantics.required: must be true")
        _positive_integer(expiry.get("maxLifetimeSeconds"), "policy.expirySemantics.maxLifetimeSeconds", errors)
        if expiry.get("clock") != "UTC":
            errors.append("policy.expirySemantics.clock: expected UTC")
    if isinstance(expiry, Mapping) and isinstance(expiry.get("maxLifetimeSeconds"), int):
        for index, rule in enumerate(fallback_rules):
            if isinstance(rule, Mapping) and isinstance(rule.get("maxLifetimeSeconds"), int) and rule["maxLifetimeSeconds"] > expiry["maxLifetimeSeconds"]:
                errors.append(f"policy.fallbackRules[{index}].maxLifetimeSeconds: exceeds policy expiry maxLifetimeSeconds")

    expected_fingerprint = policy_fingerprint(policy)
    if policy.get("policyFingerprint") != expected_fingerprint:
        errors.append("policy.policyFingerprint: does not match normalized policy content")
    return {"valid": not errors, "errors": errors, "policyFingerprint": expected_fingerprint}


@lru_cache(maxsize=16)
def _cached_policy_validation(serialized_policy: str) -> tuple[bool, tuple[str, ...], str | None]:
    """Cache validation by canonical policy content without caching mutable mappings."""

    policy = json.loads(serialized_policy)
    result = _validate_policy_uncached(policy)
    return bool(result.get("valid")), tuple(result.get("errors", [])), result.get("policyFingerprint")


def validate_policy(value: Any) -> dict[str, Any]:
    """Validate a policy, reusing results for unchanged canonical policy content."""

    if not isinstance(value, Mapping):
        return _validate_policy_uncached(value)
    try:
        serialized = canonical_json(value)
        valid, errors, fingerprint = _cached_policy_validation(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "errors": [f"policy: invalid JSON-compatible mapping ({type(exc).__name__})"],
        }
    return {
        "valid": valid,
        "errors": list(errors),
        "policyFingerprint": fingerprint,
    }


def validate_task_facts(value: Any) -> dict[str, Any]:
    """Validate the normalized, non-authoritative task facts accepted by the selector."""

    errors: list[str] = []
    facts = _object(value, "taskFacts", errors)
    if facts is None:
        return {"valid": False, "errors": errors}
    required = {
        "schemaVersion",
        "route",
        "taskClass",
        "taskId",
        "projectId",
        "repositoryId",
        "packetVersion",
        "planVersion",
        "waveId",
        "attempt",
        "sourceFingerprint",
        "hostId",
        "issuedAt",
        "expiresAt",
        "needsCoordinator",
        "workerScopes",
        "coordinatorScope",
    }
    _keys(facts, required, TASK_FIELDS, "taskFacts", errors)
    if facts.get("schemaVersion") != TASK_SCHEMA_VERSION:
        errors.append(f"taskFacts.schemaVersion: expected {TASK_SCHEMA_VERSION}")
    if facts.get("route") not in ROUTES:
        errors.append("taskFacts.route: unsupported value")
    if facts.get("taskClass") not in TASK_CLASSES:
        errors.append("taskFacts.taskClass: unsupported value")
    for key in ("taskId", "projectId", "repositoryId", "packetVersion", "planVersion", "waveId", "sourceFingerprint", "hostId"):
        _nonempty(facts.get(key), f"taskFacts.{key}", errors)
    if "tenantId" in facts:
        _nonempty(facts.get("tenantId"), "taskFacts.tenantId", errors, allow_null=True)
    _positive_integer(facts.get("attempt"), "taskFacts.attempt", errors)
    issued = _timestamp(facts.get("issuedAt"), "taskFacts.issuedAt", errors)
    expires = _timestamp(facts.get("expiresAt"), "taskFacts.expiresAt", errors)
    if issued is not None and expires is not None and expires <= issued:
        errors.append("taskFacts: expiresAt must be after issuedAt")
    validation_time = None
    if "validationTime" in facts and facts.get("validationTime") is not None:
        validation_time = _timestamp(facts.get("validationTime"), "taskFacts.validationTime", errors)
        if validation_time is not None and expires is not None and validation_time >= expires:
            errors.append("taskFacts.validationTime: task is expired at validation time")
    _boolean(facts.get("needsCoordinator"), "taskFacts.needsCoordinator", errors)
    scopes = facts.get("workerScopes")
    if not isinstance(scopes, list):
        errors.append("taskFacts.workerScopes: expected array")
    else:
        seen_scope_ids: set[str] = set()
        for index, scope in enumerate(scopes):
            validated = _validate_scope(scope, f"taskFacts.workerScopes[{index}]", errors)
            if validated is not None:
                scope_id = validated.get("scopeId")
                if scope_id in seen_scope_ids:
                    errors.append(f"taskFacts.workerScopes[{index}].scopeId: duplicate scope")
                seen_scope_ids.add(str(scope_id))
    coordinator_scope = facts.get("coordinatorScope")
    if coordinator_scope is not None:
        _validate_scope(coordinator_scope, "taskFacts.coordinatorScope", errors)
    if "scope" in facts and facts.get("scope") is not None:
        _validate_scope(facts.get("scope"), "taskFacts.scope", errors)
    for key in ("role",):
        if key in facts and facts.get(key) not in ROLES:
            errors.append(f"taskFacts.{key}: unsupported value")
    if "requestedRoles" in facts:
        _string_list(facts.get("requestedRoles"), "taskFacts.requestedRoles", errors, unique=True)
        if isinstance(facts.get("requestedRoles"), list):
            for role in facts["requestedRoles"]:
                if role not in ROLES:
                    errors.append("taskFacts.requestedRoles: unsupported role")
    if "override" in facts and facts.get("override") is not None:
        _validate_override_shape(facts.get("override"), "taskFacts.override", errors)
    return {"valid": not errors, "errors": errors}


def validate_host_catalog(value: Any) -> dict[str, Any]:
    """Validate a host capability catalog without making it authoritative."""

    if value is None:
        return {"valid": True, "errors": [], "absent": True}
    errors: list[str] = []
    catalog = _object(value, "hostCatalog", errors)
    if catalog is None:
        return {"valid": False, "errors": errors}
    _keys(catalog, HOST_CATALOG_FIELDS, HOST_CATALOG_FIELDS, "hostCatalog", errors)
    if catalog.get("schemaVersion") != HOST_CATALOG_SCHEMA_VERSION:
        errors.append(f"hostCatalog.schemaVersion: expected {HOST_CATALOG_SCHEMA_VERSION}")
    _nonempty(catalog.get("hostId"), "hostCatalog.hostId", errors)
    available = catalog.get("availableModels")
    if not isinstance(available, Mapping):
        errors.append("hostCatalog.availableModels: expected object")
    else:
        for model, efforts in available.items():
            _nonempty(model, "hostCatalog.availableModels.model", errors)
            _string_list(efforts, f"hostCatalog.availableModels.{model}", errors, unique=True)
            if isinstance(efforts, list) and any(item not in REASONING_EFFORTS for item in efforts):
                errors.append(f"hostCatalog.availableModels.{model}: unsupported reasoning effort")
    inherited = catalog.get("inheritedModel")
    if inherited is not None and inherited not in SUPPORTED_MODELS:
        errors.append("hostCatalog.inheritedModel: unsupported model")
    if inherited is not None:
        _nonempty(catalog.get("inheritedReason"), "hostCatalog.inheritedReason", errors)
    elif catalog.get("inheritedReason") is not None:
        errors.append("hostCatalog.inheritedReason: requires inheritedModel")
    return {"valid": not errors, "errors": errors, "absent": False}


def _rule_for(policy: Mapping[str, Any], role: str, task_class: str) -> Mapping[str, Any] | None:
    for rule in policy.get("coordinatorRules", []):
        if rule.get("role") == role and task_class in rule.get("taskClasses", []):
            return rule
    return None


def _worker_rule_for(policy: Mapping[str, Any], task_class: str) -> Mapping[str, Any] | None:
    """Return the single complexity-driven worker rule for a task class."""

    for rule in policy.get("workerRules", []):
        if isinstance(rule, Mapping) and task_class in rule.get("taskClasses", []):
            return rule
    return None


def _fallback_rule(
    policy: Mapping[str, Any],
    task_class: str,
    *,
    automatic: bool | None = None,
    from_model: str | None = None,
) -> Mapping[str, Any] | None:
    for rule in policy.get("fallbackRules", []):
        if not isinstance(rule, Mapping):
            continue
        if rule.get("enabled") is not True or task_class not in rule.get("taskClasses", []):
            continue
        if automatic is not None and rule.get("automatic") is not automatic:
            continue
        if "worker" not in rule.get("roles", []):
            continue
        if from_model is not None and rule.get("fromModel") != from_model:
            continue
        return rule
    return None


def _fallback_policy_ids(evidence: Any) -> list[str]:
    if not isinstance(evidence, list):
        return []
    values: list[str] = []
    for item in evidence:
        if not isinstance(item, str) or not item.startswith("fallback_policy:"):
            continue
        rule_id = item.split(":", 1)[1]
        if rule_id:
            values.append(rule_id)
    return values


def _high_risk(task_class: str) -> bool:
    return task_class in {
        "coupled",
        "high-risk",
        "security",
        "cross-repository",
        "architecture",
        "difficult-root-cause",
        "final-audit",
    }


def _coordinator_role(facts: Mapping[str, Any]) -> str:
    if facts.get("taskClass") == "final-audit" or facts.get("route") == "REVIEW_ONLY" and facts.get("taskClass") == "final-audit":
        return "final-audit"
    return "high-risk-coordinator" if _high_risk(str(facts.get("taskClass"))) else "normal-coordinator"


def _is_direct(facts: Mapping[str, Any]) -> bool:
    route = facts.get("route")
    if route in DIRECT_ROUTES:
        return True
    return (
        route == "DIRECT_WORK"
        and not facts.get("needsCoordinator")
        and not facts.get("workerScopes")
        and not facts.get("requestedRoles")
        and facts.get("role") is None
    )


def _available(catalog: Mapping[str, Any] | None, model: str, effort: str) -> bool:
    if not catalog:
        return False
    available = catalog.get("availableModels", {})
    if not isinstance(available, Mapping):
        return False
    efforts = available.get(model, [])
    return isinstance(efforts, list) and effort in efforts


def _assignment_id(facts: Mapping[str, Any], policy_fp: str, role: str, scope: Mapping[str, Any]) -> str:
    identity = {
        "taskId": facts.get("taskId"),
        "waveId": facts.get("waveId"),
        "attempt": facts.get("attempt"),
        "role": role,
        "scopeId": scope.get("scopeId"),
        "policyFingerprint": policy_fp,
    }
    return "assignment-" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:20]


def _base_assignment(
    facts: Mapping[str, Any],
    policy_fp: str,
    *,
    role: str,
    scope: Mapping[str, Any],
    desired_model: str | None,
    resolved_model: str | None,
    reasoning_effort: str | None,
    status: str,
    capability_status: str,
    rationale: str,
    override_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": ASSIGNMENT_SCHEMA_VERSION,
        "assignmentId": _assignment_id(facts, policy_fp, role, scope),
        "status": status,
        "taskId": facts["taskId"],
        "projectId": facts["projectId"],
        "repositoryId": facts["repositoryId"],
        "tenantId": facts.get("tenantId"),
        "packetVersion": facts["packetVersion"],
        "planVersion": facts["planVersion"],
        "waveId": facts["waveId"],
        "attempt": facts["attempt"],
        "sourceFingerprint": facts["sourceFingerprint"],
        "policyFingerprint": policy_fp,
        "role": role,
        "scope": deepcopy(dict(scope)),
        "hostId": facts["hostId"],
        "desiredModel": desired_model,
        "resolvedModel": resolved_model,
        "reasoningEffort": reasoning_effort,
        "selectionRationale": rationale,
        "capabilityStatus": capability_status,
        "overrideEvidence": deepcopy(dict(override_evidence)) if override_evidence is not None else None,
        "task": {
            "taskId": facts["taskId"],
            "taskClass": facts["taskClass"],
        },
        "issuedAt": facts["issuedAt"],
        "expiresAt": facts["expiresAt"],
        "terminalStatus": True,
    }


def _task_binding(facts: Mapping[str, Any], policy_fp: str) -> dict[str, Any]:
    binding = {key: facts.get(key) for key in SELECTION_BINDING_KEYS if key != "policyFingerprint"}
    binding["policyFingerprint"] = policy_fp
    return binding


def _assignment_dispatch_binding(assignment: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(assignment.get(key)) for key in ASSIGNMENT_DISPATCH_BINDING_KEYS}


def _validate_exception(
    override: Mapping[str, Any],
    facts: Mapping[str, Any],
    policy: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    host_catalog: Mapping[str, Any] | None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    shape = _validate_override_shape(override, "override", errors)
    if shape is None:
        return False, errors
    requirements = policy["exceptionRequirements"]
    if shape.get("origin") not in requirements["trustedOrigins"]:
        errors.append("override.origin: untrusted override origin")
    if shape.get("model") not in requirements["allowedModels"]:
        errors.append("override.model: not permitted by policy exception allowlist")
    if facts.get("taskClass") not in requirements.get("allowedTaskClasses", EXPLICIT_FALLBACK_TASK_CLASSES):
        errors.append("override: explicit fallback is limited to coupled/high-risk/security work")
    worker_rule = _worker_rule_for(policy, str(facts.get("taskClass")))
    if worker_rule is None:
        errors.append("override: no worker model rule exists for this task class")
    explicit_rule = _fallback_rule(policy, str(facts.get("taskClass")), automatic=False)
    required_evidence = requirements["requiredEvidence"]
    if explicit_rule is None:
        errors.append("override: no enabled explicit fallback rule exists for this task class")
    else:
        required_evidence = explicit_rule.get("requiredEvidence", required_evidence)
        if shape.get("origin") != explicit_rule.get("approvalOrigin"):
            errors.append("override.origin: explicit fallback requires explicit_user and the task-bound policy origin")
        if shape.get("model") != explicit_rule.get("toModel") or shape.get("reasoningEffort") != explicit_rule.get("reasoningEffort"):
            errors.append("override: model and reasoning effort do not match the task-bound fallback rule")
        if worker_rule is not None and explicit_rule.get("fromModel") != worker_rule.get("model"):
            errors.append("override: fallback source model does not match the complexity rule")
    if host_catalog is None:
        errors.append("override: host capability evidence is required")
    elif worker_rule is not None and _available(
        host_catalog,
        str(worker_rule.get("model")),
        str(worker_rule.get("reasoningEffort")),
    ):
        errors.append("override: the preferred complexity-rule model is available; fallback evidence is not applicable")
    elif host_catalog.get("inheritedModel") is not None:
        errors.append("override: host-inherited capability cannot be promoted by exception")
    if shape.get("taskId") != facts.get("taskId"):
        errors.append("override.taskId: does not match task binding")
    if shape.get("scopeId") != scope.get("scopeId"):
        errors.append("override.scopeId: does not match bounded scope")
    evidence_values = shape.get("evidence") if isinstance(shape.get("evidence"), list) else []
    evidence = {item for item in evidence_values if isinstance(item, str)}
    missing = set(required_evidence) - evidence
    if missing:
        errors.append("override.evidence: missing required exception evidence")
    if explicit_rule is not None:
        fallback_ids = _fallback_policy_ids(evidence_values)
        if len(fallback_ids) != 1 or fallback_ids[0] != explicit_rule.get("ruleId"):
            errors.append("override.evidence: explicit fallback policy rule binding is required")
    issued = _timestamp(shape.get("issuedAt"), "override.issuedAt", errors)
    expires = _timestamp(shape.get("expiresAt"), "override.expiresAt", errors)
    task_issued = _timestamp(facts.get("issuedAt"), "taskFacts.issuedAt", errors)
    task_expires = _timestamp(facts.get("expiresAt"), "taskFacts.expiresAt", errors)
    if issued is not None and task_issued is not None and issued != task_issued:
        errors.append("override.issuedAt: does not match task binding")
    if expires is not None and task_expires is not None and expires > task_expires:
        errors.append("override.expiresAt: exceeds task expiry")
    if not shape.get("approvalId"):
        errors.append("override.approvalId: approval is required")
    if shape.get("policyFingerprint") != policy.get("policyFingerprint"):
        errors.append("override.policyFingerprint: required and must match policy")
    validation_time = _timestamp(facts.get("validationTime"), "taskFacts.validationTime", errors) if facts.get("validationTime") else None
    if expires is not None and validation_time is not None and validation_time >= expires:
        errors.append("override.expiresAt: exception is expired at validation time")
    if explicit_rule is not None and issued is not None and expires is not None:
        maximum_lifetime = explicit_rule.get("maxLifetimeSeconds")
        if isinstance(maximum_lifetime, int) and (expires - issued).total_seconds() > maximum_lifetime:
            errors.append("override: expiry exceeds the task-bound fallback rule lifetime")
    if host_catalog is not None and not _available(host_catalog, str(shape.get("model")), str(shape.get("reasoningEffort"))):
        errors.append("override: exception model is not available in host catalog")
    return not errors, errors


def _validate_fallback_evidence(
    override: Mapping[str, Any],
    assignment: Mapping[str, Any],
    errors: list[str],
    *,
    expected_policy: Mapping[str, Any] | None = None,
) -> None:
    """Validate an automatically issued, policy-bound model fallback receipt."""

    if override.get("origin") != "host_policy":
        errors.append("assignment.overrideEvidence.origin: automatic fallback requires host_policy")
    evidence = override.get("evidence")
    evidence_values = evidence if isinstance(evidence, list) else []
    fallback_ids = _fallback_policy_ids(evidence)
    if len(fallback_ids) != 1 or not fallback_ids[0]:
        errors.append("assignment.overrideEvidence.evidence: fallback policy rule binding is required")
    if override.get("policyFingerprint") != assignment.get("policyFingerprint"):
        errors.append("assignment.overrideEvidence.policyFingerprint: does not match assignment policy")
    if expected_policy is not None and fallback_ids:
        rule = next(
            (
                item
                for item in expected_policy.get("fallbackRules", [])
                if isinstance(item, Mapping) and item.get("ruleId") == fallback_ids[0]
            ),
            None,
        )
        if rule is None or rule.get("enabled") is not True or rule.get("automatic") is not True:
            errors.append("assignment.overrideEvidence: fallback rule is not an enabled automatic policy rule")
        elif (
            rule.get("fromModel") != assignment.get("desiredModel")
            or rule.get("toModel") != assignment.get("resolvedModel")
            or rule.get("reasoningEffort") != assignment.get("reasoningEffort")
            or not set(rule.get("requiredEvidence", [])) <= set(evidence_values)
        ):
            errors.append("assignment.overrideEvidence: fallback rule does not authorize this model transition")
    else:
        if override.get("model") != assignment.get("resolvedModel"):
            errors.append("assignment.overrideEvidence.model: does not match resolved fallback model")
        if override.get("reasoningEffort") != assignment.get("reasoningEffort"):
            errors.append("assignment.overrideEvidence.reasoningEffort: does not match fallback effort")
        if assignment.get("desiredModel") == assignment.get("resolvedModel"):
            errors.append("assignment: fallback must change the unavailable model")


def _validate_explicit_fallback_evidence(
    override: Mapping[str, Any],
    assignment: Mapping[str, Any],
    errors: list[str],
    *,
    expected_policy: Mapping[str, Any] | None = None,
) -> None:
    """Validate an explicit, task-bound fallback receipt at assignment time."""

    if override.get("origin") != "explicit_user":
        errors.append("assignment.overrideEvidence.origin: explicit fallback requires explicit_user")
    evidence = override.get("evidence")
    evidence_values = evidence if isinstance(evidence, list) else []
    evidence_set = {item for item in evidence_values if isinstance(item, str)}
    fallback_ids = _fallback_policy_ids(evidence_values)
    if len(fallback_ids) != 1 or not fallback_ids[0]:
        errors.append("assignment.overrideEvidence.evidence: explicit fallback policy rule binding is required")
    if override.get("model") != assignment.get("resolvedModel"):
        errors.append("assignment.overrideEvidence.model: does not match resolved fallback model")
    if override.get("reasoningEffort") != assignment.get("reasoningEffort"):
        errors.append("assignment.overrideEvidence.reasoningEffort: does not match fallback effort")
    if assignment.get("desiredModel") == assignment.get("resolvedModel"):
        errors.append("assignment: explicit fallback must change the unavailable model")
    if expected_policy is None or not fallback_ids:
        return

    task_summary = assignment.get("task")
    task_class = task_summary.get("taskClass") if isinstance(task_summary, Mapping) else None
    if task_class not in EXPLICIT_FALLBACK_TASK_CLASSES:
        errors.append("assignment.task.taskClass: explicit fallback requires a high-risk task class")
        return
    rule = next(
        (
            item
            for item in expected_policy.get("fallbackRules", [])
            if isinstance(item, Mapping) and item.get("ruleId") == fallback_ids[0]
        ),
        None,
    )
    if (
        rule is None
        or rule.get("enabled") is not True
        or rule.get("automatic") is not False
        or task_class not in rule.get("taskClasses", [])
        or "worker" not in rule.get("roles", [])
    ):
        errors.append("assignment.overrideEvidence: explicit fallback rule is not enabled for this task class")
        return
    if (
        rule.get("approvalOrigin") != "explicit_user"
        or rule.get("fromModel") != assignment.get("desiredModel")
        or rule.get("toModel") != assignment.get("resolvedModel")
        or rule.get("reasoningEffort") != assignment.get("reasoningEffort")
        or not set(rule.get("requiredEvidence", [])) <= evidence_set
    ):
        errors.append("assignment.overrideEvidence: explicit fallback rule does not authorize this task-bound model transition")


def _rejected(route: Any, policy_fp: str, errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schemaVersion": DECISION_SCHEMA_VERSION,
        "status": "REJECTED",
        "route": route,
        "policyFingerprint": policy_fp,
        "assignments": [],
        "errors": list(errors),
    }


FAST_DAILY_SELECTION_SCHEMA_VERSION = "fast-daily-model-selection-v1"
FAST_DAILY_MOBILE_DECISION_SCHEMA_VERSION = "mobile-workflow-decision-v1"
FAST_DAILY_MODEL_RULES = {
    "lightweight": ("model-fast", "low"),
    "small": ("model-fast", "medium"),
    "normal": ("model-balanced", "high"),
}
FAST_DAILY_BYPASSED_CONTROLS = (
    "taskFacts",
    "sourceFingerprint",
    "policyFingerprint",
    "hostCatalog",
    "expiry",
    "coordinatorScope",
    "fallbackRules",
)


def _fast_daily_rejected(errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schemaVersion": FAST_DAILY_SELECTION_SCHEMA_VERSION,
        "status": "REJECTED",
        "route": "FAST_DAILY",
        "selectionBinding": "NONE",
        "strictModelSelectionBypass": False,
        "bypassedControls": [],
        "dispatchable": False,
        "assignments": [],
        "errors": list(errors),
    }


def select_fast_daily_model(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a validated lead-only mobile decision without strict bindings.

    This is deliberately separate from ``select_model_assignments``.  It does
    not validate task facts, policy fingerprints, host catalogs, expiry, or
    worker scopes, and it never returns a dispatchable assignment.  Callers
    must obtain the input from ``classify_mobile_workflow``; higher-risk,
    runtime, or delegated work must use ``select_model_assignments`` instead.
    """

    errors: list[str] = []
    if not isinstance(decision, Mapping):
        return _fast_daily_rejected(["decision: expected classifier object"])
    if decision.get("schemaVersion") != FAST_DAILY_MOBILE_DECISION_SCHEMA_VERSION:
        errors.append("decision.schemaVersion: expected a mobile classifier decision")
    if decision.get("decision") != "FAST_DAILY":
        errors.append("decision.decision: only FAST_DAILY can use the direct path")
    if decision.get("mobile") is not True:
        errors.append("decision.mobile: direct path requires a mobile decision")
    if decision.get("taskClass") not in FAST_DAILY_MODEL_RULES:
        errors.append("decision.taskClass: direct path requires lightweight, small, or normal")
    if decision.get("coordinator") is not None:
        errors.append("decision.coordinator: direct path is lead-only")
    if decision.get("workerStrategy") != "lead_only":
        errors.append("decision.workerStrategy: direct path cannot select workers")
    if decision.get("leadOnlyFastPath") is not True:
        errors.append("decision.leadOnlyFastPath: direct path requires explicit classifier authorization")
    if decision.get("runtimeRequested") is not False:
        errors.append("decision.runtimeRequested: direct path cannot claim runtime validation")
    if not isinstance(decision.get("matchedRiskSignals"), list) or decision.get("matchedRiskSignals"):
        errors.append("decision.matchedRiskSignals: direct path requires no risk signals")
    if not isinstance(decision.get("mutationRequested"), bool):
        errors.append("decision.mutationRequested: expected boolean")
    required_gates = decision.get("requiredGates") if isinstance(decision.get("requiredGates"), list) else []
    skipped_gates = decision.get("skippedGates") if isinstance(decision.get("skippedGates"), list) else []
    if decision.get("mutationRequested") is True:
        if "bounded_reuse_assessment" not in required_gates:
            errors.append("decision.requiredGates: source mutation requires bounded_reuse_assessment")
        if "bounded_reuse_assessment" in skipped_gates:
            errors.append("decision.skippedGates: source mutation cannot skip bounded_reuse_assessment")
    task_class = decision.get("taskClass")
    expected_model, expected_effort = FAST_DAILY_MODEL_RULES.get(task_class, (None, None)) if isinstance(task_class, str) else (None, None)
    if decision.get("model") != expected_model:
        errors.append("decision.model: does not match the V1-compatible complexity rule")
    if decision.get("reasoningEffort") != expected_effort:
        errors.append("decision.reasoningEffort: does not match the V1-compatible complexity rule")
    if decision.get("modelSelectionMode") != "FAST_DAILY_DIRECT":
        errors.append("decision.modelSelectionMode: direct mode is required")
    if decision.get("selectionBinding") != "SESSION_ONLY":
        errors.append("decision.selectionBinding: direct mode is session-only")
    if decision.get("strictModelSelectionBypass") is not True:
        errors.append("decision.strictModelSelectionBypass: explicit fast-lane authorization is required")
    if decision.get("bypassedControls") != list(FAST_DAILY_BYPASSED_CONTROLS):
        errors.append("decision.bypassedControls: does not match the bounded fast-lane contract")
    if errors:
        return _fast_daily_rejected(errors)

    return {
        "schemaVersion": FAST_DAILY_SELECTION_SCHEMA_VERSION,
        "status": "FAST_DAILY_DIRECT",
        "route": "FAST_DAILY",
        "taskClass": decision["taskClass"],
        "model": expected_model,
        "reasoningEffort": expected_effort,
        "coordinator": None,
        "workerStrategy": "lead_only",
        "selectionBinding": "SESSION_ONLY",
        "strictModelSelectionBypass": True,
        "bypassedControls": list(FAST_DAILY_BYPASSED_CONTROLS),
        "dispatchable": False,
        "assignments": [],
        "rationale": (
            "Use the V1-compatible model directly for this lead-only source task; "
            "strict worker-assignment controls are intentionally not constructed."
        ),
        "errors": [],
    }


def validate_fast_daily_model_selection(value: Any) -> dict[str, Any]:
    """Validate the non-dispatchable direct-selection envelope."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["selection: expected object"]}
    if value.get("schemaVersion") != FAST_DAILY_SELECTION_SCHEMA_VERSION:
        errors.append(f"selection.schemaVersion: expected {FAST_DAILY_SELECTION_SCHEMA_VERSION}")
    if value.get("status") != "FAST_DAILY_DIRECT":
        errors.append("selection.status: expected FAST_DAILY_DIRECT")
    if value.get("route") != "FAST_DAILY":
        errors.append("selection.route: expected FAST_DAILY")
    if value.get("taskClass") not in FAST_DAILY_MODEL_RULES:
        errors.append("selection.taskClass: expected lightweight, small, or normal")
    expected_model, expected_effort = FAST_DAILY_MODEL_RULES.get(
        value.get("taskClass"), (None, None)
    ) if isinstance(value.get("taskClass"), str) else (None, None)
    if value.get("model") != expected_model:
        errors.append("selection.model: does not match task class")
    if value.get("reasoningEffort") != expected_effort:
        errors.append("selection.reasoningEffort: does not match task class")
    if value.get("coordinator") is not None or value.get("workerStrategy") != "lead_only":
        errors.append("selection: direct path must be lead-only")
    if value.get("selectionBinding") != "SESSION_ONLY":
        errors.append("selection.selectionBinding: expected SESSION_ONLY")
    if value.get("strictModelSelectionBypass") is not True:
        errors.append("selection.strictModelSelectionBypass: expected true")
    if value.get("bypassedControls") != list(FAST_DAILY_BYPASSED_CONTROLS):
        errors.append("selection.bypassedControls: unexpected control list")
    if value.get("dispatchable") is not False:
        errors.append("selection.dispatchable: direct selection cannot be dispatched")
    if value.get("assignments") != []:
        errors.append("selection.assignments: direct selection cannot contain assignments")
    return {"valid": not errors, "errors": errors}


def select_model_assignments(
    task_facts: Mapping[str, Any],
    host_catalog: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Select coordinator/worker assignments from normalized facts.

    Invalid policy input raises ``ModelSelectionError`` because continuing
    would make the policy fingerprint untrustworthy.  Invalid task or host
    input returns an explicit ``REJECTED`` decision without an assignment.
    """

    policy_result = validate_policy(policy)
    if not policy_result["valid"]:
        raise ModelSelectionError("invalid model-selection policy: " + "; ".join(policy_result["errors"]))
    policy_fp = policy_result["policyFingerprint"]

    task_result = validate_task_facts(task_facts)
    if not task_result["valid"]:
        route = task_facts.get("route") if isinstance(task_facts, Mapping) else None
        return _rejected(route, policy_fp, task_result["errors"])
    facts = task_facts
    task_issued = _timestamp(facts.get("issuedAt"), "taskFacts.issuedAt", [])
    task_expires = _timestamp(facts.get("expiresAt"), "taskFacts.expiresAt", [])
    if task_issued is not None and task_expires is not None:
        maximum_lifetime = policy["expirySemantics"]["maxLifetimeSeconds"]
        if (task_expires - task_issued).total_seconds() > maximum_lifetime:
            return _rejected(facts["route"], policy_fp, ["taskFacts: expiry exceeds policy maxLifetimeSeconds"])

    if _is_direct(facts):
        return {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "status": "DIRECT_NO_ASSIGNMENT",
            "route": facts["route"],
            "policyFingerprint": policy_fp,
            "taskBinding": _task_binding(facts, policy_fp),
            "assignmentBindings": [],
            "assignments": [],
        }

    catalog_result = validate_host_catalog(host_catalog)
    if not catalog_result["valid"]:
        return _rejected(facts["route"], policy_fp, catalog_result["errors"])
    catalog = host_catalog if isinstance(host_catalog, Mapping) else None
    if catalog is not None and catalog.get("hostId") != facts.get("hostId"):
        return _rejected(facts["route"], policy_fp, ["hostCatalog.hostId: does not match task host binding"])

    requested_roles = list(facts.get("requestedRoles") or [])
    if facts.get("role") is not None and facts["role"] not in requested_roles:
        requested_roles.append(facts["role"])
    worker_scopes = list(facts.get("workerScopes") or [])
    if facts.get("scope") is not None and facts.get("role") == "worker":
        if not worker_scopes:
            worker_scopes = [facts["scope"]]
        elif all(scope.get("scopeId") != facts["scope"].get("scopeId") for scope in worker_scopes):
            return _rejected(facts["route"], policy_fp, ["taskFacts.scope: does not match worker scope list"])
    wants_worker = "worker" in requested_roles or (not requested_roles and bool(worker_scopes))
    wants_coordinator = bool(facts.get("needsCoordinator")) or any(role != "worker" for role in requested_roles)
    if not wants_worker and not wants_coordinator:
        return {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "status": "DIRECT_NO_ASSIGNMENT",
            "route": facts["route"],
            "policyFingerprint": policy_fp,
            "taskBinding": _task_binding(facts, policy_fp),
            "assignmentBindings": [],
            "assignments": [],
        }
    if len(worker_scopes) > policy["maximumParallelism"]:
        return _rejected(facts["route"], policy_fp, ["worker scope count exceeds policy maximumParallelism"])
    if wants_worker and not worker_scopes:
        return _rejected(facts["route"], policy_fp, ["worker selection requires a bounded worker scope"])

    coordinator_role = _coordinator_role(facts)
    if wants_coordinator:
        explicit_coordinators = [role for role in requested_roles if role != "worker"]
        if explicit_coordinators:
            if len(explicit_coordinators) != 1 or explicit_coordinators[0] != coordinator_role:
                return _rejected(facts["route"], policy_fp, ["requested coordinator role does not match verified task class"])
        if _rule_for(policy, coordinator_role, str(facts["taskClass"])) is None:
            return _rejected(facts["route"], policy_fp, ["policy has no coordinator rule for task class"])

    override = facts.get("override")
    if override is not None and not wants_worker:
        return _rejected(facts["route"], policy_fp, ["override: exceptions are limited to bounded worker scopes"])
    if override is not None and len(worker_scopes) > policy["exceptionRequirements"]["maxScopes"]:
        return _rejected(facts["route"], policy_fp, ["override: more than one worker scope is not permitted"])
    if override is not None:
        if not any(
            isinstance(scope, Mapping) and scope.get("scopeId") == override.get("scopeId")
            for scope in worker_scopes
        ):
            return _rejected(facts["route"], policy_fp, ["override.scopeId: does not match any bounded worker scope"])
        if facts.get("taskClass") == "final-audit":
            return _rejected(facts["route"], policy_fp, ["override: final-audit workers cannot use fallback exceptions"])

    assignments: list[dict[str, Any]] = []
    if wants_coordinator:
        rule = _rule_for(policy, coordinator_role, str(facts["taskClass"]))
        assert rule is not None
        scope = facts.get("coordinatorScope") or {"scopeId": "coordinator", "paths": ["."], "authority": "read"}
        if not isinstance(scope, Mapping):
            return _rejected(facts["route"], policy_fp, ["taskFacts.coordinatorScope: expected object"])
        desired = str(rule["model"])
        effort = str(rule["reasoningEffort"])
        if _available(catalog, desired, effort):
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role=coordinator_role,
                scope=scope,
                desired_model=desired,
                resolved_model=desired,
                reasoning_effort=effort,
                status="SELECTED",
                capability_status="AVAILABLE",
                rationale=f"Policy role {coordinator_role} selects {desired} for {facts['taskClass']} work.",
            ))
        elif catalog is not None and catalog.get("inheritedModel") is not None:
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role=coordinator_role,
                scope=scope,
                desired_model=desired,
                resolved_model=catalog.get("inheritedModel"),
                reasoning_effort=None,
                status="HOST_INHERITED",
                capability_status="INHERITED",
                rationale="Host explicitly inherited its configured model; policy selection is not reported as selected.",
            ))
        else:
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role=coordinator_role,
                scope=scope,
                desired_model=desired,
                resolved_model=None,
                reasoning_effort=None,
                status="CAPABILITY_UNAVAILABLE",
                capability_status="UNAVAILABLE",
                rationale=f"Policy requires {desired} for {coordinator_role}, but the host catalog does not expose it.",
            ))

    for scope in worker_scopes if wants_worker else []:
        if not isinstance(scope, Mapping):
            return _rejected(facts["route"], policy_fp, ["worker scope: expected object"])
        worker_rule = _worker_rule_for(policy, str(facts["taskClass"]))
        if worker_rule is None:
            return _rejected(
                facts["route"],
                policy_fp,
                [f"policy has no worker model rule for {facts['taskClass']} work"],
            )
        desired = str(worker_rule["model"])
        effort = str(worker_rule["reasoningEffort"])
        exception_evidence: Mapping[str, Any] | None = None
        exception_valid = False
        exception_errors: list[str] = []
        if override is not None and override.get("scopeId") == scope.get("scopeId"):
            exception_valid, exception_errors = _validate_exception(
                override,
                facts,
                policy,
                scope,
                host_catalog=catalog,
            )
            if exception_valid:
                exception_evidence = override
                desired_exception_model = str(override["model"])
                exception_effort = str(override["reasoningEffort"])
                assignments.append(_base_assignment(
                    facts,
                    policy_fp,
                    role="worker",
                    scope=scope,
                    desired_model=desired,
                    resolved_model=desired_exception_model,
                    reasoning_effort=exception_effort,
                    status="OVERRIDE_EVIDENCED",
                    capability_status="OVERRIDE_APPROVED",
                    rationale="Trusted, task-bound exception evidence permits a named model for this one bounded worker scope.",
                    override_evidence=exception_evidence,
                ))
                continue
            return _rejected(facts["route"], policy_fp, exception_errors)
        if _available(catalog, desired, effort):
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role="worker",
                scope=scope,
                desired_model=desired,
                resolved_model=desired,
                reasoning_effort=effort,
                status="SELECTED",
                capability_status="AVAILABLE",
                rationale=(
                    f"Complexity rule for {facts['taskClass']} work assigns "
                    f"{desired} at {effort} reasoning to this bounded worker scope."
                ),
            ))
            continue

        automatic_fallback_rule = _fallback_rule(
            policy,
            str(facts["taskClass"]),
            automatic=True,
            from_model=desired,
        )
        automatic_fallback_allowed = (
            override is None
            and automatic_fallback_rule is not None
            and len(worker_scopes) <= automatic_fallback_rule["maxScopes"]
            and catalog is not None
            and catalog.get("inheritedModel") is None
            and _available(
                catalog,
                str(automatic_fallback_rule["toModel"]),
                str(automatic_fallback_rule["reasoningEffort"]),
            )
        )
        if automatic_fallback_allowed and automatic_fallback_rule is not None:
            fallback_evidence = {
                "origin": automatic_fallback_rule["approvalOrigin"],
                "model": automatic_fallback_rule["toModel"],
                "reasoningEffort": automatic_fallback_rule["reasoningEffort"],
                "taskId": facts["taskId"],
                "scopeId": scope["scopeId"],
                "evidence": list(automatic_fallback_rule["requiredEvidence"]) + [
                    f"fallback_policy:{automatic_fallback_rule['ruleId']}"
                ],
                "approvalId": automatic_fallback_rule["approvalId"],
                "issuedAt": facts["issuedAt"],
                "expiresAt": facts["expiresAt"],
                "policyFingerprint": policy_fp,
            }
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role="worker",
                scope=scope,
                desired_model=desired,
                resolved_model=str(automatic_fallback_rule["toModel"]),
                reasoning_effort=str(automatic_fallback_rule["reasoningEffort"]),
                status="FALLBACK_EVIDENCED",
                capability_status="FALLBACK_APPROVED",
                rationale=(
                    f"{desired} at {effort} is unavailable; the fingerprinted policy permits "
                    f"one bounded worker to use {automatic_fallback_rule['toModel']} at "
                    f"{automatic_fallback_rule['reasoningEffort']} reasoning."
                ),
                override_evidence=fallback_evidence,
            ))
        elif catalog is not None and catalog.get("inheritedModel") is not None:
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role="worker",
                scope=scope,
                desired_model=desired,
                resolved_model=catalog.get("inheritedModel"),
                reasoning_effort=None,
                status="HOST_INHERITED",
                capability_status="INHERITED",
                rationale="Host explicitly inherited its configured model after the worker model was unavailable.",
            ))
        else:
            assignments.append(_base_assignment(
                facts,
                policy_fp,
                role="worker",
                scope=scope,
                desired_model=desired,
                resolved_model=None,
                reasoning_effort=None,
                status="CAPABILITY_UNAVAILABLE",
                capability_status="UNAVAILABLE",
                rationale=(
                    f"Complexity rule requires {desired} at {effort} reasoning, but the "
                    "host exposes neither the preferred model nor an approved fallback."
                ),
            ))

    statuses = {assignment["status"] for assignment in assignments}
    if "CAPABILITY_UNAVAILABLE" in statuses:
        status = "CAPABILITY_UNAVAILABLE"
    elif "FALLBACK_EVIDENCED" in statuses:
        status = "FALLBACK_EVIDENCED"
    elif "OVERRIDE_EVIDENCED" in statuses:
        status = "OVERRIDE_EVIDENCED"
    elif "HOST_INHERITED" in statuses:
        status = "HOST_INHERITED"
    else:
        status = "SELECTED"
    return {
        "schemaVersion": DECISION_SCHEMA_VERSION,
        "status": status,
        "route": facts["route"],
        "policyFingerprint": policy_fp,
        "taskBinding": _task_binding(facts, policy_fp),
        "assignmentBindings": [_assignment_dispatch_binding(assignment) for assignment in assignments],
        "assignments": assignments,
    }


def _validate_assignment_model_policy(
    assignment: Mapping[str, Any],
    policy: Mapping[str, Any],
    errors: list[str],
) -> None:
    """Verify that an assignment still matches its complexity-driven policy rule."""

    role = assignment.get("role")
    status = assignment.get("status")
    if role == "worker":
        task_summary = assignment.get("task")
        task_class = task_summary.get("taskClass") if isinstance(task_summary, Mapping) else None
        if task_class not in TASK_CLASSES:
            errors.append("assignment.task.taskClass: required to validate adaptive worker selection")
            return
        rule = _worker_rule_for(policy, str(task_class))
        if rule is None:
            errors.append(f"assignment: policy has no worker model rule for {task_class} work")
            return
        if status in {"SELECTED", "OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"}:
            if assignment.get("desiredModel") != rule.get("model"):
                errors.append("assignment.desiredModel: does not match the complexity rule")
        if status == "SELECTED":
            if assignment.get("resolvedModel") != rule.get("model"):
                errors.append("assignment.resolvedModel: selected assignment cannot silently escalate from the complexity rule")
            if assignment.get("reasoningEffort") != rule.get("reasoningEffort"):
                errors.append("assignment.reasoningEffort: does not match the complexity rule")
        elif status == "FALLBACK_EVIDENCED" and assignment.get("desiredModel") != rule.get("model"):
            errors.append("assignment.desiredModel: fallback does not match the complexity rule")
        return

    task_summary = assignment.get("task")
    task_class = task_summary.get("taskClass") if isinstance(task_summary, Mapping) else None
    if task_class not in TASK_CLASSES or status not in {"SELECTED", "FALLBACK_EVIDENCED"}:
        return
    expected_role = _coordinator_role({"taskClass": task_class})
    if role != expected_role:
        errors.append("assignment.role: does not match the complexity-driven coordinator rule")
        return
    rule = _rule_for(policy, role, str(task_class))
    if rule is None:
        errors.append(f"assignment: policy has no coordinator rule for {task_class} work")
        return
    if assignment.get("desiredModel") != rule.get("model") or assignment.get("resolvedModel") != rule.get("model"):
        errors.append("assignment: coordinator model does not match the policy rule")
    if assignment.get("reasoningEffort") != rule.get("reasoningEffort"):
        errors.append("assignment.reasoningEffort: coordinator effort does not match the policy rule")


def validate_assignment(
    value: Any,
    *,
    expected_policy: Mapping[str, Any] | None = None,
    expected_policy_fingerprint: str | None = None,
    expected_binding: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate one assignment and optionally its policy/binding freshness."""

    errors: list[str] = []
    assignment = _object(value, "assignment", errors)
    if assignment is None:
        return {"valid": False, "errors": errors}
    required = ASSIGNMENT_REQUIRED_FIELDS
    _keys(assignment, required, ASSIGNMENT_FIELDS, "assignment", errors)
    if assignment.get("schemaVersion") != ASSIGNMENT_SCHEMA_VERSION:
        errors.append(f"assignment.schemaVersion: expected {ASSIGNMENT_SCHEMA_VERSION}")
    if assignment.get("status") not in ASSIGNMENT_STATUSES:
        errors.append("assignment.status: unsupported value")
    for key in ("assignmentId", "taskId", "projectId", "repositoryId", "packetVersion", "planVersion", "waveId", "sourceFingerprint", "hostId", "selectionRationale"):
        _nonempty(assignment.get(key), f"assignment.{key}", errors)
    if "tenantId" in assignment:
        _nonempty(assignment.get("tenantId"), "assignment.tenantId", errors, allow_null=True)
    task_summary = assignment.get("task")
    if task_summary is not None:
        if not isinstance(task_summary, Mapping):
            errors.append("assignment.task: expected object")
        else:
            _keys(task_summary, {"taskId"}, {"taskId", "taskClass"}, "assignment.task", errors)
            _nonempty(task_summary.get("taskId"), "assignment.task.taskId", errors)
            if task_summary.get("taskId") != assignment.get("taskId"):
                errors.append("assignment.task.taskId: does not match assignment")
            if "taskClass" in task_summary and task_summary.get("taskClass") not in TASK_CLASSES:
                errors.append("assignment.task.taskClass: unsupported task class")
    _positive_integer(assignment.get("attempt"), "assignment.attempt", errors)
    if not _sha256(assignment.get("policyFingerprint")):
        errors.append("assignment.policyFingerprint: expected lowercase SHA-256 fingerprint")
    if assignment.get("role") not in ROLES:
        errors.append("assignment.role: unsupported value")
    _validate_scope(assignment.get("scope"), "assignment.scope", errors)
    if (
        isinstance(assignment.get("scope"), Mapping)
        and isinstance(assignment.get("assignmentId"), str)
        and isinstance(assignment.get("policyFingerprint"), str)
        and isinstance(assignment.get("role"), str)
    ):
        expected_assignment_id = _assignment_id(
            assignment,
            assignment["policyFingerprint"],
            assignment["role"],
            assignment["scope"],
        )
        if assignment.get("assignmentId") != expected_assignment_id:
            errors.append("assignment.assignmentId: does not match the bound assignment identity")
    for key in ("desiredModel", "resolvedModel"):
        model = assignment.get(key)
        if model is not None and model not in SUPPORTED_MODELS:
            errors.append(f"assignment.{key}: unsupported model")
    effort = assignment.get("reasoningEffort")
    if effort is not None and effort not in REASONING_EFFORTS:
        errors.append("assignment.reasoningEffort: unsupported value")
    capability_statuses = {"AVAILABLE", "UNAVAILABLE", "INHERITED", "OVERRIDE_APPROVED", "FALLBACK_APPROVED", "INVALID"}
    if assignment.get("capabilityStatus") not in capability_statuses:
        errors.append("assignment.capabilityStatus: unsupported value")
    _boolean(assignment.get("terminalStatus"), "assignment.terminalStatus", errors)
    issued = _timestamp(assignment.get("issuedAt"), "assignment.issuedAt", errors)
    expires = _timestamp(assignment.get("expiresAt"), "assignment.expiresAt", errors)
    if issued is not None and expires is not None and expires <= issued:
        errors.append("assignment: expiresAt must be after issuedAt")
    current = _timestamp(now, "now", errors) if now is not None else None
    if current is not None and issued is not None and current < issued:
        errors.append("assignment: not yet valid")
    if current is not None and expires is not None and current >= expires:
        errors.append("assignment: expired")

    override = assignment.get("overrideEvidence")
    if override is not None:
        _validate_override_shape(override, "assignment.overrideEvidence", errors)
        if isinstance(override, Mapping):
            if override.get("taskId") != assignment.get("taskId"):
                errors.append("assignment.overrideEvidence.taskId: does not match assignment")
            assignment_scope = assignment.get("scope") if isinstance(assignment.get("scope"), Mapping) else {}
            if override.get("scopeId") != assignment_scope.get("scopeId"):
                errors.append("assignment.overrideEvidence.scopeId: does not match assignment scope")
            if override.get("issuedAt") != assignment.get("issuedAt"):
                errors.append("assignment.overrideEvidence.issuedAt: does not match assignment window")
            if override.get("expiresAt") != assignment.get("expiresAt"):
                errors.append("assignment.overrideEvidence.expiresAt: does not match assignment window")
    status = assignment.get("status")
    if status == "SELECTED" and (assignment.get("resolvedModel") is None or assignment.get("reasoningEffort") is None):
        errors.append("assignment: selected status requires resolved model and reasoning effort")
    if status == "CAPABILITY_UNAVAILABLE" and assignment.get("resolvedModel") is not None:
        errors.append("assignment: unavailable status cannot contain resolved model")
    if status == "OVERRIDE_EVIDENCED" and override is None:
        errors.append("assignment: override-evidenced status requires override evidence")
    if status == "FALLBACK_EVIDENCED" and override is None:
        errors.append("assignment: fallback-evidenced status requires fallback evidence")
    if status in {"OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"} and isinstance(override, Mapping):
        if not _sha256(override.get("policyFingerprint")):
            errors.append("assignment.overrideEvidence.policyFingerprint: required lowercase SHA-256 fingerprint")
        elif override.get("policyFingerprint") != assignment.get("policyFingerprint"):
            errors.append("assignment.overrideEvidence.policyFingerprint: does not match assignment policy")
    if status == "FALLBACK_EVIDENCED":
        if assignment.get("capabilityStatus") != "FALLBACK_APPROVED":
            errors.append("assignment.capabilityStatus: fallback evidence requires FALLBACK_APPROVED")
        if isinstance(override, Mapping):
            _validate_fallback_evidence(override, assignment, errors, expected_policy=expected_policy)
    if status == "OVERRIDE_EVIDENCED" and isinstance(override, Mapping):
        _validate_explicit_fallback_evidence(override, assignment, errors, expected_policy=expected_policy)
    if status == "HOST_INHERITED" and assignment.get("resolvedModel") is None:
        errors.append("assignment: host-inherited status requires resolved model")
    role = assignment.get("role")
    if role == "worker":
        if status == "SELECTED":
            if assignment.get("desiredModel") is None:
                errors.append("assignment.desiredModel: selected workers require a desired model")
            if assignment.get("resolvedModel") != assignment.get("desiredModel"):
                errors.append("assignment.resolvedModel: selected workers cannot silently escalate from the desired model")
        if status == "OVERRIDE_EVIDENCED" and isinstance(override, Mapping):
            if assignment.get("resolvedModel") != override.get("model"):
                errors.append("assignment.resolvedModel: does not match approved override")
        if status == "FALLBACK_EVIDENCED":
            if assignment.get("desiredModel") is None or assignment.get("resolvedModel") == assignment.get("desiredModel"):
                errors.append("assignment: fallback workers must transition from the unavailable desired model")
    elif role == "normal-coordinator" and status == "SELECTED":
        if assignment.get("resolvedModel") != "model-balanced":
            errors.append("assignment.resolvedModel: normal coordinators must use BalancedModel")
    elif role in {"high-risk-coordinator", "final-audit"} and status == "SELECTED":
        if assignment.get("resolvedModel") != "model-advanced":
            errors.append("assignment.resolvedModel: high-risk coordinators and audits must use AdvancedModel")

    if expected_policy is not None:
        policy_result = validate_policy(expected_policy)
        if not policy_result["valid"]:
            errors.extend(f"expectedPolicy: {error}" for error in policy_result["errors"])
        elif assignment.get("policyFingerprint") != policy_result["policyFingerprint"]:
            errors.append("assignment.policyFingerprint: does not match expected policy")
        elif not errors:
            _validate_assignment_model_policy(assignment, expected_policy, errors)
        issued_policy = _timestamp(assignment.get("issuedAt"), "assignment.issuedAt", [])
        expires_policy = _timestamp(assignment.get("expiresAt"), "assignment.expiresAt", [])
        if issued_policy is not None and expires_policy is not None:
            lifetime = (expires_policy - issued_policy).total_seconds()
            maximum_lifetime = expected_policy.get("expirySemantics", {}).get("maxLifetimeSeconds")
            if isinstance(maximum_lifetime, int) and lifetime > maximum_lifetime:
                errors.append("assignment: expiry exceeds policy maxLifetimeSeconds")
    if expected_policy_fingerprint is not None and assignment.get("policyFingerprint") != expected_policy_fingerprint:
        errors.append("assignment.policyFingerprint: does not match expected fingerprint")
    if expected_binding is not None:
        aliases = {"host": "hostId", "sourceOrDiffFingerprint": "sourceFingerprint"}
        for binding_key in (
            "taskId",
            "projectId",
            "repositoryId",
            "tenantId",
            "packetVersion",
            "planVersion",
            "waveId",
            "attempt",
            "sourceFingerprint",
            "policyFingerprint",
            "hostId",
        ):
            expected_key = binding_key
            if expected_key not in expected_binding and binding_key in aliases:
                expected_key = aliases[binding_key]
            if expected_key in expected_binding and assignment.get(binding_key) != expected_binding.get(expected_key):
                errors.append(f"assignment.{binding_key}: does not match binding")
        if "taskClass" in expected_binding:
            task_summary = assignment.get("task")
            assignment_task_class = task_summary.get("taskClass") if isinstance(task_summary, Mapping) else None
            if assignment_task_class != expected_binding.get("taskClass"):
                errors.append("assignment.task.taskClass: does not match binding")
    return {"valid": not errors, "errors": errors}


def assignment_is_current(
    assignment: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Return a fail-closed freshness decision for a bound assignment."""

    result = validate_assignment(assignment, expected_binding=binding, now=now)
    if result["valid"]:
        return {"valid": True, "status": "CURRENT", "errors": []}
    return {"valid": False, "status": "INVALIDATED", "errors": result["errors"]}


def explain_selection(fixture: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Explain a fixture using only redacted, deterministic decision data."""

    if not isinstance(fixture, Mapping):
        raise ModelSelectionError("fixture: expected object")
    task_facts = fixture.get("taskFacts", fixture)
    host_catalog = fixture.get("hostCatalog")
    result = select_model_assignments(task_facts, host_catalog, policy)
    return _redact_decision(result)


def _redact_decision(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact_decision(item)
            for key, item in value.items()
            if key.lower() not in {"rawprompt", "prompt", "transcript", "memory", "agentoutput"}
        }
    if isinstance(value, list):
        return [_redact_decision(item) for item in value]
    return value


# Compatibility aliases keep the contract discoverable to host adapters while
# retaining one implementation and one source of truth.
validate_model_selection_policy = validate_policy
validate_model_assignment = validate_assignment
select_model = select_model_assignments


__all__ = [
    "ASSIGNMENT_SCHEMA_VERSION",
    "ASSIGNMENT_DISPATCH_BINDING_KEYS",
    "ASSIGNMENT_STATUSES",
    "DECISION_SCHEMA_VERSION",
    "FAST_DAILY_BYPASSED_CONTROLS",
    "FAST_DAILY_MODEL_RULES",
    "FAST_DAILY_SELECTION_SCHEMA_VERSION",
    "HOST_CATALOG_SCHEMA_VERSION",
    "SELECTION_BINDING_KEYS",
    "MODEL_SELECTION_SCHEMA_VERSION",
    "ModelSelectionError",
    "assignment_is_current",
    "canonical_json",
    "explain_selection",
    "policy_fingerprint",
    "select_fast_daily_model",
    "select_model",
    "select_model_assignments",
    "validate_assignment",
    "validate_fast_daily_model_selection",
    "validate_host_catalog",
    "validate_model_assignment",
    "validate_model_selection_policy",
    "validate_policy",
    "validate_task_facts",
]
