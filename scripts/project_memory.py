"""Scoped canonical project memory and reviewed candidate ingestion.

Task closure only queues candidate notes. Promotion is a separate, explicit
host review that binds the note, durable lesson, and current source bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Mapping

try:
    from rag_contracts import privacy_findings
    from storage_paths import resolve_storage_paths
except ImportError:  # pragma: no cover - package import fallback
    from .rag_contracts import privacy_findings
    from .storage_paths import resolve_storage_paths


REVIEW_SCHEMA_VERSION = "canonical-ingestion-review-v1"
PROJECT_MEMORY_SCHEMA_VERSION = "buildcrew-project-memory-v1"
MAX_CANONICAL_BYTES = 128 * 1024
SHA256 = re.compile(r"^[a-f0-9]{64}$")
RECORD_MARKER = re.compile(r"^<!-- buildcrew-record-v1: (\{[^\n]+\}) -->$", re.M)


def project_memory_path(memory_root: Path, project_id: str, repository: str) -> Path:
    """Derive an exact project/repository location without using input as a path."""

    identity = json.dumps([project_id, repository], separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return memory_root / "autonomous-engineering" / "project-memory" / digest / "MEMORY.md"


def _header(project_id: str, repository: str) -> str:
    identity = json.dumps(
        {"projectId": project_id, "repository": repository},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"# BuildCrew project memory\n\n<!-- {PROJECT_MEMORY_SCHEMA_VERSION}: {identity} -->\n\n"


def read_project_memory(path: Path, project_id: str, repository: str) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CANONICAL_BYTES:
        raise ValueError("project memory is missing, symlinked, or over budget")
    content = path.read_text(encoding="utf-8")
    if not content.startswith(_header(project_id, repository)):
        raise ValueError("project memory identity or format does not match")
    # Record markers contain machine digests; scan the readable memory text.
    if privacy_findings(RECORD_MARKER.sub("", content), "projectMemory"):
        raise ValueError("project memory contains sensitive material")
    return content


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _safe_relative(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or "\\" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise ValueError("expected a safe relative path")
    return candidate


def _no_symlink_path(path: Path, root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("approved root is unavailable or symlinked")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("path escapes approved root") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("path contains a symlink")


def _candidate(review: Mapping[str, Any], root: Path, record: Mapping[str, Any]) -> tuple[Path, str]:
    relative = _safe_relative(str(review["candidateRelativePath"]))
    if relative.parts[:3] != ("extensions", "ad_hoc", "notes") or relative.suffix != ".md":
        raise ValueError("candidate must be an ad-hoc Markdown note")
    path = root.joinpath(*relative.parts)
    _no_symlink_path(path, root)
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("candidate note is missing or over budget")
    if _digest(path) != review["candidateSha256"]:
        raise ValueError("candidate note digest changed since review")
    content = path.read_text(encoding="utf-8")
    record_id = record["id"]
    task_id = review["taskId"]
    module_note = (
        f'record_id: {json.dumps(record_id)}' in content
        and f'task_id: {json.dumps(task_id)}' in content
        and f'project: {json.dumps(record["projectId"])}' in content
        and f'repository: {json.dumps(record["repository"])}' in content
    )
    continuity_note = (
        f"- Learning record: {record_id}" in content
        and f"- Task: {task_id}" in content
        and f"- Project: {record['projectId']}" in content
        and f"- Repository: {record['repository']}" in content
    )
    readable = re.sub(r"sha256:[a-f0-9]{64}", "[digest]", content)
    readable = re.sub(r"lesson-[a-f0-9]{24}", "[lesson]", readable)
    if not (module_note or continuity_note) or privacy_findings(readable, "candidateNote"):
        raise ValueError("candidate note does not match reviewed lesson or is sensitive")
    return path, content


def _source_hashes(review: Mapping[str, Any], source_root: Path, record: Mapping[str, Any]) -> None:
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("source root is unavailable or symlinked")
    supplied = review["sourceHashes"]
    evidence_paths = {item["path"] for item in record["evidence"]}
    if not isinstance(supplied, Mapping) or set(supplied) != evidence_paths or len(supplied) > 32:
        raise ValueError("source hashes must cover exactly the lesson evidence paths")
    for relative, expected in supplied.items():
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise ValueError("source hash must be a lowercase SHA-256")
        path = source_root.joinpath(*_safe_relative(relative).parts)
        _no_symlink_path(path, source_root)
        if not path.is_file() or _digest(path) != expected:
            raise ValueError("current source does not match reviewed evidence")


def _atomic_text(path: Path, content: str) -> bool:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=".MEMORY.", suffix=".tmp", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            return False
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def ingest_reviewed_candidate(
    review: Mapping[str, Any], *, memory_root: Path, source_root: Path, store_path: Path | None = None
) -> dict[str, Any]:
    """Promote one reviewed lesson; never promote a task note on closure alone."""

    try:
        from learning_runtime import load_learning_store
        from memory_context_runtime import canonical_memory_lock
        from rag_contracts import validate_lesson
    except ImportError:  # pragma: no cover - package import fallback
        from .learning_runtime import load_learning_store
        from .memory_context_runtime import canonical_memory_lock
        from .rag_contracts import validate_lesson

    required = {
        "schemaVersion", "decision", "approvalId", "reviewer", "taskId", "recordId",
        "projectId", "repository", "branchOrTree", "candidateRelativePath",
        "candidateSha256", "sourceHashes",
    }
    if (
        not isinstance(review, Mapping)
        or set(review) != required
        or review.get("schemaVersion") != REVIEW_SCHEMA_VERSION
    ):
        raise ValueError("review packet has an invalid version or fields")
    if review["decision"] != "APPROVED" or any(
        not isinstance(review[key], str) or not review[key].strip()
        for key in required - {"schemaVersion", "decision", "sourceHashes"}
    ):
        raise ValueError("ingestion needs an explicit approved review with complete identity")
    if any(
        len(review[key]) > 500 or any(ord(character) < 32 for character in review[key])
        for key in required - {"schemaVersion", "decision", "sourceHashes"}
    ):
        raise ValueError("review identity fields must be bounded single-line text")
    if not SHA256.fullmatch(review["candidateSha256"]):
        raise ValueError("candidate digest must be a lowercase SHA-256")
    review_text = {key: review[key] for key in ("approvalId", "reviewer", "projectId", "repository", "branchOrTree")}
    if privacy_findings(review_text, "ingestionReview"):
        raise ValueError("review packet contains sensitive material")
    root = memory_root.expanduser()
    source = source_root.expanduser()
    if root.is_symlink() or not root.is_dir() or source.is_symlink() or not source.is_dir():
        raise ValueError("memory and source roots must be existing real directories")
    root = root.resolve(strict=True)
    source = source.resolve(strict=True)
    if root == source or root in source.parents or source in root.parents:
        raise ValueError("memory and source roots must not overlap")
    store = store_path or resolve_storage_paths(memory_root=root).learning_store
    if store.is_symlink():
        raise ValueError("learning store cannot be a symlink")
    loaded = load_learning_store(store)
    if not loaded["valid"]:
        raise ValueError("learning store is invalid or unavailable")
    record = next(
        (item for item in loaded["document"]["records"] if item.get("id") == review["recordId"]),
        None,
    )
    if record is None or not validate_lesson(record)["valid"]:
        raise ValueError("reviewed lesson is missing or invalid")
    if any(record.get(key) != review[key] for key in ("projectId", "repository", "branchOrTree")):
        raise ValueError("review scope does not match the lesson")
    shared_scope = [f"project:{record['projectId'].casefold()}:read"]
    if record.get("tenantId") or record.get("accessScope") != shared_scope:
        raise ValueError("restricted lessons cannot enter shared project memory")
    if (
        record.get("status") in {"superseded", "rejected", "quarantined"}
        or record.get("sensitivity") != "none"
    ):
        raise ValueError("lesson is not eligible for canonical promotion")
    expiry = datetime.fromisoformat(record["expiresAt"].replace("Z", "+00:00"))
    if expiry <= datetime.now(timezone.utc):
        raise ValueError("lesson has expired; revalidate before promotion")
    _candidate(review, root, record)
    _source_hashes(review, source, record)
    path = project_memory_path(root, review["projectId"], review["repository"])
    marker = {
        "recordId": record["id"], "contentHash": record["contentHash"],
        "canonicalKey": record["canonicalKey"], "candidateSha256": review["candidateSha256"],
    }
    entry = "\n".join([
        f"<!-- buildcrew-record-v1: {json.dumps(marker, sort_keys=True, separators=(',', ':'))} -->",
        f"## {record['canonicalKey']}",
        f"- Claim: {record['claim']}",
        f"- Source: {record['repository']} @ {record['branchOrTree']}",
        f"- Evidence: {', '.join(sorted(review['sourceHashes']))}",
        f"- Review: {review['reviewer']} ({review['approvalId']})",
        "- Revalidate against current source before applying this knowledge.",
        "",
    ])
    if privacy_findings(RECORD_MARKER.sub("", entry), "canonicalEntry"):
        raise ValueError("canonical entry contains sensitive material")
    with canonical_memory_lock(root, exclusive=True):
        _no_symlink_path(path, root)
        if path.exists():
            content = read_project_memory(path, review["projectId"], review["repository"])
        else:
            content = _header(review["projectId"], review["repository"])
        existing = [json.loads(match.group(1)) for match in RECORD_MARKER.finditer(content)]
        same = next((item for item in existing if item.get("recordId") == record["id"]), None)
        if same is not None:
            if all(same.get(key) == marker[key] for key in ("recordId", "contentHash", "canonicalKey")):
                return {
                    "outcome": "ALREADY_INGESTED", "path": str(path),
                    "recordId": record["id"], "canonicalFilesChanged": False,
                }
            raise ValueError("canonical record identity conflicts with existing content")
        if any(item.get("canonicalKey") == record["canonicalKey"] for item in existing):
            raise ValueError("canonical key already has a different lesson; review supersession first")
        updated = content + entry
        if len(updated.encode("utf-8")) > MAX_CANONICAL_BYTES:
            raise ValueError("canonical project memory exceeds its size budget")
        _source_hashes(review, source, record)
        path.parent.mkdir(parents=True, exist_ok=True)
        _no_symlink_path(path, root)
        durable = _atomic_text(path, updated)
        try:
            readback = read_project_memory(path, review["projectId"], review["repository"])
        except (OSError, UnicodeError, ValueError):
            readback = None
        if not durable or readback != updated:
            return {
                "outcome": "PARTIAL", "path": str(path), "recordId": record["id"],
                "canonicalFilesChanged": True,
                "errors": ["canonical file was replaced but durability or readback was not confirmed"],
            }
    return {"outcome": "INGESTED", "path": str(path), "recordId": record["id"], "canonicalFilesChanged": True}

