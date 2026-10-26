#!/usr/bin/env python3
"""Bounded automatic learning capture for actionable AE tasks.

Every input uses the separate read-only memory-context lookup. Simple questions
still skip durable writes. Actionable work is captured only at a terminal
outcome, sanitized, matched against existing knowledge, atomically upserted,
and queued by repository/module/feature without editing canonical memory files.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - platform-specific fallback
    fcntl = None

try:
    from rag_contracts import privacy_findings, validate_lesson
    from storage_paths import resolve_storage_paths
except ImportError:  # pragma: no cover - package import fallback
    from .rag_contracts import privacy_findings, validate_lesson
    from .storage_paths import resolve_storage_paths

try:
    from memory_context_runtime import (
        default_memory_root,
        derive_module_feature_route,
        queue_module_feature_update,
    )
except ImportError:  # pragma: no cover - package import fallback
    from .memory_context_runtime import (
        default_memory_root,
        derive_module_feature_route,
        queue_module_feature_update,
    )


STORE_SCHEMA_VERSION = "autonomous-engineering-learning-store-v1"
LEARNING_POLICY_VERSION = "learning-policy-v1"
MAX_RECORDS = 5000
MAX_TEXT = 2000
MAX_LIST_ITEMS = 64

SIMPLE_INTENTS = {
    "simple_question",
    "informational",
    "explanation",
    "status",
    "confirmation",
    "conversation",
}
REQUIRED_INTENTS = {
    "issue",
    "bug",
    "debug",
    "investigation",
    "complex",
    "feature",
    "implementation",
    "review",
    "test",
    "migration",
    "incident",
    "runtime",
    "cross_repo",
    "cross_repository",
}
LEARNING_KEYWORDS = (
    ("issue", re.compile(r"\b(?:issue|incident|bug|defect|failure|failing|broken|error)\b", re.I)),
    ("debug", re.compile(r"\b(?:debug|diagnos|investigat|trace|reproduc)\w*\b", re.I)),
    ("review", re.compile(r"\b(?:review|audit|assess|validat|evaluat|inspect)\w*\b", re.I)),
    ("change", re.compile(r"\b(?:implement|fix|repair|build|refactor|migrat|add|update|replace|expand|test)\w*\b", re.I)),
)
TERMINAL_TASK_STATES = {"completed", "complete", "blocked", "paused", "pause"}
ACTIVE_TASK_STATES = {"received", "in_progress", "running", "started"}
FINGERPRINT_KEYS = {"errorCode", "normalizedError", "symbol", "path", "route", "issueKey", "stackHash", "taskType"}
STATUS_RANK = {
    "superseded": 0,
    "rejected": 0,
    "quarantined": 0,
    "candidate": 1,
    "needs_review": 2,
    "verified": 3,
    "confirmed": 4,
}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
RETRIEVABLE_STATUSES = {"candidate", "needs_review", "verified", "confirmed"}
STRONG_FINGERPRINT_KEYS = {"errorCode", "normalizedError", "issueKey", "stackHash", "route"}


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, *, field: str, required: bool = False) -> tuple[str, str | None]:
    if not isinstance(value, str):
        return "", f"{field}: expected string"
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        return "", f"{field}: expected non-empty string"
    if len(normalized) > MAX_TEXT:
        return "", f"{field}: exceeds {MAX_TEXT} characters"
    return normalized, None


def _normal(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _safe_list(value: Any, *, field: str, required: bool = False) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], [f"{field}: expected string array"]
    if required and not value:
        return [], [f"{field}: expected non-empty string array"]
    if len(value) > MAX_LIST_ITEMS:
        return [], [f"{field}: exceeds {MAX_LIST_ITEMS} items"]
    result: list[str] = []
    errors: list[str] = []
    for item in value:
        text, error = _text(item, field=field, required=True)
        if error:
            errors.append(error)
        elif text not in result:
            result.append(text)
    return result, errors


def _safe_relative_paths(value: Any, *, field: str) -> tuple[list[str], list[str]]:
    values, errors = _safe_list(value, field=field, required=False)
    for path in values:
        normalized = path.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized or normalized == "..":
            errors.append(f"{field}: paths must be repository-relative")
    return values, errors


def _safe_fingerprints(value: Any, *, task_type: str) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    fingerprints: dict[str, str] = {}
    if value is not None:
        if not isinstance(value, Mapping):
            return {}, ["fingerprints: expected object"]
        for key, raw in value.items():
            if key not in FINGERPRINT_KEYS:
                errors.append(f"fingerprints.{key}: unsupported key")
                continue
            text, error = _text(raw, field=f"fingerprints.{key}", required=True)
            if error:
                errors.append(error)
            else:
                fingerprints[key] = text
    if not fingerprints and task_type:
        fingerprints["taskType"] = task_type
    if not fingerprints:
        errors.append("fingerprints: at least one identity fingerprint is required")
    return fingerprints, errors


def _safe_kind(value: Any, intent: str) -> tuple[str, str | None]:
    kind = _normal(value)
    if not kind:
        kind = "failure_pattern" if intent in REQUIRED_INTENTS and intent in {"issue", "bug", "debug", "incident"} else "project_fact"
    if kind not in {"project_fact", "user_preference", "correction", "failure_pattern", "process_proposal"}:
        return "", "kind: unsupported lesson kind"
    return kind, None


def _canonical_key(value: Any, *, payload: Mapping[str, Any], claim: str) -> str:
    raw = value if isinstance(value, str) else ""
    normalized = re.sub(r"[^a-z0-9._:-]+", "-", raw.casefold()).strip("-")[:120]
    if normalized:
        return normalized
    identity = "|".join(
        str(payload.get(key, "")).strip().casefold()
        for key in ("projectId", "repository", "taskId", "issueKey", "symbol", "path")
    )
    return "lesson-" + hashlib.sha256(f"{identity}|{claim.casefold()}".encode("utf-8")).hexdigest()[:24]


def _content_hash(record: Mapping[str, Any]) -> str:
    content = {
        key: value
        for key, value in record.items()
        if key not in {"id", "contentHash", "status", "observedAt", "lastVerifiedAt", "reviewAt", "expiresAt"}
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_input(payload: Mapping[str, Any] | None, *, input_text: str | None = None) -> dict[str, Any]:
    """Classify whether a task requires learning without persisting the input."""

    facts = payload if isinstance(payload, Mapping) else {}
    intent = _normal(facts.get("intent") or facts.get("requestType") or facts.get("taskType"))
    complexity = _normal(facts.get("complexity") or facts.get("riskTier"))
    route = _normal(facts.get("route"))
    actionable = facts.get("actionable") is True or facts.get("mutating") is True
    reusable_learning = facts.get("reusableLearning") is True
    text = input_text if isinstance(input_text, str) else facts.get("inputText")
    text = text if isinstance(text, str) else ""

    if intent in SIMPLE_INTENTS and not actionable and not reusable_learning and complexity in {"", "lightweight", "small", "low"}:
        return {
            "policyVersion": LEARNING_POLICY_VERSION,
            "decision": "SKIP",
            "reason": "simple or informational input",
            "signals": [],
        }

    signals: list[str] = []
    if intent in REQUIRED_INTENTS:
        signals.append(intent)
    if complexity in {"normal", "coupled", "complex", "medium", "high", "critical"}:
        signals.append(complexity)
    if route in {"full_safety", "runtime_validation", "review_only", "direct_work"}:
        signals.append(route)
    if actionable:
        signals.append("actionable")
    if reusable_learning:
        signals.append("reusable_learning")
    if intent not in SIMPLE_INTENTS and intent not in REQUIRED_INTENTS:
        for label, pattern in LEARNING_KEYWORDS:
            if pattern.search(text):
                signals.append(label)

    if signals:
        return {
            "policyVersion": LEARNING_POLICY_VERSION,
            "decision": "REQUIRED",
            "reason": "actionable issue, bug, debug, or complex work",
            "signals": sorted(set(signals)),
        }
    return {
        "policyVersion": LEARNING_POLICY_VERSION,
        "decision": "SKIP",
        "reason": "unclassified lightweight input",
        "signals": [],
    }


def default_store_path(*, memory_root: str | Path | None = None) -> Path:
    return resolve_storage_paths(memory_root=memory_root).learning_store


def _empty_document(now: datetime | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": STORE_SCHEMA_VERSION,
        "updatedAt": _iso(now or _now()),
        "records": [],
    }


def _validate_document(document: Any) -> tuple[bool, list[str]]:
    if not isinstance(document, Mapping):
        return False, ["learning store: expected object"]
    errors: list[str] = []
    if document.get("schemaVersion") != STORE_SCHEMA_VERSION:
        errors.append(f"learning store.schemaVersion: expected {STORE_SCHEMA_VERSION}")
    records = document.get("records")
    if not isinstance(records, list):
        errors.append("learning store.records: expected array")
        return False, errors
    if len(records) > MAX_RECORDS:
        errors.append(f"learning store.records: exceeds {MAX_RECORDS} records")
    for index, record in enumerate(records):
        checked = validate_lesson(record)
        if not checked["valid"]:
            errors.extend(f"records[{index}]: {error}" for error in checked["errors"])
    return not errors, errors


def load_learning_store(path: str | Path | None = None) -> dict[str, Any]:
    store_path = Path(path).expanduser() if path is not None else default_store_path()
    if not store_path.exists():
        return {"valid": True, "document": _empty_document(), "errors": [], "path": str(store_path)}
    try:
        document = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {"valid": False, "document": None, "errors": [f"learning store cannot be read: {type(error).__name__}"], "path": str(store_path)}
    valid, errors = _validate_document(document)
    return {"valid": valid, "document": deepcopy(document) if valid else None, "errors": errors, "path": str(store_path)}


@contextmanager
def _store_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    lock_path = path.with_name(f".{path.name}.lock")
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            lock_path.chmod(0o600)
        except OSError:
            pass
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_document(path: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    durability_error: str | None = None
    committed = False
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
        committed = True
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            durability_error = f"learning store directory fsync failed ({type(error).__name__})"
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError as error:
                if committed and durability_error is None:
                    durability_error = f"learning store temporary cleanup failed ({type(error).__name__})"
                elif not committed:
                    raise
    return {"committed": committed, "durable": committed and durability_error is None, "error": durability_error}


def _record_from_payload(payload: Mapping[str, Any], *, now: datetime) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    project_id, error = _text(payload.get("projectId"), field="projectId", required=True)
    if error:
        errors.append(error)
    repository, error = _text(payload.get("repository"), field="repository", required=True)
    if error:
        errors.append(error)
    branch_or_tree, error = _text(payload.get("branchOrTree"), field="branchOrTree", required=True)
    if error:
        errors.append(error)
    claim_value = payload.get("claim") or payload.get("learningClaim") or payload.get("lesson")
    claim, error = _text(claim_value, field="claim", required=True)
    if error:
        errors.append(error)
    task_id, error = _text(payload.get("taskId"), field="taskId", required=True)
    if error:
        errors.append(error)
    access_scope, scope_errors = _safe_list(payload.get("accessScope"), field="accessScope", required=True)
    errors.extend(scope_errors)
    files, file_errors = _safe_relative_paths(payload.get("filesInvolved", []), field="filesInvolved")
    errors.extend(file_errors)
    intent = _normal(payload.get("intent") or payload.get("requestType") or payload.get("taskType"))
    task_type, task_type_error = _text(payload.get("taskType") or intent or "engineering", field="taskType", required=True)
    if task_type_error:
        errors.append(task_type_error)
    fingerprints, fingerprint_errors = _safe_fingerprints(payload.get("fingerprints"), task_type=task_type)
    errors.extend(fingerprint_errors)
    kind, kind_error = _safe_kind(payload.get("kind"), intent)
    if kind_error:
        errors.append(kind_error)
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence: expected non-empty evidence array")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, Mapping):
                continue
            if _text(item.get("repository"), field="evidence.repository")[0] != repository:
                errors.append(f"evidence[{index}].repository: must match repository")
            if _text(item.get("commitOrTree"), field="evidence.commitOrTree")[0] != branch_or_tree:
                errors.append(f"evidence[{index}].commitOrTree: must match branchOrTree")
            evidence_path = _text(item.get("path"), field="evidence.path")[0].replace("\\", "/")
            parsed_path = PurePosixPath(evidence_path)
            if not evidence_path or parsed_path.is_absolute() or ".." in parsed_path.parts:
                errors.append(f"evidence[{index}].path: expected repository-relative path")

    optional_text: dict[str, str] = {}
    for key in ("businessLogic", "fixStrategy"):
        if payload.get(key) is not None:
            text, text_error = _text(payload.get(key), field=key, required=False)
            if text_error:
                errors.append(text_error)
            elif text:
                optional_text[key] = text

    tenant_id: str | None = None
    if payload.get("tenantId") is not None:
        tenant_id, tenant_error = _text(payload.get("tenantId"), field="tenantId", required=True)
        if tenant_error:
            errors.append(tenant_error)

    if errors:
        return None, sorted(set(errors))

    task_status = _normal(payload.get("status") or "completed")
    # Automatic capture is durable but never silently promotes a lesson. The
    # existing RAG contract requires explicit promotion authority/reviewer
    # evidence for a verified record; all automatic terminal write-backs start
    # as candidates and remain retrievable for later revalidation.
    status = "candidate"
    confidence = _normal(payload.get("confidence"))
    if confidence not in CONFIDENCE_RANK:
        confidence = "high" if status == "verified" else "medium"
    observed = _iso(now)
    canonical_key = _canonical_key(payload.get("canonicalKey"), payload=payload, claim=claim)
    record: dict[str, Any] = {
        "schemaVersion": "rag-record-v1",
        "id": "pending",
        "claim": claim,
        "kind": kind,
        "scope": "repo",
        "memoryTier": "dynamic_durable",
        "projectId": project_id,
        "repository": repository,
        "branchOrTree": branch_or_tree,
        "accessScope": access_scope,
        "fingerprints": fingerprints,
        "status": status,
        "confidence": confidence,
        "canonicalKey": canonical_key,
        "contentHash": "pending",
        "evidence": deepcopy(evidence),
        "observedAt": observed,
        "lastVerifiedAt": observed if status == "verified" else None,
        "reviewAt": _iso(now + timedelta(days=30)),
        "expiresAt": _iso(now + timedelta(days=180)),
        "contradictionGroup": None,
        "sensitivity": "none",
    }
    if files:
        record["filesInvolved"] = files
    if tenant_id:
        record["tenantId"] = tenant_id
    record.update(optional_text)
    record["contentHash"] = _content_hash(record)
    record["id"] = "lesson-" + hashlib.sha256(
        f"{project_id}|{repository}|{canonical_key}|{record['contentHash']}".encode("utf-8")
    ).hexdigest()[:24]
    checked = validate_lesson(record)
    if not checked["valid"]:
        return None, checked["errors"]
    return checked["record"], []


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:-]+", value.casefold()))


def _lesson_scope_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    access_scope = record.get("accessScope", [])
    normalized_scope = tuple(sorted(value for value in access_scope if isinstance(value, str))) if isinstance(access_scope, list) else ()
    return (
        record.get("projectId"),
        record.get("repository"),
        record.get("branchOrTree"),
        record.get("tenantId"),
        normalized_scope,
        record.get("memoryTier"),
        record.get("scope"),
    )


def _record_is_matchable(record: Mapping[str, Any], *, now: datetime) -> bool:
    if record.get("status") not in RETRIEVABLE_STATUSES:
        return False
    expires_at = record.get("expiresAt")
    if isinstance(expires_at, str):
        try:
            if _now(expires_at) <= now:
                return False
        except ValueError:
            return False
    return True


def _score_record(
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
    lesson: Mapping[str, Any] | None = None,
    *,
    now: datetime,
) -> int:
    if not _record_is_matchable(record, now=now):
        return 0
    if record.get("projectId") != payload.get("projectId") or record.get("repository") != payload.get("repository"):
        return 0
    candidate = lesson or {}
    if lesson is not None and _lesson_scope_key(record) != _lesson_scope_key(candidate):
        return 0
    score = 0
    if record.get("canonicalKey") and record.get("canonicalKey") == candidate.get("canonicalKey"):
        score += 100
    existing_fingerprints = record.get("fingerprints", {})
    candidate_fingerprints = candidate.get("fingerprints", {})
    for key, value in candidate_fingerprints.items():
        if value and existing_fingerprints.get(key) == value:
            score += 20
    existing_terms = _tokens(" ".join([record.get("claim", ""), *existing_fingerprints.values()]))
    candidate_terms = _tokens(" ".join([candidate.get("claim", ""), *candidate_fingerprints.values()]))
    score += min(len(existing_terms & candidate_terms), 10)
    return score


def _strong_relation(record: Mapping[str, Any], lesson: Mapping[str, Any], score: int) -> bool:
    """Require an identity match before mutating a durable lesson."""

    if score >= 100 and record.get("canonicalKey") == lesson.get("canonicalKey"):
        return True
    existing = record.get("fingerprints", {})
    incoming = lesson.get("fingerprints", {})
    strong_matches = sum(
        key in STRONG_FINGERPRINT_KEYS and existing.get(key) == value
        for key, value in incoming.items()
        if value
    )
    if strong_matches:
        return True
    path_matches = existing.get("path") and existing.get("path") == incoming.get("path")
    symbol_matches = existing.get("symbol") and existing.get("symbol") == incoming.get("symbol")
    return bool(path_matches and symbol_matches)


def _related_records(
    records: Iterable[Mapping[str, Any]],
    payload: Mapping[str, Any],
    lesson: Mapping[str, Any],
    *,
    now: datetime,
) -> list[tuple[int, Mapping[str, Any]]]:
    scored = [
        (_score_record(record, payload, lesson, now=now), record)
        for record in records
        if isinstance(record, Mapping)
    ]
    return sorted(
        (
            (score, record)
            for score, record in scored
            if score > 0 and _strong_relation(record, lesson, score)
        ),
        key=lambda item: (-item[0], item[1].get("id", "")),
    )[:8]


def _summary(record: Mapping[str, Any], score: int) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "canonicalKey": record.get("canonicalKey"),
        "claim": record.get("claim"),
        "status": record.get("status"),
        "confidence": record.get("confidence"),
        "score": score,
        "lastVerifiedAt": record.get("lastVerifiedAt"),
    }


def find_related_knowledge(
    payload: Mapping[str, Any],
    *,
    store_path: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Retrieve a small, identity-scoped set before a new learning write."""

    loaded = load_learning_store(store_path)
    if not loaded["valid"]:
        return {"outcome": "blocked", "records": [], "recordIds": [], "errors": loaded["errors"], "path": loaded["path"]}
    lesson, errors = _record_from_payload(payload, now=_now(now))
    if errors or lesson is None:
        return {"outcome": "blocked", "records": [], "recordIds": [], "errors": errors, "path": loaded["path"]}
    related = _related_records(loaded["document"]["records"], payload, lesson, now=_now(now))
    return {
        "outcome": "matched" if related else "no_relevant_memory",
        "records": [_summary(record, score) for score, record in related[:5]],
        "recordIds": [record.get("id") for _, record in related[:5]],
        "errors": [],
        "path": loaded["path"],
    }


def _merge_unique(first: Iterable[Any], second: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in [*first, *second]:
        key = json.dumps(value, sort_keys=True, ensure_ascii=True)
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(value))
    return result


def _merge_text(existing: Any, incoming: Any) -> str | None:
    old, _ = _text(existing, field="existing")
    new, _ = _text(incoming, field="incoming")
    if not new:
        return old or None
    if not old or old == new:
        return new
    combined = f"{old} Additional evidence-backed context: {new}"
    return combined[:MAX_TEXT]


def _merge_record(existing: Mapping[str, Any], incoming: Mapping[str, Any], *, expanded: bool = False) -> dict[str, Any]:
    merged = deepcopy(existing)
    if expanded:
        merged["claim"] = _merge_text(existing.get("claim"), incoming.get("claim")) or existing.get("claim")
    else:
        merged["claim"] = incoming.get("claim", existing.get("claim"))
    merged["evidence"] = _merge_unique(existing.get("evidence", []), incoming.get("evidence", []))
    merged["filesInvolved"] = _merge_unique(existing.get("filesInvolved", []), incoming.get("filesInvolved", []))
    merged["relatedRecords"] = _merge_unique(existing.get("relatedRecords", []), incoming.get("relatedRecords", []))
    merged["fingerprints"] = {**existing.get("fingerprints", {}), **incoming.get("fingerprints", {})}
    for key in ("businessLogic", "fixStrategy"):
        value = _merge_text(existing.get(key), incoming.get(key))
        if value:
            merged[key] = value
    if STATUS_RANK.get(incoming.get("status"), 0) >= STATUS_RANK.get(existing.get("status"), 0):
        merged["status"] = incoming.get("status", existing.get("status"))
    if CONFIDENCE_RANK.get(incoming.get("confidence"), 0) >= CONFIDENCE_RANK.get(existing.get("confidence"), 0):
        merged["confidence"] = incoming.get("confidence", existing.get("confidence"))
    merged["lastVerifiedAt"] = incoming.get("lastVerifiedAt") or existing.get("lastVerifiedAt")
    merged["reviewAt"] = incoming.get("reviewAt")
    merged["expiresAt"] = incoming.get("expiresAt")
    merged["contentHash"] = _content_hash(merged)
    checked = validate_lesson(merged)
    if not checked["valid"]:
        raise ValueError("merged lesson failed validation: " + "; ".join(checked["errors"]))
    return checked["record"]


def _empty_module_writeback(outcome: str = "not_required") -> dict[str, Any]:
    return {
        "schemaVersion": "module-feature-memory-writeback-v1",
        "outcome": outcome,
        "taskId": None,
        "recordId": None,
        "route": None,
        "scopeBinding": None,
        "notePath": None,
        "noteRelativePath": None,
        "canonicalFilesChanged": False,
        "noClobber": True,
        "ingestionRequired": False,
        "generatedAt": None,
        "errors": [],
        "sideEffects": [],
    }


def _blocked_result(classification: Mapping[str, Any], *, task_id: str | None, path: Path, errors: list[str]) -> dict[str, Any]:
    return {
        "schemaVersion": "learning-writeback-receipt-v1",
        "policyVersion": LEARNING_POLICY_VERSION,
        "decision": classification.get("decision", "REQUIRED"),
        "outcome": "blocked",
        "taskId": task_id,
        "recordId": None,
        "matchedRecordIds": [],
        "storePath": str(path),
        "errors": sorted(set(errors)),
        "sideEffects": [],
        "moduleFeatureWriteback": _empty_module_writeback(),
    }


def record_task_learning(
    payload: Mapping[str, Any],
    *,
    store_path: str | Path | None = None,
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Apply the learning policy and durably upsert a terminal task lesson."""

    classification = classify_input(payload, input_text=payload.get("inputText") if isinstance(payload, Mapping) else None)
    # Keep the configured lexical absolute path in receipts. Security checks
    # continue to use canonical paths at their filesystem boundaries.
    path = resolve_storage_paths(memory_root=memory_root, learning_store=store_path).learning_store.absolute()
    task_id = payload.get("taskId") if isinstance(payload, Mapping) else None
    if classification["decision"] == "SKIP":
        return {
            "schemaVersion": "learning-writeback-receipt-v1",
            "policyVersion": LEARNING_POLICY_VERSION,
            "decision": "SKIP",
            "outcome": "skipped_simple",
            "taskId": task_id,
            "recordId": None,
            "matchedRecordIds": [],
            "storePath": str(path),
            "errors": [],
            "sideEffects": [],
            "moduleFeatureWriteback": _empty_module_writeback(),
        }
    if fcntl is None:
        return _blocked_result(
            classification,
            task_id=task_id if isinstance(task_id, str) else None,
            path=path,
            errors=["durable learning requires advisory file locking on this host"],
        )
    if not isinstance(payload, Mapping):
        return _blocked_result(classification, task_id=None, path=path, errors=["task payload: expected object"])
    if payload.get("status") is None:
        return _blocked_result(
            classification,
            task_id=task_id if isinstance(task_id, str) else None,
            path=path,
            errors=["status: explicit completed, blocked, or paused state is required"],
        )
    task_status = _normal(payload.get("status"))
    if task_status in ACTIVE_TASK_STATES:
        return {
            **_blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=["learning waits for a completed, blocked, or paused task"]),
            "outcome": "pending",
            "sideEffects": [],
        }
    if task_status not in TERMINAL_TASK_STATES:
        return _blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=["status: expected completed, blocked, or paused"])

    current = _now(now)
    lesson, errors = _record_from_payload(payload, now=current)
    if errors or lesson is None:
        return _blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=errors or ["lesson: could not be built"])

    with _store_lock(path):
        loaded = load_learning_store(path)
        if not loaded["valid"]:
            return _blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=loaded["errors"])
        document = loaded["document"]
        records: list[dict[str, Any]] = document["records"]
        related = _related_records(records, payload, lesson, now=current)
        matched_ids = [record.get("id") for _, record in related[:5]]
        exact = next(
            (
                record
                for score, record in related
                if score >= 100 and record.get("canonicalKey") == lesson.get("canonicalKey") and record.get("status") not in {"superseded", "rejected", "quarantined"}
            ),
            None,
        )
        persisted_record: dict[str, Any]
        if exact is not None:
            if _normal(exact.get("claim")) == _normal(lesson.get("claim")):
                updated = _merge_record(exact, lesson)
                records[records.index(exact)] = updated
                action = "updated"
                update_kind = "same_claim_merge"
                record_id = updated["id"]
                persisted_record = updated
            else:
                replacement = deepcopy(lesson)
                replacement["supersedes"] = exact["id"]
                exact["status"] = "superseded"
                exact["relatedRecords"] = _merge_unique(exact.get("relatedRecords", []), [replacement["id"]])
                exact["contentHash"] = _content_hash(exact)
                records.append(replacement)
                action = "updated"
                update_kind = "superseded_and_created"
                record_id = replacement["id"]
                persisted_record = replacement
        elif related and related[0][0] >= 20 and _strong_relation(related[0][1], lesson, related[0][0]):
            base = related[0][1]
            expanded = _merge_record(base, lesson, expanded=True)
            records[records.index(base)] = expanded
            action = "updated"
            update_kind = "evidence_expansion"
            record_id = expanded["id"]
            persisted_record = expanded
        else:
            if len(records) >= MAX_RECORDS:
                return _blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=[f"learning store capacity reached: {MAX_RECORDS}"])
            records.append(lesson)
            action = "saved" if lesson["status"] == "verified" else "candidate_pending_review"
            update_kind = "created"
            record_id = lesson["id"]
            persisted_record = lesson
        document["updatedAt"] = _iso(current)
        valid, validation_errors = _validate_document(document)
        if not valid:
            return _blocked_result(classification, task_id=task_id if isinstance(task_id, str) else None, path=path, errors=validation_errors)
        try:
            write_result = _write_document(path, document)
        except (OSError, UnicodeError) as error:
            return _blocked_result(
                classification,
                task_id=task_id if isinstance(task_id, str) else None,
                path=path,
                errors=[f"learning store write failed ({type(error).__name__})"],
            )
        if not write_result["durable"]:
            return {
                "schemaVersion": "learning-writeback-receipt-v1",
                "policyVersion": LEARNING_POLICY_VERSION,
                "decision": "REQUIRED",
                "outcome": "partial",
                "storeOutcome": action,
                "updateKind": update_kind,
                "taskId": task_id,
                "recordId": record_id,
                "matchedRecordIds": matched_ids,
                "storePath": str(path),
                "errors": [write_result["error"] or "learning store durability confirmation failed"],
                "sideEffects": ["local durable learning store updated; durability confirmation failed"],
                "moduleFeatureWriteback": _empty_module_writeback(),
            }
        readback = load_learning_store(path)
        if not readback["valid"]:
            return {
                "schemaVersion": "learning-writeback-receipt-v1",
                "policyVersion": LEARNING_POLICY_VERSION,
                "decision": "REQUIRED",
                "outcome": "partial",
                "storeOutcome": action,
                "updateKind": update_kind,
                "taskId": task_id,
                "recordId": record_id,
                "matchedRecordIds": matched_ids,
                "storePath": str(path),
                "errors": ["post-write learning-store validation failed", *readback["errors"]],
                "sideEffects": ["local durable learning store updated; post-write validation failed"],
                "moduleFeatureWriteback": _empty_module_writeback(),
            }
        stored = next(
            (record for record in readback["document"]["records"] if record.get("id") == record_id),
            None,
        )
        if stored is None or stored.get("contentHash") != persisted_record.get("contentHash"):
            return {
                "schemaVersion": "learning-writeback-receipt-v1",
                "policyVersion": LEARNING_POLICY_VERSION,
                "decision": "REQUIRED",
                "outcome": "partial",
                "storeOutcome": action,
                "updateKind": update_kind,
                "taskId": task_id,
                "recordId": record_id,
                "matchedRecordIds": matched_ids,
                "storePath": str(path),
                "errors": ["post-write learning-store receipt does not match persisted record"],
                "sideEffects": ["local durable learning store updated; receipt verification failed"],
                "moduleFeatureWriteback": _empty_module_writeback(),
            }

    approved_memory_root: Path | None = None
    if memory_root is not None:
        approved_memory_root = Path(memory_root).expanduser()
    elif store_path is None:
        approved_memory_root = default_memory_root()
    module_feature_writeback: dict[str, Any]
    if approved_memory_root is None:
        module_feature_writeback = _empty_module_writeback("not_configured")
    else:
        module_feature_writeback = queue_module_feature_update(
            persisted_record,
            task_id=str(task_id),
            route=derive_module_feature_route(payload, persisted_record),
            memory_root=approved_memory_root,
            now=current,
        )

    side_effects = ["local durable learning store updated"]
    side_effects.extend(module_feature_writeback.get("sideEffects", []))

    failed_module_writeback = module_feature_writeback.get("outcome") in {"blocked", "conflict", "partial"}
    module_errors = [f"module/feature write-back: {error}" for error in module_feature_writeback.get("errors", [])]
    return {
        "schemaVersion": "learning-writeback-receipt-v1",
        "policyVersion": LEARNING_POLICY_VERSION,
        "decision": "REQUIRED",
        "outcome": "partial" if failed_module_writeback else action,
        **({"storeOutcome": action} if failed_module_writeback else {}),
        "updateKind": update_kind,
        "taskId": task_id,
        "recordId": record_id,
        "matchedRecordIds": matched_ids,
        "storePath": str(path),
        "retrieval": {"matched": bool(matched_ids), "count": len(matched_ids), "maxRecords": 5},
        "errors": module_errors,
        "sideEffects": side_effects,
        "moduleFeatureWriteback": module_feature_writeback,
    }


def _read_payload(path: Path) -> Mapping[str, Any]:
    if str(path) == "-":
        import sys

        value = json.load(sys.stdin)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("input JSON must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True, help="JSON task closure payload")
    parser.add_argument("--store", type=Path, default=None, help="Optional durable learning store path")
    parser.add_argument("--memory-root", type=Path, default=None, help="Approved memory root for the learning store and note queue")
    parser.add_argument("--now", default=None, help="Optional ISO-8601 time for deterministic operation")
    args = parser.parse_args(argv)
    try:
        selected_store = resolve_storage_paths(memory_root=args.memory_root, learning_store=args.store).learning_store
    except ValueError as error:
        parser.error(str(error))
    try:
        payload = _read_payload(args.input_json)
        result = record_task_learning(payload, store_path=args.store, memory_root=args.memory_root, now=args.now)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        result = {
            "schemaVersion": "learning-writeback-receipt-v1",
            "policyVersion": LEARNING_POLICY_VERSION,
            "decision": "REQUIRED",
            "outcome": "blocked",
            "taskId": None,
            "recordId": None,
            "matchedRecordIds": [],
            "storePath": str(selected_store),
            "errors": [f"input: {type(error).__name__}"],
            "sideEffects": [],
            "moduleFeatureWriteback": _empty_module_writeback(),
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    module_outcome = result.get("moduleFeatureWriteback", {}).get("outcome")
    return 0 if result["outcome"] not in {"blocked", "pending", "partial"} and module_outcome not in {"blocked", "conflict", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
