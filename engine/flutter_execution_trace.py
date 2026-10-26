#!/usr/bin/env python3
"""Static Flutter execution-chain evidence contracts.

This module never invokes Flutter, Gradle, Xcode, an emulator, or a device.  A
valid trace proves only bounded source/evidence coverage for the declared ten
stages; runtime evidence remains a separate host-owned artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping


SCHEMA_VERSION = "flutter-execution-trace-v1"
APPLICABILITY_POLICY_VERSION = "flutter-trace-applicability-v1"

STAGE_ORDER = (
    "entry-ui",
    "state",
    "repository-client",
    "local-storage",
    "queue-sync",
    "lifecycle",
    "native-boundary",
    "rendered-result",
    "backend-contract",
    "regression",
)

STAGE_STATUSES = {"PASS", "FAIL", "BLOCKED", "MISSING_COVERAGE", "NOT_APPLICABLE"}
APPLICABILITY = {"AFFECTED", "PRESERVED", "NOT_APPLICABLE"}
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

RISK_STAGE_MAP = {
    "auth": {"repository-client", "local-storage", "backend-contract", "regression"},
    "authentication": {"repository-client", "local-storage", "backend-contract", "regression"},
    "authorization": {"repository-client", "backend-contract", "regression"},
    "credential": {"local-storage", "repository-client", "backend-contract", "regression"},
    "data loss": {"local-storage", "queue-sync", "lifecycle", "regression"},
    "permission": {"native-boundary", "lifecycle", "regression"},
    "privacy": {"local-storage", "repository-client", "backend-contract", "regression"},
    "production": {"lifecycle", "native-boundary", "backend-contract", "regression"},
    "release": {"lifecycle", "native-boundary", "rendered-result", "regression"},
    "secret": {"local-storage", "repository-client", "backend-contract", "regression"},
    "security": {"local-storage", "repository-client", "native-boundary", "backend-contract", "regression"},
    "token": {"local-storage", "repository-client", "backend-contract", "regression"},
    "tenant": {"repository-client", "backend-contract", "regression"},
    "background foreground": {"local-storage", "queue-sync", "lifecycle", "rendered-result", "regression"},
    "background fetch": {"repository-client", "queue-sync", "lifecycle", "native-boundary", "regression"},
    "background processing": {"local-storage", "queue-sync", "lifecycle", "native-boundary", "regression"},
    "background service": {"queue-sync", "lifecycle", "native-boundary", "regression"},
    "background sync": {"local-storage", "queue-sync", "lifecycle", "backend-contract", "regression"},
    "background task": {"queue-sync", "lifecycle", "native-boundary", "regression"},
    "conflict": {"state", "local-storage", "queue-sync", "backend-contract", "regression"},
    "contract": {"repository-client", "backend-contract", "regression"},
    "cross boundary": {"repository-client", "native-boundary", "backend-contract", "regression"},
    "cross platform": {"entry-ui", "repository-client", "native-boundary", "rendered-result", "regression"},
    "device boundary": {"native-boundary", "lifecycle", "regression"},
    "device lifecycle": {"local-storage", "queue-sync", "lifecycle", "regression"},
    "device permission": {"native-boundary", "lifecycle", "regression"},
    "device runtime": {"entry-ui", "lifecycle", "native-boundary", "rendered-result", "regression"},
    "device specific": {"native-boundary", "rendered-result", "regression"},
    "device storage": {"local-storage", "queue-sync", "lifecycle", "regression"},
    "foreground lifecycle": {"state", "local-storage", "queue-sync", "lifecycle", "rendered-result", "regression"},
    "foreground transition": {"state", "lifecycle", "rendered-result", "regression"},
    "lifecycle": {"state", "local-storage", "queue-sync", "lifecycle", "rendered-result", "regression"},
    "migration": {"repository-client", "local-storage", "backend-contract", "regression"},
    "native api": {"repository-client", "native-boundary", "backend-contract", "regression"},
    "native boundary": {"lifecycle", "native-boundary", "regression"},
    "native code": {"native-boundary", "regression"},
    "native integration": {"repository-client", "lifecycle", "native-boundary", "regression"},
    "native lifecycle": {"lifecycle", "native-boundary", "regression"},
    "native module": {"lifecycle", "native-boundary", "regression"},
    "offline": {"repository-client", "local-storage", "queue-sync", "lifecycle", "backend-contract", "regression"},
    "platform channel": {"repository-client", "native-boundary", "regression"},
    "persistence": {"repository-client", "local-storage", "queue-sync", "lifecycle", "backend-contract", "regression"},
    "queue": {"repository-client", "local-storage", "queue-sync", "lifecycle", "backend-contract", "regression"},
    "reconnect": {"repository-client", "queue-sync", "lifecycle", "backend-contract", "regression"},
    "retry": {"repository-client", "queue-sync", "backend-contract", "regression"},
    "shared boundary": {"repository-client", "native-boundary", "backend-contract", "regression"},
    "sync": {"repository-client", "local-storage", "queue-sync", "lifecycle", "backend-contract", "regression"},
}

BOUNDARY_STAGE_MAP = {
    **{stage: {stage} for stage in STAGE_ORDER},
    "storage": {"local-storage"},
    "sync": {"queue-sync"},
    "native": {"native-boundary"},
    "backend": {"backend-contract"},
    "rendering": {"rendered-result"},
}

TRACE_FIELDS = {
    "schemaVersion",
    "traceId",
    "taskId",
    "repositoryId",
    "sourceFingerprint",
    "overallDiffFingerprint",
    "route",
    "policyVersion",
    "policyFingerprint",
    "riskSignals",
    "required",
    "stages",
    "overallStatus",
    "runtimeEvidenceStatus",
    "generatedAt",
}
STAGE_FIELDS = {
    "stageId",
    "applicability",
    "status",
    "paths",
    "symbols",
    "evidenceRefs",
    "residualRisk",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def applicability_policy_fingerprint() -> str:
    normalized = {signal: sorted(stages) for signal, stages in sorted(RISK_STAGE_MAP.items())}
    return "sha256:" + hashlib.sha256(
        f"{APPLICABILITY_POLICY_VERSION}\0{_canonical_json(normalized)}".encode("utf-8")
    ).hexdigest()


def derive_trace_requirement(mobile_decision: Mapping[str, Any]) -> dict[str, Any]:
    platforms = mobile_decision.get("platforms", []) if isinstance(mobile_decision, Mapping) else []
    flutter = isinstance(platforms, list) and "flutter" in platforms
    decision = mobile_decision.get("decision") if isinstance(mobile_decision, Mapping) else None
    required = flutter and decision in {"FULL_SAFETY", "RUNTIME_VALIDATION"}
    return {
        "required": required,
        "reason": (
            "Flutter full-safety/runtime route requires ten-stage static evidence"
            if required
            else "Flutter FAST_DAILY presentation work does not require the full trace"
            if flutter
            else "No Flutter lane is present"
        ),
    }


def derive_required_stages(
    mobile_decision: Mapping[str, Any],
    trusted_change_facts: Mapping[str, Any],
) -> dict[str, Any]:
    requirement = derive_trace_requirement(mobile_decision)
    fact_errors: list[str] = []

    def trusted_strings(container: Any, key: str) -> list[str]:
        value = container.get(key, []) if isinstance(container, Mapping) else []
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            fact_errors.append(f"trustedChangeFacts.{key}: expected string array")
            return []
        return value

    supplied = trusted_strings(trusted_change_facts, "riskSignals")
    decision_signals = trusted_strings(mobile_decision, "matchedRiskSignals")
    changed_paths = trusted_strings(trusted_change_facts, "changedPaths")
    symbols = trusted_strings(trusted_change_facts, "symbols")
    boundaries = trusted_strings(trusted_change_facts, "boundaries")
    signals = sorted(set(supplied) | set(decision_signals))
    required: set[str] = set()
    unknown: list[str] = []
    for signal in signals:
        stages = RISK_STAGE_MAP.get(signal)
        if stages is None:
            unknown.append(signal)
        else:
            required.update(stages)
    unknown_boundaries: list[str] = []
    for boundary in sorted(set(boundaries)):
        stages = BOUNDARY_STAGE_MAP.get(boundary)
        if stages is None:
            unknown_boundaries.append(boundary)
        else:
            required.update(stages)
    decision = mobile_decision.get("decision") if isinstance(mobile_decision, Mapping) else None
    if requirement["required"] and not signals and decision == "RUNTIME_VALIDATION":
        required.update({"entry-ui", "state", "repository-client", "lifecycle", "native-boundary", "rendered-result", "regression"})
    return {
        "required": requirement["required"],
        "requiredStages": [stage for stage in STAGE_ORDER if stage in required],
        "unknownSignals": unknown,
        "unknownBoundaries": unknown_boundaries,
        "riskSignals": signals,
        "changedPaths": sorted(set(changed_paths)),
        "symbols": sorted(set(symbols)),
        "boundaries": sorted(set(boundaries)),
        "trustedFactErrors": fact_errors,
        "mappingMissing": requirement["required"] and not required,
        "policyVersion": APPLICABILITY_POLICY_VERSION,
        "policyFingerprint": applicability_policy_fingerprint(),
    }


def derive_trace_status(stages: list[Mapping[str, Any]], *, required: bool) -> str:
    statuses = [stage.get("status") for stage in stages if isinstance(stage, Mapping)]
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "MISSING_COVERAGE" in statuses:
        return "MISSING_COVERAGE"
    affected = [stage for stage in stages if isinstance(stage, Mapping) and stage.get("applicability") == "AFFECTED"]
    if required and not affected:
        return "MISSING_COVERAGE"
    if any(stage.get("status") != "PASS" for stage in affected):
        return "MISSING_COVERAGE"
    return "PASS"


def _strings(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return []
    if any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{label}: expected non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values")
    return list(value)


def _safe_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return bool(path) and "\\" not in path and not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def validate_flutter_execution_trace(
    value: Any,
    mobile_decision: Mapping[str, Any],
    trusted_change_facts: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "derivedStatus": "MISSING_COVERAGE", "errors": ["flutterTrace: expected object"]}
    errors.extend(f"flutterTrace: missing required field {key}" for key in sorted(TRACE_FIELDS - set(value)))
    errors.extend(f"flutterTrace: unknown field {key}" for key in sorted(set(value) - TRACE_FIELDS))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"flutterTrace.schemaVersion: expected {SCHEMA_VERSION}")
    for key in ("traceId", "taskId", "repositoryId", "sourceFingerprint", "route", "policyVersion", "policyFingerprint", "overallStatus", "runtimeEvidenceStatus", "generatedAt"):
        if not isinstance(value.get(key), str) or not value.get(key):
            errors.append(f"flutterTrace.{key}: expected non-empty string")
    if not isinstance(value.get("overallDiffFingerprint"), str) or not FINGERPRINT_RE.fullmatch(value["overallDiffFingerprint"]):
        errors.append("flutterTrace.overallDiffFingerprint: expected sha256 fingerprint")
    if not isinstance(value.get("required"), bool):
        errors.append("flutterTrace.required: expected boolean")
    expected = derive_required_stages(mobile_decision, trusted_change_facts)
    if value.get("required") != expected["required"]:
        errors.append("flutterTrace.required: does not match controller route")
    if value.get("route") != mobile_decision.get("decision"):
        errors.append("flutterTrace.route: does not match mobile decision")
    if value.get("policyVersion") != APPLICABILITY_POLICY_VERSION:
        errors.append("flutterTrace.policyVersion: unsupported applicability policy")
    if value.get("policyFingerprint") != applicability_policy_fingerprint():
        errors.append("flutterTrace.policyFingerprint: does not match applicability policy")
    risk_signals = _strings(value.get("riskSignals"), "flutterTrace.riskSignals", errors)
    if risk_signals != expected["riskSignals"]:
        errors.append("flutterTrace.riskSignals: does not match trusted route/change facts")
    if expected["unknownSignals"]:
        errors.append("flutterTrace.riskSignals: one or more trusted signals are unmapped")
    if expected["unknownBoundaries"]:
        errors.append("flutterTrace: one or more trusted boundaries are unmapped")
    errors.extend(expected["trustedFactErrors"])
    if any(not _safe_path(path) for path in expected["changedPaths"]):
        errors.append("trustedChangeFacts.changedPaths: unsafe relative path")
    if expected["mappingMissing"]:
        errors.append("flutterTrace: required route has no trusted stage mapping")

    raw_stages = value.get("stages")
    if not isinstance(raw_stages, list):
        errors.append("flutterTrace.stages: expected array")
        raw_stages = []
    actual_order = [stage.get("stageId") if isinstance(stage, Mapping) else None for stage in raw_stages]
    if actual_order != list(STAGE_ORDER):
        errors.append("flutterTrace.stages: every required stage must appear exactly once in canonical order")
    required_stages = set(expected["requiredStages"])
    evidence_gap = False
    for index, raw_stage in enumerate(raw_stages):
        label = f"flutterTrace.stages[{index}]"
        if not isinstance(raw_stage, Mapping):
            errors.append(f"{label}: expected object")
            continue
        errors.extend(f"{label}: missing required field {key}" for key in sorted(STAGE_FIELDS - set(raw_stage)))
        errors.extend(f"{label}: unknown field {key}" for key in sorted(set(raw_stage) - STAGE_FIELDS))
        stage_id = raw_stage.get("stageId")
        if stage_id not in STAGE_ORDER:
            errors.append(f"{label}.stageId: unsupported stage")
        applicability = raw_stage.get("applicability")
        status = raw_stage.get("status")
        if applicability not in APPLICABILITY:
            errors.append(f"{label}.applicability: unsupported value")
        if status not in STAGE_STATUSES:
            errors.append(f"{label}.status: unsupported value")
        paths = _strings(raw_stage.get("paths"), f"{label}.paths", errors)
        if any(not _safe_path(path) for path in paths):
            errors.append(f"{label}.paths: unsafe relative path")
        stage_symbols = _strings(raw_stage.get("symbols"), f"{label}.symbols", errors)
        evidence = _strings(raw_stage.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
        _strings(raw_stage.get("residualRisk"), f"{label}.residualRisk", errors)
        if stage_id in required_stages and applicability != "AFFECTED":
            errors.append(f"{label}.applicability: trusted risk signal requires AFFECTED")
            evidence_gap = True
        if applicability == "AFFECTED":
            if status == "NOT_APPLICABLE":
                errors.append(f"{label}.status: affected stage cannot be NOT_APPLICABLE")
                evidence_gap = True
            if not evidence:
                errors.append(f"{label}.evidenceRefs: affected stage requires evidence")
                evidence_gap = True
            trusted_path_match = bool(set(paths) & set(expected["changedPaths"]))
            trusted_symbol_match = bool(set(stage_symbols) & set(expected["symbols"]))
            if not trusted_path_match and not trusted_symbol_match:
                errors.append(
                    f"{label}: affected stage is not bound to trusted changed paths or symbols"
                )
                evidence_gap = True
        if applicability == "NOT_APPLICABLE" and status != "NOT_APPLICABLE":
            errors.append(f"{label}.status: NOT_APPLICABLE stage must use NOT_APPLICABLE status")
        if applicability != "NOT_APPLICABLE" and status == "NOT_APPLICABLE":
            errors.append(f"{label}.status: applicable stage cannot use NOT_APPLICABLE")
    derived = derive_trace_status(raw_stages, required=expected["required"])
    if (evidence_gap or expected["mappingMissing"]) and derived == "PASS":
        derived = "MISSING_COVERAGE"
    if value.get("overallStatus") != derived:
        errors.append("flutterTrace.overallStatus: does not match derived status")
    if value.get("runtimeEvidenceStatus") != "NOT_VERIFIED":
        errors.append("flutterTrace.runtimeEvidenceStatus: static trace cannot claim runtime evidence")
    _parse_errors: list[str] = []
    _timestamp = value.get("generatedAt")
    if isinstance(_timestamp, str):
        try:
            parsed = datetime.fromisoformat(_timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                _parse_errors.append("timezone is required")
        except ValueError:
            _parse_errors.append("invalid timestamp")
    if _parse_errors:
        errors.append("flutterTrace.generatedAt: expected timezone-aware ISO-8601 timestamp")
    return {
        "valid": not errors,
        "derivedStatus": derived,
        "requiredStages": expected["requiredStages"],
        "runtimeEvidenceStatus": "NOT_VERIFIED",
        "errors": errors,
    }
