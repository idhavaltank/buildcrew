#!/usr/bin/env python3
"""Deterministic policy selection and resolution for a context map."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .policy_catalog import POLICY_VERSION, explain_rule, rules_for_profiles
from .project_context import _is_within, _looks_sensitive, utc_now

POLICY_SCHEMA_VERSION = "policy-bundle-v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _normalise_profile(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def select_profiles(
    context_map: Mapping[str, Any],
    *,
    explicit_profiles: Sequence[str] = (),
) -> list[str]:
    profiles: list[str] = ["v3_common"]
    declared = [_normalise_profile(item) for item in context_map.get("manifestProfiles", []) if str(item).strip()]
    explicit = [_normalise_profile(item) for item in explicit_profiles if str(item).strip()]
    profiles.extend(explicit)
    profiles.extend(declared)

    platforms = {
        str(item.get("platform"))
        for item in context_map.get("platformSignals", [])
        if item.get("confidence") == "high"
    }
    if "cross_platform" in platforms:
        for platform in ("web", "mobile", "backend"):
            if platform in platforms:
                profiles.append(platform)
    else:
        profiles.extend(platform for platform in ("web", "mobile", "backend") if platform in platforms)
    if "flutter" in platforms or "flutter" in declared or "flutter" in explicit:
        profiles.extend(("mobile", "flutter"))

    products = {
        str(item.get("product"))
        for item in context_map.get("productSignals", [])
        if item.get("confidence") == "high"
    }
    if "shared_application" in products or "shared_application" in declared or "shared_application" in explicit:
        profiles.append("shared_application")
    if "media" in products or "media" in declared or "media" in explicit:
        profiles.append("media")

    supported = {"v3_common", "shared_application", "web", "mobile", "backend", "flutter", "media"}
    return _unique(profile for profile in profiles if profile in supported)


def _targets(intake: Mapping[str, Any]) -> list[str]:
    values = intake.get("targetPaths") or intake.get("targets") or ["."]
    return [str(value) for value in values] or ["."]


def _applies(rule: Mapping[str, Any], targets: Sequence[str]) -> bool:
    patterns = rule.get("appliesTo") or ["**"]
    for target in targets:
        normalized = target.strip("/") or "."
        for raw_pattern in patterns:
            pattern = str(raw_pattern).strip("/")
            if pattern in {"", "**", "*"}:
                return True
            if pattern.endswith("/**"):
                prefix = pattern[:-3].rstrip("/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
            if fnmatch.fnmatch(normalized, pattern):
                return True
    return False


def _safe_requirement(value: str) -> str:
    value = re.sub(r"(?i)(api[_-]?key|token|password|secret|dsn)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    value = value.strip()
    return value[:500] or "Task acceptance criterion"


def _manifest_rules(manifest: Mapping[str, Any] | None, source_ref: str | None) -> list[dict[str, Any]]:
    if not manifest or not isinstance(manifest.get("policyRules"), list):
        return []
    result: list[dict[str, Any]] = []
    for raw in manifest["policyRules"]:
        if not isinstance(raw, Mapping):
            continue
        rule_id = str(raw.get("ruleId", "")).strip()
        if not rule_id:
            continue
        existing = explain_rule(rule_id)
        if existing:
            rule = existing
            rule["source"] = "project_manifest"
            rule["sourceRef"] = source_ref or "project-manifest"
            if raw.get("enforcement") in {"BLOCK", "REQUIRED", "ADVISORY", "DISCOVERED"}:
                rule["enforcement"] = raw["enforcement"]
            if isinstance(raw.get("appliesTo"), list) and raw["appliesTo"]:
                rule["appliesTo"] = [str(item) for item in raw["appliesTo"]]
            result.append(rule)
            continue
        requirement = raw.get("requirement")
        if not isinstance(requirement, str) or not requirement.strip():
            continue
        result.append({
            "ruleId": rule_id,
            "scope": "PROJECT",
            "title": str(raw.get("title") or rule_id),
            "requirement": _safe_requirement(requirement),
            "enforcement": raw.get("enforcement", "REQUIRED") if raw.get("enforcement") in {"BLOCK", "REQUIRED", "ADVISORY", "DISCOVERED"} else "REQUIRED",
            "source": "project_manifest",
            "sourceRef": source_ref or "project-manifest",
            "appliesTo": [str(item) for item in raw.get("appliesTo", ["**"])],
            "riskSignals": [str(item) for item in raw.get("riskSignals", [])],
            "requiredCapabilities": [str(item) for item in raw.get("requiredCapabilities", [])],
            "requiredEvidence": [str(item) for item in raw.get("requiredEvidence", ["project rule evidence"])],
            "conflictsWith": [str(item) for item in raw.get("conflictsWith", [])],
        })
    return result


def _instruction_applies_to(source: Mapping[str, Any]) -> list[str]:
    scope = str(source.get("scope", "/"))
    if scope in {"", "/"}:
        return ["**"]
    return [scope.lstrip("/")]


def _instruction_risk(line: str) -> list[str]:
    lowered = line.lower()
    tokens = ("security", "secret", "credential", "authorization", "permission", "migration", "data loss", "controller")
    return [token for token in tokens if token in lowered]


def _authoritative_instruction_rules(
    context_map: Mapping[str, Any],
    repository_root: str | None,
) -> list[dict[str, Any]]:
    """Promote only explicit normative lines from authoritative AGENTS files."""

    if not repository_root:
        return []
    root = Path(repository_root).resolve(strict=False)
    result: list[dict[str, Any]] = []
    marker = re.compile(r"(?i)\b(?:MUST\s+NOT|MUST|DO\s+NOT|NEVER|REQUIRED)\b")
    for source in context_map.get("sources", []):
        if source.get("kind") != "instruction" or source.get("usability") != "authoritative":
            continue
        raw_path = str(source.get("path", ""))
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts or relative.name != "AGENTS.md":
            continue
        path = (root / Path(*relative.parts)).resolve(strict=False)
        if not _is_within(path, root) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip().lstrip("-*> ")
            if not line or not marker.search(line) or _looks_sensitive(line):
                continue
            safe_line = _safe_requirement(line)
            source_ref = relative.as_posix()
            rule_id = f"PROJECT.INSTRUCTION.{_fingerprint((source_ref, line_number, safe_line))[:16]}"
            result.append({
                "ruleId": rule_id,
                "scope": "PROJECT",
                "title": "Authoritative repository instruction",
                "requirement": safe_line,
                "enforcement": "BLOCK" if re.search(r"(?i)MUST\s+NOT|DO\s+NOT|NEVER", line) and any(token in line.lower() for token in ("secret", "credential", "destructive", "controller")) else "REQUIRED",
                "source": "repository_instruction",
                "sourceRef": source_ref,
                "sourceEvidence": f"{source_ref}:{line_number}",
                "appliesTo": _instruction_applies_to(source),
                "riskSignals": _instruction_risk(line),
                "requiredCapabilities": ["repository instruction verification"],
                "requiredEvidence": ["instruction evidence"],
                "conflictsWith": [],
            })
    return result


def _task_rules(intake: Mapping[str, Any], targets: Sequence[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, criterion in enumerate(intake.get("acceptanceCriteria", []) or []):
        if not isinstance(criterion, str) or not criterion.strip():
            continue
        result.append({
            "ruleId": f"TASK.ACCEPTANCE.{index + 1}",
            "scope": "TASK",
            "title": "Task acceptance criterion",
            "requirement": _safe_requirement(criterion),
            "enforcement": "REQUIRED",
            "source": "task_requirement",
            "sourceRef": "canonical-intake",
            "appliesTo": list(targets),
            "riskSignals": [],
            "requiredCapabilities": ["acceptance verification"],
            "requiredEvidence": ["acceptance criterion evidence"],
            "conflictsWith": [],
        })
    return result


def _rule_conflicts(rules: Sequence[Mapping[str, Any]], context_conflicts: Sequence[Mapping[str, Any]], targets: Sequence[str]) -> list[dict[str, Any]]:
    conflicts = [deepcopy(dict(item)) for item in context_conflicts]
    by_id = {str(rule.get("ruleId")): rule for rule in rules}
    seen = {(item.get("subject"), tuple(sorted(item.get("sourcePaths", [])))) for item in conflicts}
    for rule in rules:
        for other_id in rule.get("conflictsWith", []):
            other = by_id.get(str(other_id))
            if not other or not _applies(rule, targets) or not _applies(other, targets):
                continue
            source_paths = tuple(sorted({str(rule.get("sourceRef")), str(other.get("sourceRef"))}))
            key = (f"{rule['ruleId']} conflicts with {other['ruleId']}", source_paths)
            if key in seen:
                continue
            conflicts.append({
                "conflictId": f"rule-conflict-{_fingerprint(key)[:12]}",
                "sourcePaths": list(source_paths),
                "subject": key[0],
                "blocking": True,
                "reason": "Two applicable policy rules declare incompatible requirements.",
            })
            seen.add(key)
    return conflicts


def _risk_floor(rules: Sequence[Mapping[str, Any]], intake: Mapping[str, Any], profiles: Sequence[str]) -> tuple[str, list[str]]:
    text = " ".join(str(value) for value in (
        intake.get("requestType", ""),
        intake.get("normalizedObjective", ""),
        *(intake.get("acceptanceCriteria", []) or []),
    )).lower()
    reasons: list[str] = []
    high_tokens = ("authorization", "permission", "tenant", "security", "privacy", "data loss", "activation", "deployment")
    coupled_tokens = ("offline", "persistence", "migration", "queue", "retry", "device", "lifecycle", "cross-platform", "contract")
    request_type = str(intake.get("requestType", "NEW_TASK")).upper()
    tier = "lightweight" if request_type in {"DIRECT", "DIRECT_VALIDATION", "QUESTION"} else "normal"
    task_signals = set(high_tokens + coupled_tokens)
    task_text_signals = {signal for signal in task_signals if signal in text}
    for rule in rules:
        for signal in rule.get("riskSignals", []):
            normalized = str(signal).lower()
            if normalized in task_text_signals:
                reasons.append(normalized)
    reasons.extend(task_text_signals)
    reasons = _unique(reasons)
    if any(reason in high_tokens for reason in reasons):
        tier = "high"
    elif any(reason in coupled_tokens for reason in reasons):
        tier = "coupled" if any(reason in coupled_tokens for reason in reasons) else "normal"
    return tier, reasons


def resolve_policy(
    intake: Mapping[str, Any],
    context_map: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | None = None,
    repository_root: str | None = None,
    explicit_profiles: Sequence[str] = (),
) -> dict[str, Any]:
    profiles = select_profiles(context_map, explicit_profiles=explicit_profiles)
    targets = _targets(intake)
    rules = rules_for_profiles(profiles)
    rules.extend(_authoritative_instruction_rules(context_map, repository_root))
    rules.extend(_manifest_rules(manifest, manifest_path))
    rules.extend(_task_rules(intake, targets))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in rules:
        if rule["ruleId"] not in seen and _applies(rule, targets):
            deduped.append(rule)
            seen.add(rule["ruleId"])

    conflicts = _rule_conflicts(deduped, context_map.get("conflicts", []), targets)
    blocking_conflicts = [item for item in conflicts if item.get("blocking")]
    blocking_gaps = [item for item in context_map.get("gaps", []) if item.get("blocking")]
    context_status = context_map.get("status")
    risk_tier, risk_reasons = _risk_floor(deduped, intake, profiles)
    capabilities = _unique(str(item) for rule in deduped for item in rule.get("requiredCapabilities", []))
    evidence = _unique(str(item) for rule in deduped for item in rule.get("requiredEvidence", []))
    policy_input = {"profiles": profiles, "rules": deduped, "conflicts": conflicts, "riskTier": risk_tier}
    if context_status in {"UNSEEN", "DISCOVERING", "STALE", "CONFLICT", "CORRUPT"}:
        mutation_status = "BLOCKED"
    elif context_status == "UNAVAILABLE":
        mutation_status = "REVIEW_REQUIRED"
    elif context_status == "INCOMPLETE":
        mutation_status = "REVIEW_REQUIRED"
    else:
        mutation_status = "BLOCKED" if blocking_conflicts else ("REVIEW_REQUIRED" if blocking_gaps else "READY")
    if context_status not in {"CURRENT", "INCOMPLETE", "UNAVAILABLE"}:
        risk_reasons = _unique([*risk_reasons, f"context status: {context_status or 'missing'}"])
    elif context_status in {"INCOMPLETE", "UNAVAILABLE"}:
        risk_reasons = _unique([*risk_reasons, f"context status: {context_status}"])
    return {
        "schemaVersion": POLICY_SCHEMA_VERSION,
        "repository": deepcopy(context_map["repository"]),
        "fingerprints": {
            "sourceFingerprint": context_map["fingerprints"]["sourceFingerprint"],
            "contextFingerprint": context_map["fingerprints"]["contextFingerprint"],
            "policyFingerprint": _fingerprint(policy_input),
            "taskScopeFingerprint": context_map["fingerprints"]["taskScopeFingerprint"],
        },
        "selectedProfiles": profiles,
        "rules": deduped,
        "requiredCapabilities": capabilities,
        "requiredEvidence": evidence,
        "riskTier": risk_tier,
        "riskReasons": risk_reasons,
        "conflicts": conflicts,
        "mutationStatus": mutation_status,
        "policyVersion": POLICY_VERSION,
        "generatedAt": utc_now(),
    }
