#!/usr/bin/env python3
"""Dependency-free, provider-neutral RAG retrieval and learning contracts.

The module deliberately accepts records supplied by a host adapter.  It never opens an
index, writes Markdown, or claims a provider call that did not happen.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import re
from time import perf_counter
from typing import Any, Iterable


RETRIEVABLE_STATUSES = {"verified", "confirmed"}
HIDDEN_STATUSES = {"superseded", "rejected", "quarantined"}
LESSON_KINDS = {"project_fact", "user_preference", "correction", "failure_pattern", "process_proposal"}
LESSON_SCOPES = {"session", "repo", "project", "tenant", "global"}
LESSON_STATUSES = {"candidate", "needs_review", "verified", "confirmed", *HIDDEN_STATUSES}
MEMORY_TIERS = {"stable_policy", "dynamic_durable", "session"}
CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}
FINGERPRINT_FIELDS = {"errorCode", "normalizedError", "symbol", "path", "route", "issueKey", "stackHash", "taskType"}
FEEDBACK_OUTCOMES = {"helpful", "irrelevant", "stale", "incorrect"}
WRITEBACK_STATES = {"saved", "updated", "candidate_pending_review", "rejected", "redacted", "quarantined", "failed", "none"}
BUDGETS = {
    "small": {"maxRecords": 3, "tokenBudget": 1200},
    "normal": {"maxRecords": 5, "tokenBudget": 2000},
    "complex": {"maxRecords": 8, "tokenBudget": 3500},
}

_FORBIDDEN_KEYS = {"rawprompt", "prompt", "transcript", "patch", "rawlog", "unredactedlog", "cookie", "token", "password", "privatekey", "dsn"}
_SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:api[_-]?key|token|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization)\s*[:=]", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb)://[^\s/@]+:[^\s/@]+@", re.I),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:\+?\d[\s().-]?){9,}\d\b"),
    re.compile(r"\b(?:customer|user|account)[_-]?(?:id|identifier)\s*[:=]", re.I),
    re.compile(r"(?m)^(?:diff --git |@@ [^\n]* @@)", re.I),
)


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _now(value)
    except ValueError:
        return False
    return True


def _strings(value: Any) -> set[str]:
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return {item.strip() for item in value}
    return set()


def privacy_findings(value: Any, path: str = "record") -> list[str]:
    """Return safe categories only; never return the sensitive value itself."""
    findings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            nested_path = f"{path}.{key}"
            if normalized in _FORBIDDEN_KEYS:
                findings.append(f"{nested_path}: forbidden raw field")
            findings.extend(privacy_findings(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(privacy_findings(nested, f"{path}[{index}]"))
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS):
        findings.append(f"{path}: sensitive content")
    return findings


def validate_lesson(record: Any, *, redact: bool = False) -> dict[str, Any]:
    """Validate a sanitized lesson without requiring a JSON-schema dependency."""
    privacy = privacy_findings(record)
    if privacy:
        return {
            "valid": False,
            "outcome": "redacted" if redact else "rejected",
            "errors": ["lesson contains forbidden sensitive material"],
            "privacyFindings": privacy,
            "record": None,
        }
    if not isinstance(record, dict):
        return {"valid": False, "outcome": "rejected", "errors": ["record: expected object"], "privacyFindings": [], "record": None}

    errors: list[str] = []
    required = {
        "schemaVersion", "id", "claim", "kind", "scope", "memoryTier", "projectId", "repository", "branchOrTree",
        "accessScope", "fingerprints", "status", "confidence", "canonicalKey", "contentHash", "evidence",
        "observedAt", "lastVerifiedAt", "reviewAt", "expiresAt", "contradictionGroup", "sensitivity",
    }
    optional = {
        "filesInvolved", "businessLogic", "fixStrategy", "relatedRecords", "supersedes", "createdBy",
        "tenantId", "embeddingModel", "embeddingVersion",
    }
    errors.extend(f"record: missing required field {key}" for key in sorted(required - set(record)))
    errors.extend(f"record: unknown field {key}" for key in sorted(set(record) - required - optional))
    if record.get("schemaVersion") != "rag-record-v1":
        errors.append("record.schemaVersion: expected rag-record-v1")
    for key in ("id", "claim", "projectId", "repository", "branchOrTree", "canonicalKey", "contentHash"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(f"record.{key}: expected non-empty string")
    if record.get("kind") not in LESSON_KINDS:
        errors.append("record.kind: unsupported value")
    if record.get("scope") not in LESSON_SCOPES:
        errors.append("record.scope: unsupported value")
    if record.get("memoryTier") not in MEMORY_TIERS:
        errors.append("record.memoryTier: unsupported value")
    if record.get("status") not in LESSON_STATUSES:
        errors.append("record.status: unsupported value")
    if record.get("confidence") not in CONFIDENCE_ORDER:
        errors.append("record.confidence: unsupported value")
    if record.get("sensitivity") not in {"none", "redacted"}:
        errors.append("record.sensitivity: unsupported value")
    if not _strings(record.get("accessScope")):
        errors.append("record.accessScope: expected non-empty string array")
    fingerprints = record.get("fingerprints")
    if not isinstance(fingerprints, dict):
        errors.append("record.fingerprints: expected object")
    elif set(fingerprints) - FINGERPRINT_FIELDS or any(not isinstance(v, str) or not v.strip() for v in fingerprints.values()):
        errors.append("record.fingerprints: unsupported or empty fingerprint")
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("record.evidence: expected non-empty array")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict) or set(item) != {"repository", "commitOrTree", "path", "symbol", "check", "sourceType"}:
                errors.append(f"record.evidence[{index}]: expected exact evidence object")
                continue
            if any(not isinstance(item[key], str) or not item[key].strip() for key in ("repository", "commitOrTree", "path", "symbol", "check")):
                errors.append(f"record.evidence[{index}]: expected non-empty evidence strings")
            if item.get("sourceType") not in {"code", "test", "runtime", "user", "review"}:
                errors.append(f"record.evidence[{index}].sourceType: unsupported value")
    for key in ("observedAt", "lastVerifiedAt", "reviewAt", "expiresAt"):
        if not _iso(record.get(key)):
            errors.append(f"record.{key}: expected ISO-8601 timestamp or null")
    if record.get("contradictionGroup") is not None and (not isinstance(record["contradictionGroup"], str) or not record["contradictionGroup"].strip()):
        errors.append("record.contradictionGroup: expected non-empty string or null")
    if "tenantId" in record and (not isinstance(record["tenantId"], str) or not record["tenantId"].strip()):
        errors.append("record.tenantId: expected non-empty string when present")
    for key in ("embeddingModel", "embeddingVersion"):
        if key in record and (not isinstance(record[key], str) or not record[key].strip()):
            errors.append(f"record.{key}: expected non-empty string when present")
    if record.get("memoryTier") == "session" and record.get("scope") != "session":
        errors.append("record.memoryTier: session tier must use session scope")
    if record.get("memoryTier") == "stable_policy" and record.get("status") not in {"verified", "confirmed"}:
        errors.append("record.memoryTier: stable policy must be verified or confirmed")
    return {"valid": not errors, "outcome": "accepted" if not errors else "rejected", "errors": errors, "privacyFindings": [], "record": deepcopy(record) if not errors else None}


def budget_for_profile(profile: str) -> dict[str, int]:
    if profile not in BUDGETS:
        raise ValueError("profile must be small, normal, or complex")
    return deepcopy(BUDGETS[profile])


def freshness_state(record: dict[str, Any], *, current_tree: str, now: str | datetime | None = None) -> str:
    if record.get("status") in HIDDEN_STATUSES:
        return "hidden"
    check_time = _now(now)
    expires_at = record.get("expiresAt")
    if expires_at and _now(expires_at) <= check_time:
        return "hidden"
    if record.get("branchOrTree") != current_tree:
        return "stale"
    review_at = record.get("reviewAt")
    if review_at and _now(review_at) <= check_time:
        return "due_for_review"
    if not record.get("lastVerifiedAt"):
        return "unknown"
    return "current"


def _scope_valid(project_id: Any, repository: Any, branch_or_tree: Any, access_scope: Any) -> tuple[bool, set[str]]:
    return all(isinstance(value, str) and value.strip() for value in (project_id, repository, branch_or_tree)), _strings(access_scope)


def source_first_fallback(
    *,
    outcome: str,
    project_id: str,
    repository: str,
    branch_or_tree: str,
    access_scope: Iterable[str],
    limitation: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "cards": [],
        "queryFingerprint": None,
        "scopeApplied": {"projectId": project_id, "repository": repository, "branchOrTree": branch_or_tree, "accessScope": sorted(access_scope)},
        "providerAttempted": False,
        "expansionCount": 0,
        "limitation": limitation,
        "sourceFirst": True,
        "providerIntegration": "not_claimed",
        "conflicts": [],
        "metrics": {
            "recordsConsidered": 0,
            "cardsReturned": 0,
            "staleCards": 0,
            "duplicateExcluded": 0,
            "conflictCount": 0,
            "injectedTokens": 0,
            "latencyMs": 0,
            "sourceFirstFallback": True,
        },
        "tenantApplied": tenant_id,
        "memoryTiersApplied": [],
    }


def retrieve_knowledge(
    records: Iterable[Any] | None,
    *,
    query: str,
    project_id: str,
    repository: str,
    branch_or_tree: str,
    access_scope: str | list[str],
    fingerprints: dict[str, str] | None = None,
    task_type: str | None = None,
    token_budget: int = 1200,
    max_records: int = 3,
    now: str | datetime | None = None,
    provider_available: bool = True,
    tenant_id: str | None = None,
    memory_tiers: str | list[str] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    scope_ok, caller_scopes = _scope_valid(project_id, repository, branch_or_tree, access_scope)
    requested_tiers = _strings(memory_tiers) if memory_tiers is not None else set(MEMORY_TIERS)
    invalid_tier_request = not requested_tiers or not requested_tiers.issubset(MEMORY_TIERS)
    invalid_tenant = tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id.strip())
    if (
        not scope_ok
        or not caller_scopes
        or not isinstance(query, str)
        or not query.strip()
        or privacy_findings(query, "query")
        or invalid_tier_request
        or invalid_tenant
    ):
        return source_first_fallback(outcome="invalid", project_id=project_id, repository=repository, branch_or_tree=branch_or_tree, access_scope=caller_scopes, limitation="invalid or sensitive retrieval request", tenant_id=tenant_id)
    if not provider_available or records is None:
        return source_first_fallback(outcome="unavailable", project_id=project_id, repository=repository, branch_or_tree=branch_or_tree, access_scope=caller_scopes, limitation="knowledge provider unavailable; continue source-first", tenant_id=tenant_id)
    if not isinstance(token_budget, int) or isinstance(token_budget, bool) or not isinstance(max_records, int) or isinstance(max_records, bool) or token_budget < 1 or max_records < 1:
        return source_first_fallback(outcome="invalid", project_id=project_id, repository=repository, branch_or_tree=branch_or_tree, access_scope=caller_scopes, limitation="invalid retrieval budget", tenant_id=tenant_id)
    if fingerprints is not None and not isinstance(fingerprints, dict):
        return source_first_fallback(outcome="invalid", project_id=project_id, repository=repository, branch_or_tree=branch_or_tree, access_scope=caller_scopes, limitation="invalid fingerprint request", tenant_id=tenant_id)
    if task_type is not None and (not isinstance(task_type, str) or not task_type.strip()):
        return source_first_fallback(outcome="invalid", project_id=project_id, repository=repository, branch_or_tree=branch_or_tree, access_scope=caller_scopes, limitation="invalid task-type filter", tenant_id=tenant_id)

    requested_fingerprints = {key: value.casefold() for key, value in (fingerprints or {}).items() if key in FINGERPRINT_FIELDS and isinstance(value, str) and value.strip()}
    query_terms = set(re.findall(r"[a-z0-9_./:-]+", query.casefold()))
    candidates: list[tuple[tuple[int, int, int, int, int], dict[str, Any], tuple[str, str]]] = []
    excluded_invalid = 0
    records_considered = 0
    for raw in records:
        checked = validate_lesson(raw)
        if not checked["valid"]:
            excluded_invalid += 1
            continue
        record = checked["record"]
        records_considered += 1
        if record["projectId"] != project_id or record["repository"] != repository:
            continue
        if (
            not set(record["accessScope"]).issubset(caller_scopes)
            or record.get("memoryTier") not in requested_tiers
            or record.get("tenantId") != tenant_id
        ):
            continue
        if record["status"] not in RETRIEVABLE_STATUSES:
            continue
        if task_type and record["fingerprints"].get("taskType") not in {None, task_type}:
            continue
        freshness = freshness_state(record, current_tree=branch_or_tree, now=now)
        if freshness == "hidden":
            continue
        exact = sum(1 for key, value in requested_fingerprints.items() if record["fingerprints"].get(key, "").casefold() == value)
        lexical = len(query_terms & set(re.findall(r"[a-z0-9_./:-]+", (record["claim"] + " " + " ".join(record["fingerprints"].values())).casefold())))
        if not exact and not lexical:
            continue
        card = _card(record, match="exact_fingerprint" if exact else "hybrid", freshness=freshness)
        user_correction = int(record["kind"] == "correction" and any(item["sourceType"] == "user" for item in record["evidence"]))
        score = (user_correction, 1 if exact else 0, exact, lexical, CONFIDENCE_ORDER[record["confidence"]])
        candidates.append((score, card, (record["canonicalKey"], record["contentHash"])))
    candidates.sort(key=lambda item: item[0], reverse=True)
    cards: list[dict[str, Any]] = []
    tokens_used = 0
    duplicate_excluded = 0
    seen_duplicates: set[tuple[str, str]] = set()
    for _, card, dedupe_key in candidates:
        if dedupe_key in seen_duplicates:
            duplicate_excluded += 1
            continue
        seen_duplicates.add(dedupe_key)
        if len(cards) >= min(max_records, 8):
            break
        estimated = _estimate_tokens(card)
        if tokens_used + estimated > min(token_budget, 3500):
            continue
        cards.append(card)
        tokens_used += estimated
    conflicts = _mark_conflicts(cards)
    return {
        "outcome": "matched" if cards else "no_relevant_memory",
        "cards": cards,
        "queryFingerprint": _query_fingerprint(query, project_id, repository, branch_or_tree, requested_fingerprints),
        "scopeApplied": {"projectId": project_id, "repository": repository, "branchOrTree": branch_or_tree, "accessScope": sorted(caller_scopes)},
        "providerAttempted": True,
        "expansionCount": 0,
        "limitation": "invalid or unsafe records were excluded" if excluded_invalid else None,
        "sourceFirst": False,
        "providerIntegration": "adapter_records_supplied",
        "conflicts": conflicts,
        "budget": {"maxRecords": min(max_records, 8), "tokenBudget": min(token_budget, 3500), "tokensUsed": tokens_used},
        "metrics": {
            "recordsConsidered": records_considered,
            "cardsReturned": len(cards),
            "staleCards": sum(card["freshness"]["state"] == "stale" for card in cards),
            "duplicateExcluded": duplicate_excluded,
            "conflictCount": len(conflicts),
            "injectedTokens": tokens_used,
            "latencyMs": round((perf_counter() - started) * 1000, 3),
            "sourceFirstFallback": False,
        },
        "executionAuthority": (
            "current_source"
            if conflicts
            else "explicit_user_direction"
            if any(card.get("authority") == "explicit_user_direction" for card in cards)
            else "revalidate_cards"
        ),
        "tenantApplied": tenant_id,
        "memoryTiersApplied": sorted(requested_tiers),
    }


def _card(record: dict[str, Any], *, match: str, freshness: str) -> dict[str, Any]:
    usable = "usable_after_validation" if freshness in {"current", "due_for_review"} else "do_not_use"
    explicit_user_direction = record["kind"] == "correction" and any(item["sourceType"] == "user" for item in record["evidence"])
    return {
        "recordId": record["id"], "claim": record["claim"], "match": match,
        "confidence": record["confidence"], "freshness": {"lastVerifiedAt": record["lastVerifiedAt"], "reviewAt": record["reviewAt"], "expiresAt": record["expiresAt"], "state": freshness},
        "scope": record["scope"], "evidenceRefs": [f"{item['repository']}:{item['commitOrTree']}:{item['path']}:{item['symbol']}" for item in record["evidence"]],
        "action": "explicit user direction is authoritative; revalidate against current source before execution" if explicit_user_direction else "revalidate against current source, tests, runtime, or user direction",
        "usability": usable,
        "authority": "explicit_user_direction" if explicit_user_direction else "hypothesis",
        "memoryTier": record["memoryTier"],
        "conflictGroup": record["contradictionGroup"],
    }


def _estimate_tokens(card: dict[str, Any]) -> int:
    return max(1, (len(card["claim"]) + sum(len(item) for item in card["evidenceRefs"]) + len(card["action"]) + 3) // 4)


def _query_fingerprint(query: str, project_id: str, repository: str, branch_or_tree: str, fingerprints: dict[str, str]) -> str:
    import hashlib
    payload = "|".join([query.strip().casefold(), project_id, repository, branch_or_tree, *[f"{key}={value}" for key, value in sorted(fingerprints.items())]])
    return hashlib.sha256(payload.encode()).hexdigest()


def _mark_conflicts(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        if card["conflictGroup"]:
            groups.setdefault(card["conflictGroup"], []).append(card)
    conflicts = []
    for group, grouped in sorted(groups.items()):
        if len(grouped) < 2:
            continue
        for card in grouped:
            card["usability"] = "do_not_use"
            card["action"] = "current source or explicit user direction wins; preserve conflict for review"
        conflicts.append({"group": group, "recordIds": [card["recordId"] for card in grouped], "resolution": "current_source_wins", "state": "needs_review"})
    return conflicts


def fetch_knowledge(records: Iterable[Any] | None, *, record_id: str, project_id: str, repository: str, branch_or_tree: str, access_scope: str | list[str]) -> dict[str, Any]:
    scope_ok, caller_scopes = _scope_valid(project_id, repository, branch_or_tree, access_scope)
    if not scope_ok or not caller_scopes or not isinstance(record_id, str) or not record_id.strip():
        return {"outcome": "invalid", "record": None, "providerIntegration": "not_claimed"}
    if records is None:
        return {"outcome": "unavailable", "record": None, "providerIntegration": "not_claimed"}
    for raw in records:
        checked = validate_lesson(raw)
        if not checked["valid"]:
            continue
        record = checked["record"]
        if record["id"] != record_id:
            continue
        if (record["projectId"] != project_id or record["repository"] != repository
                or not set(record["accessScope"]).issubset(caller_scopes)
                or record["status"] not in RETRIEVABLE_STATUSES):
            return {"outcome": "denied_or_not_found", "record": None, "providerIntegration": "adapter_records_supplied"}
        freshness = freshness_state(record, current_tree=branch_or_tree)
        if freshness == "hidden":
            return {"outcome": "denied_or_not_found", "record": None, "providerIntegration": "adapter_records_supplied"}
        return {"outcome": "matched", "record": _card(record, match="record_id", freshness=freshness), "providerIntegration": "adapter_records_supplied"}
    return {"outcome": "denied_or_not_found", "record": None, "providerIntegration": "adapter_records_supplied"}


def _learning(state: str, *, candidate_id: str | None = None, duplicate_of: str | None = None, limitation: str | None = None) -> dict[str, Any]:
    """Return a bounded learning outcome; never include raw investigation material."""
    return {
        "state": state,
        "candidateId": candidate_id,
        "duplicateOf": duplicate_of,
        "limitation": limitation,
    }


def find_duplicate_lesson(records: Iterable[Any] | None, candidate: dict[str, Any]) -> dict[str, str] | None:
    """Find a same-scope canonical/content duplicate before candidate write-back."""
    if records is None:
        return None
    for raw in records:
        checked = validate_lesson(raw)
        if not checked["valid"]:
            continue
        existing = checked["record"]
        same_scope = (
            existing["projectId"] == candidate["projectId"]
            and existing["repository"] == candidate["repository"]
            and existing.get("tenantId") == candidate.get("tenantId")
            and existing["memoryTier"] == candidate["memoryTier"]
            and set(existing["accessScope"]) == set(candidate["accessScope"])
        )
        if not same_scope:
            continue
        if existing["contentHash"] == candidate["contentHash"]:
            return {"recordId": existing["id"], "match": "content_hash"}
        if existing["canonicalKey"] == candidate["canonicalKey"]:
            return {"recordId": existing["id"], "match": "canonical_key"}
    return None


def propose_lesson(
    record: Any,
    *,
    candidate_write_authorized: bool,
    redact: bool = False,
    existing_records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    if not candidate_write_authorized:
        return {"outcome": "blocked", "candidate": None, "limitation": "candidate-write permission denied", "learning": _learning("blocked", limitation="candidate-write permission denied")}
    candidate = deepcopy(record) if isinstance(record, dict) else record
    if isinstance(candidate, dict):
        candidate["status"] = "candidate"
    checked = validate_lesson(candidate, redact=redact)
    if not checked["valid"]:
        return {"outcome": checked["outcome"], "candidate": None, "limitation": "privacy or schema validation failed", "learning": _learning(checked["outcome"], limitation="privacy or schema validation failed")}
    duplicate = find_duplicate_lesson(existing_records, checked["record"])
    if duplicate:
        return {
            "outcome": "duplicate",
            "candidate": None,
            "candidateId": candidate["id"],
            "duplicateOf": duplicate["recordId"],
            "duplicateMatch": duplicate["match"],
            "limitation": "matching scoped lesson already exists; no new write claimed",
            "learning": _learning("none", candidate_id=candidate["id"], duplicate_of=duplicate["recordId"], limitation="duplicate suppressed"),
        }
    return {
        "outcome": "candidate",
        "candidateId": candidate["id"],
        "candidate": checked["record"],
        "limitation": None,
        "learning": _learning("candidate", candidate_id=candidate["id"]),
    }


def promote_lesson(candidate: Any, *, evidence: list[dict[str, Any]], reviewer: str, promotion_authorized: bool, now: str | datetime | None = None) -> dict[str, Any]:
    if not promotion_authorized:
        return {"outcome": "blocked", "record": None, "limitation": "promotion permission denied", "learning": _learning("blocked", limitation="promotion permission denied")}
    if not isinstance(candidate, dict) or candidate.get("status") not in {"candidate", "needs_review"}:
        return {"outcome": "rejected", "record": None, "limitation": "only candidate or needs_review lessons may be promoted", "learning": _learning("rejected", limitation="candidate lifecycle state is not promotable")}
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(evidence, list) or not evidence:
        return {"outcome": "needs_review", "record": None, "limitation": "verified evidence and reviewer are required", "learning": _learning("needs_review", limitation="verified evidence and reviewer are required")}
    if any(
        not isinstance(item, dict)
        or item.get("repository") != candidate.get("repository")
        or item.get("commitOrTree") != candidate.get("branchOrTree")
        for item in evidence
    ):
        return {"outcome": "needs_review", "record": None, "limitation": "promotion evidence must match the candidate repository and tree", "learning": _learning("needs_review", limitation="promotion evidence is not bound to the candidate tree")}
    promoted = deepcopy(candidate)
    promoted["evidence"] = evidence
    promoted["status"] = "verified"
    promoted["lastVerifiedAt"] = _now(now).isoformat().replace("+00:00", "Z")
    checked = validate_lesson(promoted)
    if not checked["valid"]:
        return {"outcome": "rejected", "record": None, "limitation": "promotion evidence failed validation", "learning": _learning("rejected", limitation="promotion evidence failed validation")}
    return {"outcome": "verified", "record": checked["record"], "reviewer": reviewer, "limitation": None, "learning": _learning("verified", candidate_id=candidate["id"])}


def record_knowledge_feedback(*, record_id: str, outcome: str, evidence: list[dict[str, Any]], feedback_authorized: bool) -> dict[str, Any]:
    if not feedback_authorized:
        return {"outcome": "denied", "acknowledged": False, "learning": _learning("blocked", limitation="feedback permission denied")}
    if not isinstance(record_id, str) or not record_id.strip() or outcome not in FEEDBACK_OUTCOMES or not isinstance(evidence, list) or not evidence:
        return {"outcome": "rejected", "acknowledged": False, "learning": _learning("rejected", limitation="feedback requires a known outcome and evidence")}
    if privacy_findings(evidence, "feedback.evidence"):
        return {"outcome": "redacted", "acknowledged": False, "learning": _learning("redacted", limitation="feedback evidence failed privacy scan")}
    return {"outcome": "acknowledged", "acknowledged": True, "feedback": outcome, "learning": _learning("none")}


def record_learning_writeback(
    *,
    state: str,
    provider_acknowledged: bool,
    record_id: str | None = None,
    limitation: str | None = None,
) -> dict[str, Any]:
    """Normalize an adapter write receipt without claiming an unacknowledged save."""
    if state not in WRITEBACK_STATES:
        return {"outcome": "blocked", "learning": _learning("blocked", limitation="unknown write-back state"), "providerIntegration": "not_claimed"}
    if state == "failed" or (state in {"saved", "updated", "candidate_pending_review"} and not provider_acknowledged):
        return {
            "outcome": "blocked",
            "learning": _learning("blocked", candidate_id=record_id, limitation=limitation or "provider write-back was not acknowledged"),
            "metrics": {
                "promotionCount": 0,
                "rejectionCount": int(state == "failed"),
                "candidateCount": int(state == "candidate_pending_review"),
                "fallbackSuccess": 0,
            },
            "providerIntegration": "not_claimed" if not provider_acknowledged else "adapter_records_supplied",
        }
    learning_state = "candidate" if state == "candidate_pending_review" else state
    return {
        "outcome": state,
        "learning": _learning(learning_state, candidate_id=record_id, limitation=limitation),
        "metrics": {
            "promotionCount": int(state in {"saved", "updated"}),
            "rejectionCount": int(state in {"rejected", "redacted", "quarantined", "failed"}),
            "candidateCount": int(state == "candidate_pending_review"),
            "fallbackSuccess": int(state == "none"),
        },
        "providerIntegration": "adapter_records_supplied" if provider_acknowledged else "not_claimed",
    }


def learning_metrics(outcomes: Iterable[str]) -> dict[str, int]:
    """Aggregate safe lifecycle counters for a host-level learning dashboard."""
    values = list(outcomes)
    return {
        "observed": len(values),
        "candidate": sum(value in {"candidate", "candidate_pending_review"} for value in values),
        "promoted": sum(value in {"verified", "confirmed", "saved", "updated"} for value in values),
        "needsReview": sum(value == "needs_review" for value in values),
        "rejected": sum(value in {"rejected", "redacted", "quarantined", "failed"} for value in values),
        "blocked": sum(value == "blocked" for value in values),
        "none": sum(value == "none" for value in values),
    }


def invalidate_knowledge(record: Any, *, reason: str, supersedes: str | None, invalidation_authorized: bool) -> dict[str, Any]:
    if not invalidation_authorized:
        return {"outcome": "blocked", "record": None, "limitation": "invalidation permission denied", "learning": _learning("blocked", limitation="invalidation permission denied")}
    if not isinstance(record, dict) or not isinstance(reason, str) or not reason.strip() or privacy_findings(reason, "invalidation.reason"):
        return {"outcome": "rejected", "record": None, "limitation": "record and reason are required and must be sanitized", "learning": _learning("rejected", limitation="invalid or sensitive invalidation reason")}
    updated = deepcopy(record)
    updated["status"] = "superseded" if supersedes else "needs_review"
    if supersedes:
        updated["supersedes"] = supersedes
    checked = validate_lesson(updated)
    outcome = updated["status"] if checked["valid"] else "rejected"
    return {
        "outcome": outcome,
        "record": checked["record"],
        "limitation": None if checked["valid"] else "invalidation record failed validation",
        "learning": _learning("updated" if checked["valid"] else "rejected", candidate_id=updated.get("id"), limitation=None if checked["valid"] else "invalidation record failed validation"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provider-neutral RAG contract library; host adapters supply records and permissions."
    )
    parser.add_argument(
        "--contract",
        action="store_true",
        help="print the supported operation names and exit without contacting a provider",
    )
    args = parser.parse_args()
    if args.contract:
        print("retrieve fetch propose promote feedback invalidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
