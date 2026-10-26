#!/usr/bin/env python3
"""Validate a declarative Harness capability manifest without executing it."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


CAPABILITY_IDS = {
    "repository-documentation",
    "lint-architecture",
    "ci-gates",
    "observability",
    "specialist-profiles",
    "quality-technical-debt",
}
FORBIDDEN_KEYS = {
    "route", "routing", "model", "modelselection", "lifecycleclosure",
    "stateroot", "stateroots", "credential", "credentials", "secret",
    "prompt", "prompts", "instructionurl", "callback", "callbacks",
    "command", "commands", "executable", "executablecontent", "shell", "script",
}
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
URL = re.compile(r"(?:https?://|ftp://|javascript:|data:)", re.I)
STATUS_VALUES = {
    "documented": {"DOCUMENTED", "UNAVAILABLE"},
    "installed": {"INSTALLED", "UNAVAILABLE"},
    "runnable": {"RUNNABLE", "UNAVAILABLE"},
    "executed": {"EXECUTED", "NOT_EXECUTED", "UNAVAILABLE"},
}
SCOPE_CLASSES = {"TASK_EVIDENCE", "REPOSITORY"}
PATH_CLASSES = {"documentation", "source", "tests", "ci", "observability", "evidence", "quality"}
TASK_CATEGORIES = {
    "documentation", "lint", "architecture", "ci", "observability",
    "specialist-selection", "quality", "technical-debt", "verification",
}
READ_WRITE_CLASSES = {"READ_ONLY", "REPOSITORY_DOCUMENTATION_WRITE"}
EVIDENCE_TYPES = {
    "catalog-fingerprint", "documentation-reference", "installed-package-check",
    "runnable-check-result", "executed-check-result", "source-diff", "test-result",
    "review-receipt", "runtime-observation", "unavailable-record",
}
FRESHNESS_VALUES = {"source-bound", "task-bound", "runtime-bound", "not-applicable"}


def _walk(value: Any, path: str = "manifest"):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), f"{path}.{key}", child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _errors_for_keys(value: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    errors = [f"{label}: missing required field {key}" for key in sorted(required - set(value))]
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - required))
    return errors


def _required_text(value: Any, label: str) -> list[str]:
    return [] if isinstance(value, str) and value.strip() else [f"{label}: expected non-empty text"]


def _safe_text(value: Any, label: str) -> list[str]:
    errors = _required_text(value, label)
    if isinstance(value, str) and value.strip():
        if len(value) > 500:
            errors.append(f"{label}: exceeds 500 characters")
        if URL.search(value):
            errors.append(f"{label}: remote instruction URL is forbidden")
    return errors


def _enum_list(value: Any, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{label}: expected non-empty array"]
    errors: list[str] = []
    if all(isinstance(item, str) for item in value) and len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    for index, item in enumerate(value):
        if item not in allowed:
            errors.append(f"{label}[{index}]: unsupported value")
    return errors


def _status_is_consistent(availability: Any, status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    if availability == "AVAILABLE":
        return (
            status.get("documented") == "DOCUMENTED"
            and status.get("installed") == "INSTALLED"
            and status.get("runnable") == "RUNNABLE"
            and status.get("executed") in {"EXECUTED", "NOT_EXECUTED"}
        )
    if availability == "UNAVAILABLE":
        return (
            status.get("documented") in {"DOCUMENTED", "UNAVAILABLE"}
            and status.get("installed") == "UNAVAILABLE"
            and status.get("runnable") == "UNAVAILABLE"
            and status.get("executed") == "UNAVAILABLE"
        )
    return True


def validate_manifest(manifest: Any, *, expected_fingerprints: Mapping[str, str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return {"valid": False, "eligible": False, "errors": ["manifest: expected object"]}

    errors.extend(_errors_for_keys(manifest, {"schemaVersion", "registry", "authority", "capabilities"}, "manifest"))
    if manifest.get("schemaVersion") != "capability-manifest-v1":
        errors.append("manifest.schemaVersion: expected capability-manifest-v1")

    registry = manifest.get("registry")
    registry_required = {"registryId", "scope", "canonical", "owner", "version", "fingerprint", "enabledByDefault"}
    if not isinstance(registry, dict):
        errors.append("manifest.registry: expected object")
    else:
        errors.extend(_errors_for_keys(registry, registry_required, "manifest.registry"))
        if registry.get("registryId") != "ae-host-capability-registry":
            errors.append("manifest.registry.registryId: unexpected registry")
        if registry.get("scope") != "host-workspace":
            errors.append("manifest.registry.scope: expected host-workspace")
        if registry.get("canonical") is not True or registry.get("owner") != "general":
            errors.append("manifest.registry: canonical registry must be AE-owned")
        if not SEMVER.fullmatch(str(registry.get("version", ""))):
            errors.append("manifest.registry.version: invalid semver")
        if not FINGERPRINT.fullmatch(str(registry.get("fingerprint", ""))):
            errors.append("manifest.registry.fingerprint: invalid sha256 fingerprint")
        if registry.get("enabledByDefault") is not False:
            errors.append("manifest.registry.enabledByDefault: must be false")

    authority = manifest.get("authority")
    authority_required = {
        "mode", "controller", "canRoute", "canSelectModel", "canGrantWrite",
        "canCloseLifecycle", "canCreateState", "canAccessSecrets",
        "canFetchInstructions", "canInvokeCallbacks", "canRunArbitraryCode",
    }
    if not isinstance(authority, dict):
        errors.append("manifest.authority: expected object")
    else:
        errors.extend(_errors_for_keys(authority, authority_required, "manifest.authority"))
        if authority.get("mode") != "NON_AUTHORITATIVE" or authority.get("controller") != "AE":
            errors.append("manifest.authority: expected non-authoritative AE controller")
        for key in authority_required - {"mode", "controller"}:
            if authority.get(key) is not False:
                errors.append(f"manifest.authority.{key}: must be false")

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("manifest.capabilities: expected non-empty array")
        capabilities = []
    seen: set[str] = set()
    required = {
        "capabilityId", "provider", "owner", "version", "fingerprint", "availability",
        "enabledByDefault", "status", "taskCategories", "readWriteClass", "scope",
        "evidenceRequirements", "purpose",
    }
    for index, capability in enumerate(capabilities):
        label = f"manifest.capabilities[{index}]"
        if not isinstance(capability, dict):
            errors.append(f"{label}: expected object")
            continue
        errors.extend(_errors_for_keys(capability, required, label))
        capability_id = capability.get("capabilityId")
        if capability_id not in CAPABILITY_IDS:
            errors.append(f"{label}.capabilityId: unsupported capability")
        elif capability_id in seen:
            errors.append(f"duplicate capability id: {capability_id}")
        else:
            seen.add(capability_id)
        if capability.get("provider") != "Harness" or capability.get("owner") != "Harness":
            errors.append(f"{label}: provider and owner must be Harness")
        if not SEMVER.fullmatch(str(capability.get("version", ""))):
            errors.append(f"{label}.version: invalid semver")
        if not FINGERPRINT.fullmatch(str(capability.get("fingerprint", ""))):
            errors.append(f"{label}.fingerprint: invalid sha256 fingerprint")
        if expected_fingerprints and capability_id in expected_fingerprints and capability.get("fingerprint") != expected_fingerprints[capability_id]:
            errors.append(f"stale capability fingerprint: {capability_id}")
        if capability.get("enabledByDefault") is not False:
            errors.append(f"{label}.enabledByDefault: must be false")
        if capability.get("availability") not in {"AVAILABLE", "UNAVAILABLE", "DISABLED", "STALE", "DUPLICATE", "INVALID"}:
            errors.append(f"{label}.availability: unsupported state")
        elif capability.get("availability") in {"DISABLED", "STALE", "DUPLICATE", "INVALID"}:
            errors.append(f"{label}.availability: non-eligible state must be rejected")
        status = capability.get("status")
        if not isinstance(status, dict) or set(status) != {"documented", "installed", "runnable", "executed"}:
            errors.append(f"{label}.status: expected documented/installed/runnable/executed")
        elif any(status[key] not in STATUS_VALUES[key] for key in STATUS_VALUES):
            errors.append(f"{label}.status: contains an unsupported lifecycle value")
        elif not _status_is_consistent(capability.get("availability"), status):
            errors.append(f"{label}: availability and documented/installed/runnable/executed status disagree")
        if capability.get("readWriteClass") not in READ_WRITE_CLASSES:
            errors.append(f"{label}.readWriteClass: unsupported value")
        scope = capability.get("scope")
        if not isinstance(scope, dict):
            errors.append(f"{label}.scope: expected object")
        else:
            scope_required = {"scopeClass", "maxRepositories", "maxPaths", "allowedPathClasses", "requiresAEGrant", "externalSideEffects"}
            errors.extend(_errors_for_keys(scope, scope_required, f"{label}.scope"))
            if scope.get("scopeClass") not in SCOPE_CLASSES:
                errors.append(f"{label}.scope.scopeClass: unsupported value")
            if not isinstance(scope.get("maxRepositories"), int) or isinstance(scope.get("maxRepositories"), bool) or not 0 <= scope.get("maxRepositories") <= 1:
                errors.append(f"{label}.scope.maxRepositories: expected integer from 0 to 1")
            if not isinstance(scope.get("maxPaths"), int) or isinstance(scope.get("maxPaths"), bool) or not 0 <= scope.get("maxPaths") <= 1000:
                errors.append(f"{label}.scope.maxPaths: expected integer from 0 to 1000")
            errors.extend(_enum_list(scope.get("allowedPathClasses"), PATH_CLASSES, f"{label}.scope.allowedPathClasses"))
            if scope.get("requiresAEGrant") is not True or scope.get("externalSideEffects") is not False:
                errors.append(f"{label}.scope: unsafe authority or side effects")
        errors.extend(_enum_list(capability.get("taskCategories"), TASK_CATEGORIES, f"{label}.taskCategories"))
        evidence_requirements = capability.get("evidenceRequirements")
        if not isinstance(evidence_requirements, list) or not evidence_requirements:
            errors.append(f"{label}.evidenceRequirements: expected non-empty array")
        else:
            seen_evidence: set[str] = set()
            evidence_required = {"evidenceType", "required", "freshness", "description"}
            for evidence_index, evidence in enumerate(evidence_requirements):
                evidence_label = f"{label}.evidenceRequirements[{evidence_index}]"
                if not isinstance(evidence, dict):
                    errors.append(f"{evidence_label}: expected object")
                    continue
                errors.extend(_errors_for_keys(evidence, evidence_required, evidence_label))
                fingerprint = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
                if fingerprint in seen_evidence:
                    errors.append(f"{evidence_label}: duplicate evidence requirement")
                seen_evidence.add(fingerprint)
                if evidence.get("evidenceType") not in EVIDENCE_TYPES:
                    errors.append(f"{evidence_label}.evidenceType: unsupported value")
                if evidence.get("required") is not True:
                    errors.append(f"{evidence_label}.required: must be true")
                if evidence.get("freshness") not in FRESHNESS_VALUES:
                    errors.append(f"{evidence_label}.freshness: unsupported value")
                errors.extend(_safe_text(evidence.get("description"), f"{evidence_label}.description"))
        errors.extend(_safe_text(capability.get("purpose"), f"{label}.purpose"))

    for key, location, child in _walk(manifest):
        normalized = key.replace("_", "").replace("-", "").lower()
        if normalized in FORBIDDEN_KEYS:
            errors.append(f"{location}: authority-bearing field is forbidden")
        if isinstance(child, str) and URL.search(child):
            errors.append(f"{location}: remote instruction URL is forbidden")

    eligible = not errors and all(
        isinstance(item, dict)
        and item.get("availability") == "AVAILABLE"
        and _status_is_consistent(item.get("availability"), item.get("status"))
        for item in capabilities
    )
    return {"valid": not errors, "eligible": eligible, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-fingerprints", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        expected = json.loads(args.expected_fingerprints.read_text(encoding="utf-8")) if args.expected_fingerprints else None
        result = validate_manifest(manifest, expected_fingerprints=expected)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(json.dumps({"valid": False, "eligible": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
