"""Fast, deterministic routing for routine mobile development.

This module is deliberately source-only.  It classifies the request before
project-wide discovery so a small screen/state change can use the bounded V1
workflow, while offline, lifecycle, persistence, security, and runtime work
keeps the stronger V2/V3 protections.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "mobile-workflow-decision-v1"

PLATFORM_ALIASES = {
    "android": "mobile",
    "android native": "mobile",
    "expo": "mobile",
    "flutter": "flutter",
    "ios": "mobile",
    "ios native": "mobile",
    "mobile": "mobile",
    "mobile app": "mobile",
    "react native": "mobile",
    "react-native": "mobile",
    "reactnative": "mobile",
    "rn": "mobile",
}

HIGH_RISK_TERMS = (
    "auth",
    "authentication",
    "authorization",
    "credential",
    "data loss",
    "permission",
    "privacy",
    "production",
    "release",
    "secret",
    "security",
    "token",
    "tenant",
)

COUPLED_TERMS = (
    "background foreground",
    "background fetch",
    "background processing",
    "background service",
    "background sync",
    "background task",
    "conflict",
    "contract",
    "cross boundary",
    "cross platform",
    "device boundary",
    "device lifecycle",
    "device permission",
    "device runtime",
    "device specific",
    "device storage",
    "foreground lifecycle",
    "foreground transition",
    "lifecycle",
    "migration",
    "native api",
    "native boundary",
    "native code",
    "native integration",
    "native lifecycle",
    "native module",
    "offline",
    "platform channel",
    "persistence",
    "reconnect",
    "shared boundary",
)

# These terms are only coupled when their surrounding request indicates a
# network, persistence, or state-boundary concern.  A bare "retry button",
# "sync icon", or "queue badge" is ordinary UI work and belongs on the fast
# path; a retry/sync/queue implementation still stays on the full route.
CONTEXTUAL_COUPLED_TERMS = {
    "queue": (
        "background",
        "conflict",
        "drain",
        "offline",
        "persistence",
        "processing",
        "replay",
        "retry",
        "sync",
        "worker",
    ),
    "retry": (
        "backoff",
        "failed",
        "failure",
        "network",
        "offline",
        "policy",
        "queue",
        "reconnect",
        "request",
        "logic",
        "sync",
    ),
    "sync": (
        "background",
        "conflict",
        "data",
        "engine",
        "logic",
        "network",
        "offline",
        "persistence",
        "queue",
        "reconnect",
        "repository",
        "service",
    ),
}

RUNTIME_TERMS = (
    "adb",
    "emulator",
    "on device",
    "real device",
    "simulator",
)

RUNTIME_TEST_TERMS = ("integration test", "widget test")
RUNTIME_ACTION_TERMS = ("execute", "exercise", "launch", "run", "validate", "verify")
MUTATION_TERMS = (
    "add",
    "change",
    "create",
    "delete",
    "fix",
    "implement",
    "migrate",
    "modify",
    "refactor",
    "remove",
    "rename",
    "repair",
    "replace",
    "update",
    "write",
)
NON_MUTATING_REQUEST_TYPES = {
    "audit",
    "direct",
    "direct validation",
    "explain",
    "lookup",
    "question",
    "read only",
    "review",
    "review only",
    "runtime validation",
    "status",
    "validation",
}

HIGH_RISK_CLASSES = {"critical", "high", "high risk", "security"}
COUPLED_CLASSES = {"architecture", "coupled", "cross repository", "difficult root cause"}
CLASS_PRIORITY = {
    "lightweight": 0,
    "small": 1,
    "normal": 2,
    "architecture": 3,
    "coupled": 3,
    "cross-repository": 4,
    "difficult-root-cause": 4,
    "high-risk": 5,
    "security": 5,
}

FAST_DAILY_MODEL_RULES = {
    "lightweight": {"model": "model-fast", "reasoningEffort": "low"},
    "small": {"model": "model-fast", "reasoningEffort": "medium"},
    "normal": {"model": "model-balanced", "reasoningEffort": "high"},
}
FAST_DAILY_BYPASSED_CONTROLS = [
    "taskFacts",
    "sourceFingerprint",
    "policyFingerprint",
    "hostCatalog",
    "expiry",
    "coordinatorScope",
    "fallbackRules",
]
MOBILE_MODEL_ROUTING_SCHEMA_VERSION = "mobile-model-routing-v1"


def _normalise(value: Any) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ").replace("/", " ")
    return re.sub(r"\s+", " ", text).strip()


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _platforms(intake: Mapping[str, Any]) -> list[str]:
    raw: list[str] = []
    for key in (
        "platform",
        "platforms",
        "target",
        "targets",
        "projectType",
        "projectTypes",
        "domain",
        "framework",
        "stack",
    ):
        raw.extend(_values(intake.get(key)))
    result: list[str] = []
    for value in raw:
        normalised = _normalise(value)
        mapped = PLATFORM_ALIASES.get(normalised)
        if mapped and mapped not in result:
            result.append(mapped)
    return result


def _intake_text(intake: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "requestType",
        "taskType",
        "normalizedObjective",
        "objective",
        "acceptanceCriterion",
        "acceptanceCriteria",
        "riskTier",
        "taskClass",
        "complexity",
    ):
        values.extend(_values(intake.get(key)))
    boolean_signal_text = {
        "auth": "auth",
        "authentication": "authentication",
        "crossRepository": "cross repository",
        "device": "device boundary",
        "lifecycle": "lifecycle",
        "native": "native boundary",
        "offline": "offline",
        "persistence": "persistence",
        "queue": "queue",
        "security": "security",
        "sharedBoundary": "shared boundary",
        "sync": "sync",
        "token": "token",
    }
    values.extend(signal for key, signal in boolean_signal_text.items() if intake.get(key) is True)
    return _normalise(" ".join(values))


def _matching_terms(text: str, terms: Iterable[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        normalised = _normalise(term)
        pattern = rf"(?<!\w){re.escape(normalised)}(?!\w)"
        if re.search(pattern, text) and normalised not in matches:
            matches.append(normalised)
    return matches


def _matching_coupled_terms(text: str) -> list[str]:
    matches = _matching_terms(text, COUPLED_TERMS)
    for term, contexts in CONTEXTUAL_COUPLED_TERMS.items():
        if not _matching_terms(text, (term,)):
            continue
        if any(_matching_terms(text, (context,)) for context in contexts):
            matches.append(term)
    return sorted(set(matches))


def _requested_class(intake: Mapping[str, Any], text: str) -> str | None:
    candidates: list[str] = []
    for key in ("taskClass", "complexity", "riskTier"):
        value = _normalise(intake.get(key))
        if value in HIGH_RISK_CLASSES:
            candidates.append("security" if value == "security" else "high-risk")
        elif value in COUPLED_CLASSES:
            candidates.append({
                "cross repository": "cross-repository",
                "difficult root cause": "difficult-root-cause",
            }.get(value, value))
        elif value in {"normal", "small", "lightweight"}:
            candidates.append(value)
        elif value == "medium":
            candidates.append("normal")
        elif value == "low":
            candidates.append("small")

    boolean_class_keys = {
        "architecture": "architecture",
        "coupled": "coupled",
        "crossRepository": "cross-repository",
        "difficultRootCause": "difficult-root-cause",
        "highRisk": "high-risk",
        "security": "security",
    }
    candidates.extend(mapped for key, mapped in boolean_class_keys.items() if intake.get(key) is True)
    if "high risk" in text or "critical" in text:
        candidates.append("high-risk")
    if "coupled" in text or "cross repository" in text or "shared boundary" in text or "cross boundary" in text:
        candidates.append("coupled")
    return max(candidates, key=lambda item: CLASS_PRIORITY[item]) if candidates else None


def _runtime_requested(intake: Mapping[str, Any], text: str) -> bool:
    if intake.get("runtimeValidation") is True or intake.get("runtime_validation") is True:
        return True
    if _matching_terms(text, RUNTIME_TERMS):
        return True
    return bool(_matching_terms(text, RUNTIME_TEST_TERMS) and _matching_terms(text, RUNTIME_ACTION_TERMS))


def _mutation_requested(intake: Mapping[str, Any], text: str, runtime_requested: bool) -> bool:
    for key in ("mutationRequested", "mutation_requested", "sourceMutation", "source_mutation", "codeChange", "code_change"):
        if isinstance(intake.get(key), bool):
            return bool(intake[key])
    if _matching_terms(text, MUTATION_TERMS):
        return True
    request_type = _normalise(intake.get("requestType") or intake.get("taskType"))
    if request_type in NON_MUTATING_REQUEST_TYPES:
        return False
    if runtime_requested:
        return False
    return True


def _delegation_requested(intake: Mapping[str, Any]) -> bool:
    for key in ("requestedRoles", "requested_roles", "workerScopes", "worker_scopes", "requestedWorkers", "requested_workers"):
        value = intake.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    for key in ("needsCoordinator", "needs_coordinator", "delegate", "delegation", "useSubagents", "use_subagents", "nativeExecution", "native_execution"):
        if intake.get(key) is True:
            return True
    return False


def classify_mobile_workflow(intake: Mapping[str, Any]) -> dict[str, Any]:
    """Return the V1-compatible mobile route and model recommendation.

    The function does not inspect the repository, start a device, create an
    agent, or claim runtime evidence.  The caller may use the returned route
    to decide whether the heavier V2/V3 sidecars are relevant.
    """

    platforms = _platforms(intake)
    text = _intake_text(intake)
    high_risk = _matching_terms(text, HIGH_RISK_TERMS)
    coupled = _matching_coupled_terms(text)
    requested_class = _requested_class(intake, text)
    runtime_requested = _runtime_requested(intake, text)
    mutation_requested = _mutation_requested(intake, text, runtime_requested)
    delegation_requested = _delegation_requested(intake)
    is_mobile = bool(platforms)

    if not is_mobile:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mobile": False,
            "platforms": [],
            "decision": "NOT_MOBILE",
            "taskClass": None,
            "model": None,
            "reasoningEffort": None,
            "coordinator": None,
            "workerStrategy": "lead_only",
            "leadOnlyFastPath": False,
            "modelSelectionMode": "NONE",
            "selectionBinding": "NONE",
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "runtimeRequested": False,
            "mutationRequested": False,
            "matchedRiskSignals": [],
            "skippedGates": [],
            "requiredGates": [],
            "rationale": "No supported mobile or Flutter platform was declared.",
        }

    if high_risk or requested_class in {"high-risk", "security"}:
        task_class = "security" if requested_class == "security" else "high-risk"
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mobile": True,
            "platforms": platforms,
            "decision": "FULL_SAFETY",
            "taskClass": task_class,
            "model": "model-advanced",
            "reasoningEffort": "max",
            "coordinator": "high-risk-coordinator",
            "workerStrategy": "bounded_named_agents",
            "leadOnlyFastPath": False,
            "modelSelectionMode": "STRICT",
            "selectionBinding": "TASK_POLICY_HOST",
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "runtimeRequested": runtime_requested,
            "mutationRequested": mutation_requested,
            "matchedRiskSignals": sorted(set(high_risk + coupled)),
            "skippedGates": [],
            "requiredGates": [
                "project_context",
                *(["bounded_reuse_assessment"] if mutation_requested else []),
                "minimal_change",
                "focused_tests",
                "final_audit",
            ],
            "rationale": "Security, authorization, privacy, release, production, or data-loss signals keep the strongest safety gates.",
        }

    if coupled or requested_class in {"architecture", "coupled", "cross-repository", "difficult-root-cause"}:
        task_class = requested_class if requested_class in {"architecture", "coupled", "cross-repository", "difficult-root-cause"} else "coupled"
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mobile": True,
            "platforms": platforms,
            "decision": "FULL_SAFETY",
            "taskClass": task_class,
            "model": "model-advanced",
            "reasoningEffort": "max",
            "coordinator": "high-risk-coordinator",
            "workerStrategy": "bounded_named_agents",
            "leadOnlyFastPath": False,
            "modelSelectionMode": "STRICT",
            "selectionBinding": "TASK_POLICY_HOST",
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "runtimeRequested": runtime_requested,
            "mutationRequested": mutation_requested,
            "matchedRiskSignals": sorted(set(coupled)),
            "skippedGates": [],
            "requiredGates": [
                "project_context",
                *(["bounded_reuse_assessment"] if mutation_requested else []),
                "minimal_change",
                "focused_tests",
                "final_audit",
            ],
            "rationale": "Offline, sync, queue, persistence, lifecycle, native, device, or cross-boundary work can lose state or cross runtime boundaries.",
        }

    if runtime_requested:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mobile": True,
            "platforms": platforms,
            "decision": "RUNTIME_VALIDATION",
            "taskClass": "normal" if requested_class not in {"small", "lightweight"} else requested_class,
            "model": "model-balanced",
            "reasoningEffort": "high",
            "coordinator": "normal-coordinator",
            "workerStrategy": "bounded_named_agents",
            "leadOnlyFastPath": False,
            "modelSelectionMode": "STRICT",
            "selectionBinding": "TASK_POLICY_HOST",
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "runtimeRequested": True,
            "mutationRequested": mutation_requested,
            "matchedRiskSignals": [],
            "skippedGates": ["repo_wide_reusable_scan"],
            "requiredGates": [
                "targeted_context",
                *(["bounded_reuse_assessment"] if mutation_requested else []),
                "runtime_evidence",
                "focused_tests",
            ],
            "rationale": "Runtime validation is explicit and must use a configured, platform-correct host adapter.",
        }

    task_class = requested_class if requested_class in {"small", "lightweight", "normal"} else "small"
    model_rule = FAST_DAILY_MODEL_RULES[task_class]
    direct = not delegation_requested
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mobile": True,
        "platforms": platforms,
        "decision": "FAST_DAILY",
        "taskClass": task_class,
        "model": model_rule["model"],
        "reasoningEffort": model_rule["reasoningEffort"],
        "coordinator": None,
        "workerStrategy": "lead_only" if direct else "bounded_named_agents",
        "leadOnlyFastPath": direct,
        "modelSelectionMode": "FAST_DAILY_DIRECT" if direct else "STRICT",
        "selectionBinding": "SESSION_ONLY" if direct else "TASK_POLICY_HOST",
        "strictModelSelectionBypass": direct,
        "bypassedControls": list(FAST_DAILY_BYPASSED_CONTROLS) if direct else [],
        "runtimeRequested": False,
        "mutationRequested": mutation_requested,
        "matchedRiskSignals": [],
        "skippedGates": [
            "full_project_context_enumeration",
            "repo_wide_reusable_scan",
            "qa_execution_packet",
            "context_enforced_gate",
            "new_worktree_provisioning",
        ] if direct else [],
        "requiredGates": [
            "bounded_target_context",
            *(["bounded_reuse_assessment"] if mutation_requested else []),
            "minimal_change",
            "focused_tests",
        ],
        "rationale": (
            "Routine mobile source work uses the fast V1 flow; high-risk boundaries remain protected by the full workflow."
            if direct
            else "Routine work requested delegation, so strict model-selection bindings remain enabled."
        ),
    }


def validate_mobile_workflow_decision(value: Any) -> dict[str, Any]:
    """Validate the classifier's stable output without inspecting a repo."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["decision: expected object"]}
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"decision.schemaVersion: expected {SCHEMA_VERSION}")
    if value.get("decision") not in {"FAST_DAILY", "FULL_SAFETY", "RUNTIME_VALIDATION", "NOT_MOBILE"}:
        errors.append("decision.decision: unsupported value")
    if not isinstance(value.get("mobile"), bool):
        errors.append("decision.mobile: expected boolean")
    if not isinstance(value.get("mutationRequested"), bool):
        errors.append("decision.mutationRequested: expected boolean")
    if not isinstance(value.get("platforms"), list) or not all(isinstance(item, str) and item for item in value.get("platforms", [])):
        errors.append("decision.platforms: expected string array")
    model = value.get("model")
    if value.get("decision") == "NOT_MOBILE":
        if model is not None or value.get("mobile") is not False:
            errors.append("decision: NOT_MOBILE must not select a model")
    elif model not in {"model-fast", "model-balanced", "model-advanced"}:
        errors.append("decision.model: unsupported value")
    if value.get("decision") == "FULL_SAFETY" and model != "model-advanced":
        errors.append("decision: FULL_SAFETY must use AdvancedModel")
    if value.get("decision") == "RUNTIME_VALIDATION" and model != "model-balanced":
        errors.append("decision: RUNTIME_VALIDATION must use BalancedModel")
    if value.get("decision") == "FAST_DAILY":
        task_class = value.get("taskClass")
        expected = FAST_DAILY_MODEL_RULES.get(task_class) if isinstance(task_class, str) else None
        if expected is None:
            errors.append("decision.taskClass: FAST_DAILY requires lightweight, small, or normal")
        else:
            if model != expected["model"]:
                errors.append("decision.model: does not match FAST_DAILY task class")
            if value.get("reasoningEffort") != expected["reasoningEffort"]:
                errors.append("decision.reasoningEffort: does not match FAST_DAILY task class")
        if value.get("runtimeRequested") is not False:
            errors.append("decision.runtimeRequested: FAST_DAILY cannot claim runtime validation")
        if value.get("matchedRiskSignals"):
            errors.append("decision.matchedRiskSignals: FAST_DAILY cannot contain risk signals")
        if value.get("strictModelSelectionBypass") is True:
            if value.get("coordinator") is not None:
                errors.append("decision.coordinator: direct FAST_DAILY must be lead-only")
            if value.get("workerStrategy") != "lead_only":
                errors.append("decision.workerStrategy: direct FAST_DAILY must be lead-only")
            if value.get("leadOnlyFastPath") is not True:
                errors.append("decision.leadOnlyFastPath: direct FAST_DAILY must be explicitly enabled")
            if value.get("modelSelectionMode") != "FAST_DAILY_DIRECT":
                errors.append("decision.modelSelectionMode: direct FAST_DAILY must use direct selection")
            if value.get("selectionBinding") != "SESSION_ONLY":
                errors.append("decision.selectionBinding: direct FAST_DAILY must be session-only")
            if value.get("bypassedControls") != FAST_DAILY_BYPASSED_CONTROLS:
                errors.append("decision.bypassedControls: does not match the bounded fast-lane contract")
        else:
            if value.get("leadOnlyFastPath") is not False:
                errors.append("decision.leadOnlyFastPath: strict FAST_DAILY must not authorize the direct path")
            if value.get("modelSelectionMode") != "STRICT":
                errors.append("decision.modelSelectionMode: delegated FAST_DAILY must use strict selection")
            if value.get("selectionBinding") != "TASK_POLICY_HOST":
                errors.append("decision.selectionBinding: delegated FAST_DAILY must use strict bindings")
            if value.get("workerStrategy") != "bounded_named_agents":
                errors.append("decision.workerStrategy: delegated FAST_DAILY must use bounded agents")
            if value.get("bypassedControls") != []:
                errors.append("decision.bypassedControls: strict FAST_DAILY cannot bypass controls")
    for key in ("skippedGates", "requiredGates", "matchedRiskSignals"):
        if not isinstance(value.get(key), list) or not all(isinstance(item, str) and item for item in value.get(key, [])):
            errors.append(f"decision.{key}: expected string array")
    required_gates = value.get("requiredGates") if isinstance(value.get("requiredGates"), list) else []
    skipped_gates = value.get("skippedGates") if isinstance(value.get("skippedGates"), list) else []
    if value.get("mobile") is True and value.get("mutationRequested") is True:
        if "bounded_reuse_assessment" not in required_gates:
            errors.append("decision.requiredGates: source mutation requires bounded_reuse_assessment")
        if "bounded_reuse_assessment" in skipped_gates:
            errors.append("decision.skippedGates: source mutation cannot skip bounded_reuse_assessment")
    elif "bounded_reuse_assessment" in required_gates:
        errors.append("decision.requiredGates: non-mutating work must not require bounded_reuse_assessment")
    return {"valid": not errors, "errors": errors}


def resolve_mobile_model_selection(intake: Mapping[str, Any]) -> dict[str, Any]:
    """Classify mobile work and expose the controller's model-selection mode.

    ``FAST_DAILY`` is a lead-only, source-only recommendation and may use the
    V1-compatible direct model path.  A worker, coordinator, native action,
    or runtime request forces the strict selector back on.  This function is
    intentionally the only convenience entry point that accepts raw intake;
    ``select_fast_daily_model`` accepts only a validated classifier decision.
    """

    decision = classify_mobile_workflow(intake)
    decision_validation = validate_mobile_workflow_decision(decision)
    if not decision_validation["valid"]:
        return {
            "schemaVersion": MOBILE_MODEL_ROUTING_SCHEMA_VERSION,
            "decision": decision,
            "selection": {"status": "REJECTED", "errors": decision_validation["errors"]},
            "valid": False,
            "errors": decision_validation["errors"],
        }

    if decision["decision"] == "FAST_DAILY" and not _delegation_requested(intake):
        from .model_selection import select_fast_daily_model, validate_fast_daily_model_selection

        selection = select_fast_daily_model(decision)
        selection_validation = validate_fast_daily_model_selection(selection)
        return {
            "schemaVersion": MOBILE_MODEL_ROUTING_SCHEMA_VERSION,
            "decision": decision,
            "selection": selection,
            "valid": selection_validation["valid"],
            "errors": selection_validation["errors"],
        }

    if decision["decision"] == "NOT_MOBILE":
        selection = {
            "schemaVersion": MOBILE_MODEL_ROUTING_SCHEMA_VERSION,
            "status": "NOT_MOBILE",
            "route": "NOT_MOBILE",
            "model": None,
            "reasoningEffort": None,
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "rationale": "The mobile classifier did not find a supported mobile or Flutter target.",
        }
    else:
        reason = (
            "Delegation was requested, so the lead-only fast lane is not eligible."
            if decision["decision"] == "FAST_DAILY"
            else "This route requires strict task, host, expiry, scope, and fallback validation."
        )
        selection = {
            "schemaVersion": MOBILE_MODEL_ROUTING_SCHEMA_VERSION,
            "status": "STRICT_REQUIRED",
            "route": decision["decision"],
            "taskClass": decision["taskClass"],
            "model": decision["model"],
            "reasoningEffort": decision["reasoningEffort"],
            "coordinator": decision["coordinator"],
            "workerStrategy": decision["workerStrategy"],
            "strictModelSelectionBypass": False,
            "bypassedControls": [],
            "rationale": reason,
        }

    return {
        "schemaVersion": MOBILE_MODEL_ROUTING_SCHEMA_VERSION,
        "decision": decision,
        "selection": selection,
        "valid": True,
        "errors": [],
    }
