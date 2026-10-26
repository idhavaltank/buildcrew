#!/usr/bin/env python3
"""Safe, external, authority-free cross-session handoff contracts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .context_state import ContextStateError, validate_state_root


SCHEMA_VERSION = "session-handoff-v1"
DEFAULT_TTL_HOURS = 72
MAX_TTL_HOURS = 168
MAX_PACKET_BYTES = 256 * 1024
MAX_RENDER_BYTES = 32 * 1024
MAX_DISCOVERY_PACKETS = 500
MAX_RESULTS = 5

FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|token|access[_-]?token|refresh[_-]?token|authorization|cookie)\s*[:=]\s*\S+"
)
RAW_CREDENTIAL_RE = re.compile(
    r"(?ix)(?:"
    r"\bbearer\s+[a-z0-9._~+/=-]{8,}"
    r"|\beyj[a-z0-9_-]{5,}\.[a-z0-9_-]{5,}\.[a-z0-9_-]{5,}\b"
    r"|\bgh(?:p|o|u|s|r)_[a-z0-9]{20,}\b"
    r"|\bgithub_pat_[a-z0-9_]{20,}\b"
    r"|\bsk-proj-[a-z0-9_-]{20,}\b"
    r"|\bsk-(?!live_)[a-z0-9_-]{20,}\b"
    r"|\bxox(?:b|p|a|r|s)-[a-z0-9-]{20,}\b"
    r"|\b(?:sk|rk)_live_[a-z0-9]{16,}\b"
    r"|\baiza[0-9a-z_-]{30,}\b"
    r"|\bakia[0-9a-z]{16}\b"
    r"|-----begin\s+[a-z0-9 ]*private\s+key-----"
    r")"
)
AUTHORITY_INSTRUCTION_RE = re.compile(
    r"(?i)(?:\b(?:owns?|assume|grant(?:ed)?|transfer(?:red)?)\b.{0,48}"
    r"\b(?:writer\s+lease|authority|ownership)\b|"
    r"\bmay\s+(?:activate|deploy|promote|publish|write|mutate)\b)"
)
FORBIDDEN_KEYS = {
    "rawprompt",
    "prompt",
    "rawtranscript",
    "transcript",
    "rawcommand",
    "command",
    "credentials",
    "token",
    "cookie",
    "secret",
    "writerlease",
    "modelassignment",
    "activationapproval",
    "reviewerauthority",
    "lifecycleownership",
    "hostauthority",
}

TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "handoffId",
    "taskId",
    "sourceSessionId",
    "repository",
    "task",
    "source",
    "phase",
    "completedWork",
    "decisions",
    "findings",
    "verification",
    "blockers",
    "residualRisk",
    "nextAction",
    "privacyReceipt",
    "createdAt",
    "expiresAt",
    "status",
    "handoffMode",
    "requiresRevalidation",
}
HANDOFF_CONTEXT_FIELDS = {
    "handoffId",
    "taskId",
    "sourceSessionId",
    "repository",
    "task",
    "source",
    "phase",
    "completedWork",
    "decisions",
    "findings",
    "verification",
    "blockers",
    "residualRisk",
    "nextAction",
}
REPOSITORY_FIELDS = {"projectId", "repositoryId", "worktreeId", "branch", "head", "sourceRef"}
TASK_FIELDS = {
    "objective",
    "route",
    "risk",
    "acceptanceCriteria",
    "acceptanceFingerprint",
    "policyVersion",
    "planVersion",
}
SOURCE_FIELDS = {
    "sourceFingerprint",
    "overallDiffFingerprint",
    "dirty",
    "dirtyFingerprint",
    "changedPaths",
}
PHASE_FIELDS = {"current", "next"}
NEXT_ACTION_FIELDS = {"objective", "readScope", "writeScope", "checks", "stopConditions"}
PRIVACY_PROVENANCE_FIELDS = {
    "redactorIdentity",
    "redactionPolicyVersion",
    "scannerVersion",
    "redactionProvenance",
}
PRIVACY_RECEIPT_FIELDS = {
    "schemaVersion",
    "receiptId",
    "receiptDigest",
    "handoffId",
    "taskId",
    "sourceSessionId",
    "contentDigest",
    "redactorIdentity",
    "redactionPolicyVersion",
    "scannerVersion",
    "redactionProvenance",
    "secretScanStatus",
    "authorityScanStatus",
    "issuedAt",
    "terminal",
}
VERIFICATION_FIELDS = {"check", "status", "evidenceRef"}


class HandoffContractError(ValueError):
    """Raised when handoff persistence or construction fails closed."""


def _parse_time(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict(value: Any, fields: set[str], label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: expected object")
        return None
    errors.extend(f"{label}: missing required field {key}" for key in sorted(fields - set(value)))
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - fields))
    return value


def _string(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: expected non-empty string")
    elif any(ord(character) < 32 and character not in "\t" for character in value):
        errors.append(f"{label}: control characters are not allowed")


def _strings(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label}: expected non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate values are not allowed")
    for index, item in enumerate(value):
        _string(item, f"{label}[{index}]", errors)
    return list(value)


def _safe_relative(path: str) -> bool:
    if "\\" in path or any(ord(character) < 32 for character in path):
        return False
    parsed = PurePosixPath(path)
    return bool(path) and not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def _logical_reference(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    stripped = value.strip()
    if stripped.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[/\\]", stripped):
        return False
    return not stripped.casefold().startswith("file:")


def _paths(value: Any, label: str, errors: list[str]) -> list[str]:
    paths = _strings(value, label, errors)
    folded: set[str] = set()
    for path in paths:
        if not _safe_relative(path):
            errors.append(f"{label}: unsafe relative path {path!r}")
        if path.casefold() in folded:
            errors.append(f"{label}: case-colliding path {path!r}")
        folded.add(path.casefold())
    return paths


def _scan_sensitive(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in FORBIDDEN_KEYS:
                errors.append(f"{label}.{key}: forbidden sensitive or authority-transfer field")
            _scan_sensitive(item, f"{label}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_sensitive(item, f"{label}[{index}]", errors)
    elif isinstance(value, str):
        if SECRET_RE.search(value) or RAW_CREDENTIAL_RE.search(value):
            errors.append(f"{label}: secret-like content is forbidden")
        free_text_markers = (
            ".objective",
            ".acceptanceCriteria[",
            ".completedWork[",
            ".decisions[",
            ".findings[",
            ".blockers[",
            ".residualRisk[",
            ".verification[",
            ".checks[",
            ".stopConditions[",
        )
        if any(marker in label for marker in free_text_markers):
            for token in re.findall(r"[A-Za-z0-9_-]{20,}", value):
                frequencies = {character: token.count(character) for character in set(token)}
                entropy = -sum(
                    (count / len(token)) * math.log2(count / len(token))
                    for count in frequencies.values()
                )
                if (
                    entropy >= 3.5
                    and re.search(r"[A-Za-z]", token)
                    and re.search(r"[0-9]", token)
                ):
                    errors.append(f"{label}: high-entropy credential-like token is forbidden")
                    break
        if AUTHORITY_INSTRUCTION_RE.search(value):
            errors.append(f"{label}: authority-transfer instruction is forbidden")
        if "\n" in value or "\r" in value:
            errors.append(f"{label}: multiline raw content is forbidden")


def _canonical_digest(domain: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(f"{domain}\0".encode("utf-8") + encoded).hexdigest()


def compute_handoff_content_digest(context: Mapping[str, Any]) -> str:
    return _canonical_digest("session-handoff-redacted-content-v1", context)


def _privacy_receipt_body(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: receipt.get(key)
        for key in sorted(PRIVACY_RECEIPT_FIELDS - {"receiptId", "receiptDigest"})
    }


def compute_privacy_receipt_id(receipt: Mapping[str, Any]) -> str:
    return "handoff-privacy-" + _canonical_digest(
        "handoff-privacy-receipt-v1-id", _privacy_receipt_body(receipt)
    ).split(":", 1)[1][:40]


def compute_privacy_receipt_digest(receipt: Mapping[str, Any]) -> str:
    body = _privacy_receipt_body(receipt)
    body["receiptId"] = receipt.get("receiptId")
    return _canonical_digest("handoff-privacy-receipt-v1", body)


def validate_privacy_receipt(
    value: Any,
    *,
    context: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    receipt = _strict(value, PRIVACY_RECEIPT_FIELDS, "privacyReceipt", errors)
    if receipt is None:
        return {"valid": False, "errors": errors}
    if receipt.get("schemaVersion") != "handoff-privacy-receipt-v1":
        errors.append("privacyReceipt.schemaVersion: unsupported value")
    for key in (
        "receiptId",
        "handoffId",
        "taskId",
        "sourceSessionId",
        "redactorIdentity",
        "redactionPolicyVersion",
        "scannerVersion",
    ):
        _string(receipt.get(key), f"privacyReceipt.{key}", errors)
    for key in ("receiptDigest", "contentDigest"):
        if not isinstance(receipt.get(key), str) or not FINGERPRINT_RE.fullmatch(receipt[key]):
            errors.append(f"privacyReceipt.{key}: expected sha256 fingerprint")
    if receipt.get("redactionProvenance") != "CALLER_REDACTED":
        errors.append("privacyReceipt.redactionProvenance: expected CALLER_REDACTED")
    if receipt.get("secretScanStatus") != "PASS":
        errors.append("privacyReceipt.secretScanStatus: expected PASS")
    if receipt.get("authorityScanStatus") != "PASS":
        errors.append("privacyReceipt.authorityScanStatus: expected PASS")
    issued = _parse_time(receipt.get("issuedAt"), "privacyReceipt.issuedAt", errors)
    current = _parse_time(now, "now", errors) if now is not None else None
    if issued is not None and current is not None and issued > current:
        errors.append("privacyReceipt.issuedAt: is in the future")
    if receipt.get("terminal") is not True:
        errors.append("privacyReceipt.terminal: must be exactly true")
    if receipt.get("contentDigest") != compute_handoff_content_digest(context):
        errors.append("privacyReceipt.contentDigest: does not match redacted content")
    for key in ("handoffId", "taskId", "sourceSessionId"):
        if receipt.get(key) != context.get(key):
            errors.append(f"privacyReceipt.{key}: does not match handoff content")
    if receipt.get("receiptId") != compute_privacy_receipt_id(receipt):
        errors.append("privacyReceipt.receiptId: does not match content")
    if receipt.get("receiptDigest") != compute_privacy_receipt_digest(receipt):
        errors.append("privacyReceipt.receiptDigest: does not match content")
    return {"valid": not errors, "errors": errors}


def compute_acceptance_fingerprint(criteria: Sequence[str]) -> str:
    """Bind the exact ordered acceptance criteria to one canonical digest."""

    if (
        not isinstance(criteria, list)
        or not criteria
        or any(not isinstance(item, str) or not item.strip() for item in criteria)
        or len(criteria) != len(set(criteria))
    ):
        raise HandoffContractError("acceptance criteria must be a non-empty unique string array")
    canonical = json.dumps(criteria, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(
        f"session-handoff-acceptance-v1\0{canonical}".encode("utf-8")
    ).hexdigest()


def build_handoff_packet(
    context: Mapping[str, Any],
    *,
    privacy_provenance: Mapping[str, Any],
    created_at: str | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    status: str = "READY",
    handoff_mode: str = "PAUSE",
) -> dict[str, Any]:
    """Build a redacted packet shape; callers must still validate before use."""

    if not isinstance(context, Mapping):
        raise HandoffContractError("context: expected object")
    required = {
        "handoffId",
        "taskId",
        "sourceSessionId",
        "repository",
        "task",
        "source",
        "phase",
        "completedWork",
        "decisions",
        "findings",
        "verification",
        "blockers",
        "residualRisk",
        "nextAction",
    }
    if set(context) != required:
        raise HandoffContractError("context: unknown or missing fields")
    errors: list[str] = []
    provenance = _strict(
        privacy_provenance,
        PRIVACY_PROVENANCE_FIELDS,
        "privacyProvenance",
        errors,
    )
    if provenance is not None:
        for key in ("redactorIdentity", "redactionPolicyVersion", "scannerVersion"):
            _string(provenance.get(key), f"privacyProvenance.{key}", errors)
        if provenance.get("redactionProvenance") != "CALLER_REDACTED":
            errors.append(
                "privacyProvenance.redactionProvenance: expected CALLER_REDACTED"
            )
    _scan_sensitive(context, "handoffContent", errors)
    created = _parse_time(created_at or _iso(datetime.now(timezone.utc)), "createdAt", errors)
    if created is None:
        raise HandoffContractError("; ".join(errors))
    if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or ttl_hours < 1:
        raise HandoffContractError("ttl_hours: expected positive integer")
    packet = json.loads(json.dumps(context, sort_keys=True))
    privacy_receipt = {
        "schemaVersion": "handoff-privacy-receipt-v1",
        "receiptId": "pending",
        "receiptDigest": "sha256:" + "0" * 64,
        "handoffId": packet["handoffId"],
        "taskId": packet["taskId"],
        "sourceSessionId": packet["sourceSessionId"],
        "contentDigest": compute_handoff_content_digest(packet),
        "redactorIdentity": provenance.get("redactorIdentity") if provenance else None,
        "redactionPolicyVersion": provenance.get("redactionPolicyVersion") if provenance else None,
        "scannerVersion": provenance.get("scannerVersion") if provenance else None,
        "redactionProvenance": provenance.get("redactionProvenance") if provenance else None,
        "secretScanStatus": "PASS",
        "authorityScanStatus": "PASS",
        "issuedAt": _iso(created),
        "terminal": True,
    }
    privacy_receipt["receiptId"] = compute_privacy_receipt_id(privacy_receipt)
    privacy_receipt["receiptDigest"] = compute_privacy_receipt_digest(privacy_receipt)
    packet.update(
        {
            "schemaVersion": SCHEMA_VERSION,
            "privacyReceipt": privacy_receipt,
            "createdAt": _iso(created),
            "expiresAt": _iso(created + timedelta(hours=ttl_hours)),
            "status": status,
            "handoffMode": handoff_mode,
            "requiresRevalidation": True,
        }
    )
    if errors:
        raise HandoffContractError("; ".join(errors))
    validation = validate_handoff_packet(packet, now=_iso(created))
    if not validation["valid"]:
        raise HandoffContractError("; ".join(validation["errors"]))
    return packet


def validate_handoff_packet(value: Any, *, now: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    packet = _strict(value, TOP_LEVEL_FIELDS, "handoff", errors)
    if packet is None:
        return {"valid": False, "expired": False, "errors": errors}
    if packet.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"handoff.schemaVersion: expected {SCHEMA_VERSION}")
    for key in ("handoffId", "taskId", "sourceSessionId"):
        _string(packet.get(key), f"handoff.{key}", errors)
    if isinstance(packet.get("handoffId"), str) and not SAFE_ID_RE.fullmatch(packet["handoffId"]):
        errors.append("handoff.handoffId: unsafe identifier")

    repository = _strict(packet.get("repository"), REPOSITORY_FIELDS, "handoff.repository", errors)
    if repository is not None:
        for key in REPOSITORY_FIELDS:
            _string(repository.get(key), f"handoff.repository.{key}", errors)
        for key in ("worktreeId", "sourceRef"):
            if not _logical_reference(repository.get(key)):
                errors.append(f"handoff.repository.{key}: expected logical redacted identifier")
    task = _strict(packet.get("task"), TASK_FIELDS, "handoff.task", errors)
    if task is not None:
        for key in ("objective", "route", "risk", "policyVersion", "planVersion"):
            _string(task.get(key), f"handoff.task.{key}", errors)
        acceptance_criteria = _strings(
            task.get("acceptanceCriteria"), "handoff.task.acceptanceCriteria", errors
        )
        if not isinstance(task.get("acceptanceFingerprint"), str) or not FINGERPRINT_RE.fullmatch(task["acceptanceFingerprint"]):
            errors.append("handoff.task.acceptanceFingerprint: expected sha256 fingerprint")
        elif acceptance_criteria:
            try:
                expected_acceptance = compute_acceptance_fingerprint(acceptance_criteria)
            except HandoffContractError as exc:
                errors.append(f"handoff.task.acceptanceCriteria: {exc}")
            else:
                if task.get("acceptanceFingerprint") != expected_acceptance:
                    errors.append(
                        "handoff.task.acceptanceFingerprint: does not match exact acceptance criteria"
                    )
    source = _strict(packet.get("source"), SOURCE_FIELDS, "handoff.source", errors)
    if source is not None:
        for key in ("sourceFingerprint", "overallDiffFingerprint", "dirtyFingerprint"):
            if not isinstance(source.get(key), str) or not FINGERPRINT_RE.fullmatch(source[key]):
                errors.append(f"handoff.source.{key}: expected sha256 fingerprint")
        if not isinstance(source.get("dirty"), bool):
            errors.append("handoff.source.dirty: expected boolean")
        changed_paths = _paths(source.get("changedPaths"), "handoff.source.changedPaths", errors)
        if changed_paths != sorted(changed_paths):
            errors.append("handoff.source.changedPaths: expected sorted canonical paths")
    phase = _strict(packet.get("phase"), PHASE_FIELDS, "handoff.phase", errors)
    if phase is not None:
        for key in PHASE_FIELDS:
            _string(phase.get(key), f"handoff.phase.{key}", errors)
    for key in ("completedWork", "decisions", "findings", "blockers", "residualRisk"):
        _strings(packet.get(key), f"handoff.{key}", errors)
    verification = packet.get("verification")
    if not isinstance(verification, list):
        errors.append("handoff.verification: expected array")
    else:
        for index, raw_row in enumerate(verification):
            row = _strict(raw_row, VERIFICATION_FIELDS, f"handoff.verification[{index}]", errors)
            if row is not None:
                for key in VERIFICATION_FIELDS:
                    _string(row.get(key), f"handoff.verification[{index}].{key}", errors)
    next_action = _strict(packet.get("nextAction"), NEXT_ACTION_FIELDS, "handoff.nextAction", errors)
    if next_action is not None:
        _string(next_action.get("objective"), "handoff.nextAction.objective", errors)
        for key in ("readScope", "writeScope"):
            _paths(next_action.get(key), f"handoff.nextAction.{key}", errors)
        for key in ("checks", "stopConditions"):
            _strings(next_action.get(key), f"handoff.nextAction.{key}", errors)
    redacted_context = {
        key: packet.get(key) for key in sorted(HANDOFF_CONTEXT_FIELDS)
    }
    privacy_result = validate_privacy_receipt(
        packet.get("privacyReceipt"), context=redacted_context, now=now
    )
    errors.extend(
        f"handoff.{error}" for error in privacy_result.get("errors", [])
    )
    if packet.get("status") not in {"READY", "BLOCKED"}:
        errors.append("handoff.status: expected READY or BLOCKED")
    if packet.get("handoffMode") not in {"PAUSE", "TRANSFER"}:
        errors.append("handoff.handoffMode: expected PAUSE or TRANSFER")
    if packet.get("requiresRevalidation") is not True:
        errors.append("handoff.requiresRevalidation: must be exactly true")

    created = _parse_time(packet.get("createdAt"), "handoff.createdAt", errors)
    expires = _parse_time(packet.get("expiresAt"), "handoff.expiresAt", errors)
    current = _parse_time(now, "now", errors) if now is not None else datetime.now(timezone.utc)
    expired = False
    if created is not None and expires is not None:
        lifetime = (expires - created).total_seconds()
        if lifetime <= 0:
            errors.append("handoff: expiresAt must be after createdAt")
        if lifetime > MAX_TTL_HOURS * 3600:
            errors.append(f"handoff: TTL exceeds {MAX_TTL_HOURS} hours")
        if current is not None and current < created:
            errors.append("handoff: createdAt is in the future")
        if current is not None and current >= expires:
            errors.append("handoff: expired")
            expired = True
    try:
        encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        errors.append("handoff: packet must be JSON-compatible")
    else:
        if len(encoded) > MAX_PACKET_BYTES:
            errors.append(f"handoff: packet exceeds {MAX_PACKET_BYTES} bytes")
    _scan_sensitive(packet, "handoff", errors)
    return {"valid": not errors, "expired": expired, "errors": errors}


def _expected_bindings(packet: Mapping[str, Any]) -> dict[str, Any]:
    repository = packet.get("repository", {})
    task = packet.get("task", {})
    source = packet.get("source", {})
    return {
        "taskId": packet.get("taskId"),
        "acceptanceCriteria": task.get("acceptanceCriteria"),
        "acceptanceFingerprint": task.get("acceptanceFingerprint"),
        "projectId": repository.get("projectId"),
        "repositoryId": repository.get("repositoryId"),
        "worktreeId": repository.get("worktreeId"),
        "branch": repository.get("branch"),
        "head": repository.get("head"),
        "sourceFingerprint": source.get("sourceFingerprint"),
        "overallDiffFingerprint": source.get("overallDiffFingerprint"),
        "dirty": source.get("dirty"),
        "dirtyFingerprint": source.get("dirtyFingerprint"),
        "changedPaths": source.get("changedPaths"),
        "planVersion": task.get("planVersion"),
        "requiresRevalidation": packet.get("requiresRevalidation"),
    }


def classify_handoff_attachment(
    packet: Mapping[str, Any],
    current_context: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    validation = validate_handoff_packet(packet, now=now)
    if not validation["valid"]:
        return {"classification": "REJECTED", "score": 0, "mismatches": list(validation["errors"])}
    if not isinstance(current_context, Mapping):
        return {"classification": "REJECTED", "score": 0, "mismatches": ["current context is unavailable"]}
    expected = _expected_bindings(packet)
    mismatches = sorted(key for key, value in expected.items() if current_context.get(key) != value)
    repository_mismatch = any(key in mismatches for key in ("projectId", "repositoryId"))
    if repository_mismatch:
        return {"classification": "REJECTED", "score": 0, "mismatches": mismatches}
    if not mismatches:
        return {"classification": "AUTO_ATTACH_EVIDENCE", "score": len(expected), "mismatches": []}
    score = sum(1 for key, value in expected.items() if current_context.get(key) == value)
    return {"classification": "CANDIDATE", "score": score, "mismatches": mismatches}


def _namespace(repository: Mapping[str, Any]) -> str:
    raw = f"{repository.get('projectId', '')}:{repository.get('repositoryId', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _external_root(
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    protected_roots: Sequence[str | os.PathLike[str]],
) -> Path:
    try:
        return validate_state_root(state_root, repository_root, protected_roots=protected_roots)
    except (ContextStateError, OSError) as exc:
        raise HandoffContractError(str(exc)) from exc


def _ensure_private_directory(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise HandoffContractError(f"handoff directory cannot be a symlink: {path.name}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700:
        raise HandoffContractError(f"handoff directory mode must be 0700, got {mode:o}")


def write_handoff_packet(
    packet: Mapping[str, Any],
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    *,
    now: str | None = None,
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Atomically write one private handoff under an explicit external root."""

    validation = validate_handoff_packet(packet, now=now)
    if not validation["valid"]:
        raise HandoffContractError("handoff is invalid: " + "; ".join(validation["errors"]))
    root = _external_root(state_root, repository_root, protected_roots)
    _ensure_private_directory(root)
    directory = root / _namespace(packet["repository"])
    _ensure_private_directory(directory)
    destination = directory / f"{packet['handoffId']}.json"
    if destination.exists():
        metadata = destination.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise HandoffContractError("existing handoff destination is not a private regular file")
    payload = (json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if len(payload) > MAX_PACKET_BYTES:
        raise HandoffContractError("handoff packet exceeds maximum size")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".handoff-", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
        directory_descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, HandoffContractError):
            raise
        raise HandoffContractError(f"handoff write failed ({type(exc).__name__})") from exc
    metadata = destination.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise HandoffContractError("handoff file failed private regular-file verification")
    return destination


def discover_handoff_packets(
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    current_context: Mapping[str, Any],
    *,
    now: str | None = None,
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    """Discover at most five ranked packets from a bounded explicit root."""

    root = _external_root(state_root, repository_root, protected_roots)
    repository = {
        "projectId": current_context.get("projectId"),
        "repositoryId": current_context.get("repositoryId"),
    }
    directory = root / _namespace(repository)
    if not directory.exists():
        return {"status": "COMPLETE", "eligibleCount": 0, "results": []}
    if directory.is_symlink() or not directory.is_dir():
        raise HandoffContractError("handoff namespace is not a safe directory")
    paths = sorted(directory.glob("*.json"))
    if len(paths) > MAX_DISCOVERY_PACKETS:
        return {
            "status": "MISSING_COVERAGE",
            "eligibleCount": len(paths),
            "results": [],
            "reason": f"packet count exceeds {MAX_DISCOVERY_PACKETS}",
        }
    results: list[dict[str, Any]] = []
    for path in paths:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600:
            continue
        try:
            if metadata.st_size > MAX_PACKET_BYTES:
                continue
            packet = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        decision = classify_handoff_attachment(packet, current_context, now=now)
        if decision["classification"] == "REJECTED":
            continue
        results.append(
            {
                "handoffId": packet.get("handoffId"),
                "taskId": packet.get("taskId"),
                "classification": decision["classification"],
                "score": decision["score"],
                "mismatches": decision["mismatches"],
                "createdAt": packet.get("createdAt"),
                "expiresAt": packet.get("expiresAt"),
            }
        )
    results.sort(
        key=lambda item: (
            item["classification"] != "AUTO_ATTACH_EVIDENCE",
            -item["score"],
            str(item["handoffId"]),
        )
    )
    return {"status": "COMPLETE", "eligibleCount": len(results), "results": results[:MAX_RESULTS]}


def render_handoff_summary(packet: Mapping[str, Any], *, now: str | None = None) -> str:
    validation = validate_handoff_packet(packet, now=now)
    if not validation["valid"]:
        raise HandoffContractError("cannot render invalid handoff")
    lines = [
        f"Task: {packet['taskId']}",
        f"Objective: {packet['task']['objective']}",
        f"Phase: {packet['phase']['current']} -> {packet['phase']['next']}",
        f"Next action: {packet['nextAction']['objective']}",
        f"Requires revalidation: {str(packet['requiresRevalidation']).lower()}",
        "Authority transferred: no",
    ]
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > MAX_RENDER_BYTES:
        raise HandoffContractError("rendered handoff exceeds maximum size")
    return rendered
