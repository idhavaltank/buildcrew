#!/usr/bin/env python3
"""Explicit host-controlled persistence for project-context sidecars.

The repository is never used as a fallback state root. Callers must opt into
an external root supplied by the host; failures become session-only state.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .project_context import _is_within, default_protected_roots


class ContextStateError(ValueError):
    """Raised when an external state boundary is unsafe."""


def _portable_project_id(repository: Mapping[str, Any]) -> str:
    raw = f"{repository.get('projectId', '')}:{repository.get('repositoryId', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def validate_state_root(
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    *,
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Return a canonical external state root or fail closed."""

    state = Path(state_root).expanduser().resolve(strict=False)
    repository = Path(repository_root).expanduser().resolve(strict=True)
    if _is_within(state, repository) or _is_within(repository, state):
        raise ContextStateError("external state root must not overlap the repository root")
    protected = default_protected_roots()
    protected.extend(Path(value).expanduser().resolve(strict=False) for value in protected_roots)
    for boundary in protected:
        if _is_within(state, boundary) or _is_within(boundary, state):
            raise ContextStateError(f"external state root overlaps protected root: {boundary}")
    return state


def state_directory(
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    repository: Mapping[str, Any],
    *,
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    root = validate_state_root(state_root, repository_root, protected_roots=protected_roots)
    return root / "project-context" / _portable_project_id(repository)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def persist_context(
    context_map: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    """Persist map and receipt atomically enough for independent sidecars.

    The return value is explicit about failure. It never writes to the
    repository and never treats a partial write as current state.
    """

    try:
        persisted_map = deepcopy(dict(context_map))
        persisted_receipt = deepcopy(dict(receipt))
        persisted_map["persistence"] = "external"
        persisted_receipt["persistence"] = "external"
        from .context_receipts import validate_context_map, validate_receipt

        map_check = validate_context_map(persisted_map)
        receipt_check = validate_receipt(persisted_receipt)
        if not map_check["valid"] or not receipt_check["valid"]:
            raise ContextStateError("map or receipt contract validation failed")
        if persisted_map.get("repository") != persisted_receipt.get("repository"):
            raise ContextStateError("map and receipt repository identities do not match")
        if persisted_map.get("fingerprints") != persisted_receipt.get("fingerprints"):
            raise ContextStateError("map and receipt fingerprints do not match")
        directory = state_directory(
            state_root,
            repository_root,
            persisted_map["repository"],
            protected_roots=protected_roots,
        )
        _atomic_write_json(directory / "map-v1.json", persisted_map)
        _atomic_write_json(directory / "map-v1.receipt.json", persisted_receipt)
        return {
            "persistence": "external",
            "directory": directory.as_posix(),
            "mapPath": (directory / "map-v1.json").as_posix(),
            "receiptPath": (directory / "map-v1.receipt.json").as_posix(),
        }
    except (OSError, KeyError, TypeError, ContextStateError) as exc:
        return {
            "persistence": "unavailable",
            "directory": None,
            "limitation": f"external state was not persisted: {type(exc).__name__}",
        }


def load_context(
    *,
    state_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    repository: Mapping[str, Any],
    protected_roots: Sequence[str | os.PathLike[str]] = (),
    expected_fingerprints: Mapping[str, Any] | None = None,
    expected_task_id: str | None = None,
) -> dict[str, Any] | None:
    """Load only the exact identity-bound pair of sidecars, if both exist."""

    try:
        directory = state_directory(
            state_root,
            repository_root,
            repository,
            protected_roots=protected_roots,
        )
        map_path = directory / "map-v1.json"
        receipt_path = directory / "map-v1.receipt.json"
        if not map_path.is_file() or not receipt_path.is_file():
            return None
        context_map = json.loads(map_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(context_map, dict) or not isinstance(receipt, dict):
            return None
        if context_map.get("repository") != dict(repository):
            return None
        from .context_receipts import validate_context_map, validate_receipt

        if not validate_context_map(context_map)["valid"] or not validate_receipt(receipt)["valid"]:
            return None
        if context_map.get("status") != "CURRENT" or receipt.get("terminalStatus") != "COMPLETED":
            return None
        if context_map.get("persistence") != "external" or receipt.get("persistence") != "external":
            return None
        if context_map.get("fingerprints") != receipt.get("fingerprints"):
            return None
        if expected_fingerprints is not None and context_map.get("fingerprints") != dict(expected_fingerprints):
            return None
        if expected_task_id is not None and receipt.get("taskId") != expected_task_id:
            return None
        expires_at = receipt.get("expiresAt")
        if expires_at:
            normalized = str(expires_at).replace("Z", "+00:00")
            if datetime.fromisoformat(normalized) <= datetime.now(timezone.utc):
                return None
        return {"contextMap": context_map, "receipt": receipt, "directory": directory.as_posix()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ContextStateError):
        return None
