"""Automatic day-to-day continuity for the standalone AE skill.

This module connects the existing bounded memory lookup, durable learning,
daily worklog, and task identity rules into one resumable loop. It stores only
sanitized task facts and references; canonical memory files are never silently
rewritten.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PACKAGE_ROOT / "scripts"
import sys

if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from learning_runtime import record_task_learning  # noqa: E402
from memory_context_runtime import lookup_memory_context  # noqa: E402
from rag_contracts import privacy_findings  # noqa: E402
from storage_paths import resolve_storage_paths  # noqa: E402
from .task_packet import validate_task_packet


SESSION_SCHEMA_VERSION = "continuity-session-v1"
REPORT_SCHEMA_VERSION = "continuity-report-v1"
BUDGET_SCHEMA_VERSION = "context-budget-report-v1"
INDEX_SCHEMA_VERSION = "continuity-index-v1"
TARGET_REDUCTION_PERCENT = 70
PROFILE_LIMITS = {
    "LIGHTWEIGHT": {"maxChars": 4800, "maxSourceChars": 1800, "maxSources": 3},
    "NORMAL": {"maxChars": 8000, "maxSourceChars": 2600, "maxSources": 5},
    "COUPLED": {"maxChars": 14000, "maxSourceChars": 3600, "maxSources": 8},
}
TERMINAL_STATUSES = {"completed", "complete", "blocked", "paused", "pause"}
REDACTED_FIELDS = {
    "inputText",
    "rawRequest",
    "prompt",
    "prompts",
    "transcript",
    "commands",
    "credentials",
    "secrets",
    "tokens",
}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, *, max_length: int = 2000, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise ValueError("expected non-empty text")
        return ""
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        raise ValueError("expected non-empty text")
    if len(normalized) > max_length:
        raise ValueError(f"text exceeds {max_length} characters")
    if "\x00" in normalized:
        raise ValueError("NUL characters are not allowed")
    return normalized


def _slug(value: str, *, fallback: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug or fallback)[:64].rstrip("-")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _estimate_tokens(characters: int) -> int:
    return (max(0, characters) + 3) // 4


def default_state_root() -> Path:
    return resolve_storage_paths().continuity_root


def _path_inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_state_root(root: str | Path, *, protected_roots: list[str | Path] | None = None) -> Path:
    candidate = Path(root).expanduser()
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("continuity state root cannot be a symlink")
    canonical = candidate.resolve(strict=False)
    for protected in protected_roots or []:
        protected_path = Path(protected).expanduser().resolve(strict=False)
        if _path_inside(canonical, protected_path):
            raise ValueError(f"continuity state root overlaps protected path: {canonical}")
    # Use the canonical path for safety checks, but preserve the host's
    # lexical absolute path for state and receipt output. On macOS, resolve()
    # can rewrite /var paths to /private/var, which makes valid configured
    # paths appear different to callers and breaks cross-process continuity.
    return candidate.absolute()


@contextmanager
def _state_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    lock_path = root / ".continuity.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("continuity lock must be a single-link regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _session_path(root: Path, task_id: str) -> Path:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return root / "sessions" / f"{_slug(task_id)}-{digest}.json"


def _index_path(root: Path) -> Path:
    return root / "context-index.json"


def _strip_untrusted(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_untrusted(child)
            for key, child in value.items()
            if str(key) not in REDACTED_FIELDS
        }
    if isinstance(value, list):
        return [_strip_untrusted(child) for child in value]
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def _packet_fingerprint(packet: Mapping[str, Any], *, project_id: str, repository: str) -> str:
    identity = {
        "taskId": packet["taskId"],
        "projectId": project_id,
        "repository": repository,
        "objective": packet["objective"],
        "acceptanceCriteria": packet["acceptanceCriteria"],
        "sourceBinding": packet.get("sourceBinding"),
    }
    return _fingerprint(identity)


def normalize_packet(value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a portable packet while keeping raw input out of durable state."""

    if not isinstance(value, Mapping):
        raise ValueError("continuity input must be an object")
    raw = dict(value)
    objective = _text(
        raw.get("objective") or raw.get("normalizedObjective") or raw.get("inputText"),
        required=True,
    )
    source_binding = raw.get("sourceBinding") if isinstance(raw.get("sourceBinding"), Mapping) else {}
    project_id = _text(
        raw.get("projectId") or raw.get("project") or "general",
        max_length=500,
        required=True,
    )
    repository = _text(
        raw.get("repository") or source_binding.get("repository") or "unknown-repository",
        max_length=500,
        required=True,
    )
    acceptance = raw.get("acceptanceCriteria")
    if not isinstance(acceptance, list) or not acceptance:
        acceptance = ["Continue the requested work with current-source revalidation."]
    acceptance = [_text(item, max_length=500, required=True) for item in acceptance[:16]]
    request_type = _text(
        raw.get("requestType") or raw.get("taskType") or "IMPLEMENTATION",
        max_length=40,
    ).upper()
    allowed_request_types = {
        "QUESTION",
        "CONVERSATION",
        "STATUS",
        "VALIDATION",
        "REVIEW",
        "INVESTIGATION",
        "IMPLEMENTATION",
        "MIGRATION",
        "SECURITY",
        "DEPLOYMENT",
    }
    if request_type not in allowed_request_types:
        request_type = "IMPLEMENTATION"
    complexity = _text(
        raw.get("complexity") or raw.get("riskTier") or "NORMAL",
        max_length=20,
    ).upper()
    complexity = {
        "SMALL": "LIGHTWEIGHT",
        "LIGHT": "LIGHTWEIGHT",
        "COMPLEX": "COUPLED",
        "HIGH": "COUPLED",
    }.get(complexity, complexity)
    if complexity not in {"LIGHTWEIGHT", "NORMAL", "COUPLED"}:
        complexity = "NORMAL"
    actions = raw.get("allowedActions") if isinstance(raw.get("allowedActions"), Mapping) else {}
    write_allowed = bool(
        actions.get(
            "write",
            raw.get("write", request_type in {"IMPLEMENTATION", "MIGRATION", "SECURITY"}),
        )
    )
    external_allowed = bool(actions.get("external", raw.get("external", False)))
    required_capabilities = raw.get("requiredCapabilities")
    if not isinstance(required_capabilities, list) or not required_capabilities:
        required_capabilities = [
            {"capabilityId": "repo_read", "reason": "inspect the current implementation"}
        ]
        if write_allowed:
            required_capabilities.append(
                {"capabilityId": "repo_write", "reason": "apply the bounded change"}
            )
    required_evidence = (
        raw.get("requiredEvidence")
        if isinstance(raw.get("requiredEvidence"), list)
        else ["source"]
    )
    approval = (
        raw.get("approval")
        if isinstance(raw.get("approval"), Mapping)
        else {
            "status": "NOT_REQUIRED",
            "approvalId": None,
            "reason": "No destructive or external action was declared.",
        }
    )
    scope = (
        raw.get("scope")
        if isinstance(raw.get("scope"), Mapping)
        else {
            "maxRepositories": 1,
            "maxPaths": 32,
            "writable": write_allowed,
            "externalSideEffects": external_allowed,
        }
    )
    packet: dict[str, Any] = {
        "schemaVersion": "portable-task-packet-v1",
        "taskId": _text(raw.get("taskId"), max_length=128) if raw.get("taskId") else "",
        "requestType": request_type,
        "objective": objective,
        "complexity": complexity,
        "acceptanceCriteria": acceptance,
        "constraints": [
            _text(item, max_length=500, required=True)
            for item in (raw.get("constraints") or [])[:16]
        ],
        "riskSignals": [
            _text(item, max_length=500, required=True)
            for item in (raw.get("riskSignals") or [])[:16]
        ],
        "requiredCapabilities": _strip_untrusted(required_capabilities),
        "requiredEvidence": required_evidence,
        "allowedActions": {
            "read": bool(actions.get("read", True)),
            "write": write_allowed,
            "execute": bool(actions.get("execute", write_allowed)),
            "external": external_allowed,
        },
        "approval": _strip_untrusted(approval),
        "scope": _strip_untrusted(scope),
    }
    for key in (
        "currentBehavior",
        "expectedBehavior",
        "reproduction",
        "dependencies",
        "missingDecisions",
    ):
        if raw.get(key) is not None:
            packet[key] = _text(
                raw[key],
                max_length=2000 if key in {"currentBehavior", "expectedBehavior", "reproduction"} else 1000,
            )
    if source_binding:
        packet["sourceBinding"] = _strip_untrusted(source_binding)
    if not packet["taskId"] or not TASK_ID_PATTERN.fullmatch(packet["taskId"]):
        packet["taskId"] = "task-" + hashlib.sha256(
            f"{project_id}|{repository}|{objective}|{acceptance}".encode("utf-8")
        ).hexdigest()[:24]
    checked = validate_task_packet(packet)
    if not checked["valid"]:
        raise ValueError("invalid normalized task packet: " + "; ".join(checked["errors"]))
    return packet, {"projectId": project_id, "repository": repository}


def _safe_memory_file(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve(strict=False)
    root_resolved = root.resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    current = root_resolved
    for part in candidate.relative_to(root_resolved).parts:
        current = current / part
        if current.is_symlink():
            return None
    if not candidate.is_file() or candidate.name == "learning-store.json" or candidate.name.endswith(".lock"):
        return None
    return candidate


def _excerpt(text: str, terms: list[str], limit: int) -> str:
    lines = text.replace("\r", "").splitlines()
    normalized_terms = [term.casefold() for term in terms if term]
    keywords = {
        word for word in re.findall(r"[a-z0-9_]+", " ".join(normalized_terms))
        if len(word) >= 4 and word not in {"with", "from", "that", "this", "there", "their", "the", "and"}
    }
    minimum_hits = min(2, len(keywords))
    selected: list[str] = []
    for line in lines:
        lowered = line.casefold()
        line_words = set(re.findall(r"[a-z0-9_]+", lowered))
        related = minimum_hits > 0 and len(line_words & keywords) >= minimum_hits
        if line.lstrip().startswith("#") or any(term in lowered for term in normalized_terms) or related:
            selected.append(line.strip())
        if sum(len(item) + 1 for item in selected) >= limit:
            break
    if not selected:
        selected = [line.strip() for line in lines[:24] if line.strip()]
    return "\n".join(selected)[:limit]


def _read_memory_sources(
    receipt: Mapping[str, Any],
    memory_root: Path,
    *,
    max_sources: int,
    max_source_chars: int,
    terms: list[str],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for relative in list(receipt.get("selectedPaths", []))[:max_sources]:
        if not isinstance(relative, str):
            continue
        path = _safe_memory_file(memory_root, relative)
        if path is None:
            continue
        try:
            full_chars = path.stat().st_size
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(200_000)
        except OSError:
            continue
        privacy = privacy_findings({"content": text}, f"memory.{relative}")
        excerpt = (
            "[content withheld: privacy scan found sensitive material]"
            if privacy
            else _excerpt(text, terms, max_source_chars)
        )
        source_receipt = next(
            (
                item
                for item in receipt.get("sources", [])
                if isinstance(item, Mapping) and item.get("path") == relative
            ),
            {},
        )
        sources.append(
            {
                "path": relative,
                "sha256": source_receipt.get("sha256"),
                "fullCharacters": full_chars,
                "excerpt": excerpt,
                "excerptCharacters": len(excerpt),
                "privacyBlocked": bool(privacy),
            }
        )
    return sources


def _read_reference_sources(
    state: Mapping[str, Any] | None,
    memory_root: Path,
    *,
    max_source_chars: int,
    terms: list[str],
) -> list[dict[str, Any]]:
    """Read only the prior session's own no-clobber reference note."""

    if not state:
        return []
    reference = state.get("dailyReference")
    if not isinstance(reference, Mapping):
        return []
    relative = reference.get("relativePath")
    if not isinstance(relative, str) or not relative:
        return []
    path = _safe_memory_file(memory_root, relative)
    if path is None:
        return []
    try:
        full_chars = path.stat().st_size
        text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return []
    privacy = privacy_findings({"content": text}, f"memory.{relative}")
    excerpt = (
        "[content withheld: privacy scan found sensitive material]"
        if privacy
        else _excerpt(text, terms, max_source_chars)
    )
    return [
        {
            "path": relative,
            "sha256": _fingerprint(text).removeprefix("sha256:"),
            "fullCharacters": full_chars,
            "excerpt": excerpt,
            "excerptCharacters": len(excerpt),
            "privacyBlocked": bool(privacy),
            "referenceType": "continuity",
        }
    ]


def _worklog_excerpt(path: str | None, task_id: str, limit: int = 3000) -> tuple[str, int]:
    if not path:
        return "", 0
    candidate = Path(path).expanduser()
    if not candidate.is_file() or candidate.is_symlink():
        return "", 0
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return "", 0
    marker = f"<!-- session:{task_id} -->"
    start = text.find(marker)
    if start < 0:
        return "", len(text)
    end = text.find("\n---", start + len(marker))
    section = text[start:] if end < 0 else text[start:end]
    return section[:limit], len(section)


def _compact_packet(packet: Mapping[str, Any], meta: Mapping[str, str]) -> dict[str, Any]:
    return {
        "taskId": packet.get("taskId"),
        "projectId": meta.get("projectId"),
        "repository": meta.get("repository"),
        "objective": packet.get("objective"),
        "acceptanceCriteria": packet.get("acceptanceCriteria"),
        "complexity": packet.get("complexity"),
        "riskSignals": packet.get("riskSignals", []),
        "sourceBinding": packet.get("sourceBinding"),
    }


def build_context_budget(
    *,
    task_id: str,
    packet: Mapping[str, Any],
    meta: Mapping[str, str],
    sources: list[dict[str, Any]],
    context_text: str,
    worklog_full_chars: int = 0,
    worklog_excerpt_chars: int = 0,
) -> dict[str, Any]:
    full_packet_chars = len(
        json.dumps(_strip_untrusted(packet), sort_keys=True, ensure_ascii=True)
    )
    compact_packet_chars = len(
        json.dumps(_compact_packet(packet, meta), sort_keys=True, ensure_ascii=True)
    )
    full_chars = (
        full_packet_chars
        + sum(int(source.get("fullCharacters", 0)) for source in sources)
        + worklog_full_chars
    )
    selected_chars = compact_packet_chars + len(context_text) + worklog_excerpt_chars
    reduction = (
        0.0
        if full_chars <= 0
        else max(0.0, round((1 - min(selected_chars, full_chars) / full_chars) * 100, 2))
    )
    return {
        "schemaVersion": BUDGET_SCHEMA_VERSION,
        "taskId": task_id,
        "baseline": {
            "characters": full_chars,
            "estimatedTokens": _estimate_tokens(full_chars),
        },
        "selected": {
            "characters": selected_chars,
            "estimatedTokens": _estimate_tokens(selected_chars),
        },
        "reduction": {
            "estimatedPercent": reduction,
            "targetPercent": TARGET_REDUCTION_PERCENT,
            "targetStatus": "MET" if reduction >= TARGET_REDUCTION_PERCENT else "NOT_MET",
        },
        "method": "character-count with 4-characters-per-token estimate; provider tokenizer not used",
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }


def _render_context(
    packet: Mapping[str, Any],
    meta: Mapping[str, str],
    sources: list[dict[str, Any]],
    state: Mapping[str, Any] | None,
    *,
    max_chars: int,
    worklog_text: str = "",
) -> str:
    lines = [
        f"Task: {packet['taskId']}",
        f"Project/repository: {meta['projectId']} / {meta['repository']}",
        f"Objective: {packet['objective']}",
        "Acceptance:",
        *[f"- {item}" for item in packet["acceptanceCriteria"]],
    ]
    if state and state.get("latestSummary"):
        lines.extend(["Previous terminal summary:", f"- {state['latestSummary']}"])
    if state and state.get("nextAction"):
        lines.extend(["Next action:", f"- {state['nextAction']}"])
    if state and state.get("learning") and state["learning"].get("recordId"):
        lines.extend(
            [
                "Durable learning reference:",
                f"- record {state['learning']['recordId']} ({state['learning'].get('outcome', 'unknown')})",
            ]
        )
    lines.append("Memory references:")
    for source in sources:
        lines.append(f"- {source['path']} [{source.get('sha256') or 'hash-unavailable'}]")
    for source in sources:
        lines.extend([f"Memory excerpt: {source['path']}", source["excerpt"]])
    if worklog_text:
        lines.extend(["Worklog excerpt:", worklog_text])
    return "\n".join(lines)[:max_chars]


def _lookup_payload(
    packet: Mapping[str, Any],
    meta: Mapping[str, str],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "taskId": packet["taskId"],
        "inputText": packet["objective"],
        "normalizedObjective": packet["objective"],
        "repository": meta["repository"],
        "projectId": meta["projectId"],
        "taskType": packet["requestType"].casefold(),
        "intent": packet["requestType"].casefold(),
    }
    for key in (
        "application",
        "module",
        "feature",
        "issueKey",
        "error",
        "symbol",
        "path",
        "route",
        "fingerprints",
    ):
        if raw.get(key) is not None:
            payload[key] = _strip_untrusted(raw[key])
    return payload


def _load_session(root: Path, task_id: str) -> dict[str, Any] | None:
    return _read_json(_session_path(root, task_id))


def _write_session(root: Path, state: Mapping[str, Any]) -> Path:
    path = _session_path(root, str(state["taskId"]))
    with _state_lock(root):
        _atomic_json(path, state)
        index = _read_json(_index_path(root)) or {
            "schemaVersion": INDEX_SCHEMA_VERSION,
            "sessions": {},
        }
        index.setdefault("schemaVersion", INDEX_SCHEMA_VERSION)
        index.setdefault("sessions", {})[state["taskId"]] = {
            "sessionPath": path.relative_to(root).as_posix(),
            "taskFingerprint": state["taskFingerprint"],
            "projectId": state["projectId"],
            "repository": state["repository"],
            "status": state["status"],
            "updatedAt": state["updatedAt"],
        }
        _atomic_json(_index_path(root), index)
    return path


def _write_no_clobber(path: Path, content: str) -> tuple[str, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        try:
            os.link(temporary, path)
            return "queued", None
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                return "conflict", "existing continuity note is not a regular file"
            return (
                ("already_queued", None)
                if path.read_text(encoding="utf-8") == content
                else ("conflict", "existing continuity note differs; no overwrite performed")
            )
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_memory_reference(
    memory_root: Path,
    state: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    if not memory_root.is_dir() or memory_root.is_symlink():
        return {
            "outcome": "blocked",
            "path": None,
            "ingestionRequired": False,
            "errors": ["memory root is unavailable or unsafe"],
        }
    notes_root = memory_root / "extensions" / "ad_hoc" / "notes"
    try:
        canonical_root = memory_root.resolve(strict=True)
        canonical_notes_root = notes_root.resolve(strict=False)
        canonical_notes_root.relative_to(canonical_root)
        current = canonical_root
        for part in notes_root.relative_to(memory_root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("memory note path contains a symlink")
    except (OSError, ValueError):
        return {
            "outcome": "blocked",
            "path": None,
            "ingestionRequired": False,
            "errors": ["memory note path is unavailable or unsafe"],
        }
    task_id = str(state["taskId"])
    learning = state.get("learning") or {}
    budget = state.get("contextBudget") or {}
    content = "\n".join(
        [
            "# AE continuity reference",
            "",
            f"- Task: {task_id}",
            f"- Status: {state.get('status')}",
            f"- Project: {state.get('projectId')}",
            f"- Repository: {state.get('repository')}",
            f"- Task fingerprint: {state.get('taskFingerprint')}",
            f"- Learning record: {learning.get('recordId') or 'none'}",
            f"- Context reduction estimate: {((budget.get('reduction') or {}).get('estimatedPercent', 0))}%",
            "",
            "## Objective",
            str((state.get("packet") or {}).get("objective", "")),
            "",
            "## Terminal summary",
            str(state.get("latestSummary") or "No terminal summary."),
            "",
            "## Next action",
            str(
                state.get("nextAction")
                or "Revalidate current source before continuing."
            ),
            "",
            "## Memory references",
            *[
                f"- {item}"
                for item in (state.get("contextReferences") or {}).get(
                    "selectedPaths", []
                )
            ],
            "",
            "This is a sanitized, no-clobber candidate reference. Canonical memory promotion requires separate ingestion and revalidation.",
            "",
        ]
    )
    # A second terminal update on the same day must keep its own reference.
    # Identical content retains the same filename and remains idempotent.
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    note_path = notes_root / (
        f"ae-continuity-{now.strftime('%Y%m%d')}-{_slug(task_id)}-{digest}.md"
    )
    try:
        outcome, error = _write_no_clobber(note_path, content)
    except (OSError, UnicodeError) as exc:
        return {
            "outcome": "blocked",
            "path": None,
            "ingestionRequired": False,
            "errors": [f"continuity note write failed ({type(exc).__name__})"],
        }
    return {
        "outcome": outcome,
        "path": str(note_path),
        "relativePath": note_path.relative_to(memory_root).as_posix(),
        "ingestionRequired": True,
        "errors": [error] if error else [],
    }


def _context_from_state(
    packet: Mapping[str, Any],
    meta: Mapping[str, str],
    receipt: Mapping[str, Any],
    state: Mapping[str, Any] | None,
    memory_root: Path,
    *,
    profile: str,
    worklog_path: str | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    limits = PROFILE_LIMITS[profile]
    terms = [
        packet["objective"],
        *packet.get("riskSignals", []),
        *packet.get("acceptanceCriteria", []),
    ]
    sources = _read_memory_sources(
        receipt,
        memory_root,
        max_sources=limits["maxSources"],
        max_source_chars=limits["maxSourceChars"],
        terms=terms,
    )
    prior_references = _read_reference_sources(
        state,
        memory_root,
        max_source_chars=limits["maxSourceChars"],
        terms=terms,
    )
    known_paths = {source["path"] for source in sources}
    sources.extend(source for source in prior_references if source["path"] not in known_paths)
    sources = sources[: limits["maxSources"]]
    worklog_text, worklog_full_chars = _worklog_excerpt(
        worklog_path, packet["taskId"]
    )
    context_text = _render_context(
        packet,
        meta,
        sources,
        state,
        max_chars=limits["maxChars"],
        worklog_text=worklog_text,
    )
    budget = build_context_budget(
        task_id=packet["taskId"],
        packet=packet,
        meta=meta,
        sources=sources,
        context_text=context_text,
        worklog_full_chars=worklog_full_chars,
        worklog_excerpt_chars=len(worklog_text),
    )
    return context_text, sources, budget


def start_or_resume(
    value: Mapping[str, Any],
    *,
    action: str,
    state_root: str | Path | None = None,
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    if action not in {"start", "resume", "auto"}:
        raise ValueError("action must be start, resume, or auto")
    packet, meta = normalize_packet(value)
    current = _now(now)
    mem_root = resolve_storage_paths(memory_root=memory_root).memory_root.absolute()
    root = validate_state_root(
        state_root or default_state_root(),
        protected_roots=[PACKAGE_ROOT, mem_root],
    )
    existing = _load_session(root, packet["taskId"])
    if action == "auto":
        action = "resume" if existing is not None else "start"
    task_fingerprint = _packet_fingerprint(
        packet, project_id=meta["projectId"], repository=meta["repository"]
    )
    if action == "resume" and existing is None:
        return {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "outcome": "NO_MATCH",
            "taskId": packet["taskId"],
            "sourceOnly": True,
            "executed": False,
            "sideEffects": [],
            "errors": ["no exact continuity session exists for this task id"],
        }
    if existing is not None and existing.get("taskFingerprint") != task_fingerprint:
        return {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "outcome": "CONFLICT",
            "taskId": packet["taskId"],
            "sourceOnly": True,
            "executed": False,
            "sideEffects": [],
            "errors": ["task identity or acceptance criteria changed; start a new task id"],
        }
    lookup = lookup_memory_context(
        _lookup_payload(packet, meta, value), memory_root=mem_root, now=current
    )
    prior = existing or {}
    profile_value = profile or packet["complexity"]
    if profile_value not in PROFILE_LIMITS:
        profile_value = "NORMAL"
    context_text, sources, budget = _context_from_state(
        packet,
        meta,
        lookup,
        prior,
        mem_root,
        profile=profile_value,
        worklog_path=(prior.get("worklog") or {}).get("path"),
    )
    state = deepcopy(prior) if prior else {
        "schemaVersion": SESSION_SCHEMA_VERSION,
        "taskId": packet["taskId"],
        "createdAt": _iso(current),
        "projectId": meta["projectId"],
        "repository": meta["repository"],
        "packet": packet,
        "taskFingerprint": task_fingerprint,
        "status": "IN_PROGRESS",
    }
    state.update(
        {
            "updatedAt": _iso(current),
            "lastResumedAt": _iso(current),
            "status": "IN_PROGRESS" if action == "resume" else state.get("status", "IN_PROGRESS"),
            "lastLookup": lookup,
            "contextReferences": {
                "selectedPaths": [source["path"] for source in sources],
                "sources": [
                    {
                        key: source.get(key)
                        for key in (
                            "path",
                            "sha256",
                            "fullCharacters",
                            "excerptCharacters",
                            "privacyBlocked",
                        )
                    }
                    for source in sources
                ],
            },
            "context": context_text,
            "contextBudget": budget,
            "profile": profile_value,
            "sourceRevalidationRequired": True,
            "sideEffects": [],
        }
    )
    session_path = _write_session(root, state)
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "outcome": "RESUMED" if existing else "STARTED",
        "taskId": packet["taskId"],
        "sessionPath": str(session_path),
        "state": state,
        "lookup": lookup,
        "context": context_text,
        "contextBudget": budget,
        "sourceRevalidationRequired": True,
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }


def measure_input_context(
    value: Mapping[str, Any],
    *,
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Measure the bounded context for an input without creating state."""

    packet, meta = normalize_packet(value)
    current = _now(now)
    mem_root = resolve_storage_paths(memory_root=memory_root).memory_root.absolute()
    profile_value = profile or packet["complexity"]
    if profile_value not in PROFILE_LIMITS:
        profile_value = "NORMAL"
    lookup = lookup_memory_context(
        _lookup_payload(packet, meta, value), memory_root=mem_root, now=current
    )
    context_text, sources, budget = _context_from_state(
        packet, meta, lookup, None, mem_root, profile=profile_value
    )
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "outcome": "MEASURED",
        "taskId": packet["taskId"],
        "lookup": lookup,
        "context": context_text,
        "contextBudget": budget,
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }


def _learning_payload(
    state: Mapping[str, Any], closure: Mapping[str, Any]
) -> dict[str, Any]:
    packet = state["packet"]
    project_id = state["projectId"]
    repository = state["repository"]
    branch_or_tree = _text(
        closure.get("branchOrTree")
        or (packet.get("sourceBinding") or {}).get("sourceFingerprint")
        or "continuity-unverified",
        max_length=500,
        required=True,
    )
    files = (
        closure.get("filesInvolved")
        if isinstance(closure.get("filesInvolved"), list)
        else []
    )
    files = [
        _text(item, max_length=500, required=True).replace("\\", "/")
        for item in files[:64]
    ]
    evidence = (
        closure.get("evidence")
        if isinstance(closure.get("evidence"), list)
        else []
    )
    if not evidence:
        evidence = [
            {
                "repository": repository,
                "commitOrTree": branch_or_tree,
                "path": files[0] if files else f"continuity/{_slug(state['taskId'])}.json",
                "symbol": "continuity.close",
                "check": "terminal continuity summary recorded",
                "sourceType": "user",
            }
        ]
    claim = _text(
        closure.get("summary") or closure.get("claim"),
        max_length=2000,
        required=True,
    )
    task_type = packet["requestType"].casefold()
    fingerprints: dict[str, str] = {"taskType": task_type, "route": state["taskId"]}
    if files:
        fingerprints["path"] = files[0]
    return {
        "projectId": project_id,
        "repository": repository,
        "branchOrTree": branch_or_tree,
        "claim": claim,
        "taskId": state["taskId"],
        "accessScope": (
            closure.get("accessScope")
            if isinstance(closure.get("accessScope"), list)
            and closure.get("accessScope")
            else [f"project:{project_id}:read"]
        ),
        "filesInvolved": files,
        "intent": task_type,
        "taskType": task_type,
        "fingerprints": fingerprints,
        "kind": closure.get("kind") or "project_fact",
        "evidence": evidence,
        "status": str(closure.get("status") or "completed").casefold(),
        "confidence": closure.get("confidence") or "medium",
        "businessLogic": closure.get("businessLogic") or claim,
        "fixStrategy": closure.get("fixStrategy")
        or closure.get("nextAction")
        or "Revalidate current source before the next continuation.",
        "canonicalKey": f"continuity:{state['taskId']}",
    }


def attach_worklog_result(
    *, state_root: str | Path, task_id: str, result: Mapping[str, Any]
) -> dict[str, Any]:
    root = validate_state_root(state_root, protected_roots=[PACKAGE_ROOT])
    state = _load_session(root, task_id)
    if state is None:
        raise ValueError("continuity session not found")
    state["worklog"] = _strip_untrusted(result)
    state["updatedAt"] = _iso(_now())
    _write_session(root, state)
    return state


def close_continuity(
    value: Mapping[str, Any],
    closure: Mapping[str, Any],
    *,
    state_root: str | Path | None = None,
    memory_root: str | Path | None = None,
    store_path: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    task_id = _text(value.get("taskId"), max_length=128, required=True)
    current = _now(now)
    mem_root = resolve_storage_paths(memory_root=memory_root).memory_root.absolute()
    root = validate_state_root(
        state_root or default_state_root(),
        protected_roots=[PACKAGE_ROOT, mem_root],
    )
    state = _load_session(root, task_id)
    if state is None:
        raise ValueError("continuity session not found")
    status = str(closure.get("status") or "completed").casefold()
    if status not in TERMINAL_STATUSES:
        raise ValueError("closure.status must be completed, blocked, or paused")
    summary = _text(
        closure.get("summary") or closure.get("claim"),
        max_length=2000,
        required=True,
    )
    privacy = privacy_findings(
        {"summary": summary, "nextAction": closure.get("nextAction", "")},
        "continuityClosure",
    )
    if privacy:
        raise ValueError("closure contains sensitive material")
    payload = _learning_payload(state, closure)
    selected_store = resolve_storage_paths(memory_root=memory_root, learning_store=store_path).learning_store
    learning = record_task_learning(
        payload,
        store_path=selected_store,
        memory_root=mem_root,
        now=current,
    )
    state.update(
        {
            "status": status.upper(),
            "updatedAt": _iso(current),
            "closedAt": _iso(current),
            "latestSummary": summary,
            "nextAction": _text(
                closure.get("nextAction")
                or "Start the next session by resuming this task and revalidating current source.",
                max_length=1000,
            ),
            "decisions": [
                _text(item, max_length=500, required=True)
                for item in (closure.get("decisions") or [])[:16]
            ],
            "learning": {
                "outcome": learning.get("outcome"),
                "recordId": learning.get("recordId"),
                "storePath": learning.get("storePath"),
                "moduleFeatureWriteback": learning.get("moduleFeatureWriteback"),
            },
            "sourceRevalidationRequired": True,
        }
    )
    note = _write_memory_reference(mem_root, state, now=current)
    state["dailyReference"] = note
    state["updatedAt"] = _iso(current)
    session_path = _write_session(root, state)
    overall = (
        "CLOSED"
        if learning.get("outcome") not in {"blocked", "partial", "pending"}
        and note.get("outcome") not in {"blocked", "partial", "conflict"}
        else "PARTIAL"
    )
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "outcome": overall,
        "taskId": task_id,
        "sessionPath": str(session_path),
        "learning": learning,
        "dailyReference": note,
        "state": state,
        "sourceRevalidationRequired": True,
        "sourceOnly": True,
        "executed": False,
        "errors": [
            *learning.get("errors", []),
            *note.get("errors", []),
        ],
        "sideEffects": [
            "continuity session updated",
            *learning.get("sideEffects", []),
        ],
    }
