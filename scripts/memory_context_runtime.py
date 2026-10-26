#!/usr/bin/env python3
"""Bounded local-memory routing and conflict-safe module/feature write-back.

Lookup is read-only and is designed to run for every input. Durable write-back
never edits canonical indexes directly; it creates one unique ad-hoc ingestion
note so a concurrent memory generator cannot be overwritten.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Mapping

try:
    from project_memory import project_memory_path, read_project_memory
    from rag_contracts import privacy_findings, validate_lesson
    from storage_paths import resolve_storage_paths
except ImportError:  # pragma: no cover - package import fallback
    from .project_memory import project_memory_path, read_project_memory
    from .rag_contracts import privacy_findings, validate_lesson
    from .storage_paths import resolve_storage_paths


LOOKUP_SCHEMA_VERSION = "memory-context-lookup-v1"
WRITEBACK_SCHEMA_VERSION = "module-feature-memory-writeback-v1"
MAX_QUERY_FIELDS = 64
MAX_QUERY_TEXT = 2000
MAX_ROUTES = 5000
MAX_MEMORY_PATHS = 32
_SAFE_ROUTE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/ -]{0,199}$")


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return " ".join(value.strip().split())[:MAX_QUERY_TEXT] if isinstance(value, str) else ""


def _slug(value: Any, *, fallback: str = "feature", limit: int = 64) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-")
    return (normalized or fallback)[:limit].rstrip("-")


def default_memory_root() -> Path:
    return resolve_storage_paths().memory_root


@contextmanager
def canonical_memory_lock(memory_root: str | Path, *, exclusive: bool = False):
    """Hold the canonical generation lock for one complete read or publication.

    Protect the configured canonical memory root and its namespaces. The lock
    lives beside that root (``.memories.canonical-memory.lock`` for a root named
    ``memories``), outside canonical content, so namespace publication keeps
    the lock inode stable. Opening is no-follow and the leaf must remain a
    single-link regular file.
    """

    root = Path(memory_root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("canonical memory root must be a real directory")
    root = root.resolve(strict=True)
    lock_path = root.parent / f".{root.name}.canonical-memory.lock"
    if lock_path.is_symlink():
        raise ValueError("canonical memory lock cannot be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("canonical memory lock must be a single-link regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield lock_path
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _lookup_receipt(
    *,
    outcome: str,
    task_id: str | None,
    memory_root: Path,
    query_fingerprint: str | None,
    route: Mapping[str, Any] | None,
    selected_paths: list[str],
    sources: list[dict[str, Any]],
    registry_status: str,
    errors: list[str],
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "schemaVersion": LOOKUP_SCHEMA_VERSION,
        "outcome": outcome,
        "taskId": task_id,
        "queryFingerprint": query_fingerprint,
        "memoryRoot": str(memory_root),
        "route": dict(route) if route is not None else None,
        "selectedPaths": selected_paths,
        "sources": sources,
        "registryStatus": registry_status,
        "providerPolicy": "local_first_provider_optional",
        "sourceFirstRequired": outcome != "matched",
        "currentSourceRevalidationRequired": True,
        "errors": sorted(set(errors)),
        "sideEffects": [],
        "generatedAt": _iso(generated_at),
    }


def _query_signals(payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "inputText",
        "normalizedObjective",
        "objective",
        "repository",
        "application",
        "module",
        "feature",
        "issueKey",
        "error",
        "symbol",
        "path",
        "route",
        "taskType",
        "intent",
    ):
        text = _text(payload.get(key))
        if text:
            values.append(text.casefold())
    fingerprints = payload.get("fingerprints")
    if isinstance(fingerprints, Mapping):
        for key in sorted(fingerprints):
            text = _text(fingerprints.get(key))
            if text:
                values.append(text.casefold())
    return list(dict.fromkeys(values))[:MAX_QUERY_FIELDS]


def _fingerprint(signals: list[str]) -> str:
    encoded = json.dumps(signals, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _contains_symlink(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _excluded_memory_path(path: Path, memory_root: Path) -> bool:
    try:
        relative = path.relative_to(memory_root)
    except ValueError:
        return True
    lowered = [part.casefold() for part in relative.parts]
    return (
        any(part == "_archive" or part.startswith(".reformat-staging-") for part in lowered)
        or path.name.casefold() == "learning-store.json"
        or path.name.casefold().endswith(".lock")
        or any(part in {"compatibility-redirects", "compatibility_redirects"} for part in lowered)
    )


def _validate_memory_file(
    path: Path,
    *,
    memory_root: Path,
    containment_root: Path,
    label: str,
) -> tuple[Path | None, str | None]:
    if _contains_symlink(path, memory_root):
        return None, f"{label}: unsafe symlinked memory path"
    if not path.exists():
        return None, f"{label}: unavailable"
    if not path.is_file():
        return None, f"{label}: unsafe non-regular memory path"
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(containment_root.resolve(strict=True))
        resolved.relative_to(memory_root.resolve(strict=True))
    except (OSError, ValueError):
        return None, f"{label}: unsafe memory path escape"
    if _excluded_memory_path(resolved, memory_root.resolve(strict=True)):
        return None, f"{label}: unsafe excluded memory path"
    return path, None


def _load_json(
    path: Path,
    *,
    label: str,
    memory_root: Path,
    containment_root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    safe_path, safe_error = _validate_memory_file(
        path,
        memory_root=memory_root,
        containment_root=containment_root,
        label=label,
    )
    if safe_error == f"{label}: unavailable":
        return None, [f"{label}: unavailable"]
    if safe_error or safe_path is None:
        return None, [safe_error or f"{label}: unsafe memory path"]
    try:
        value = json.loads(safe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [f"{label}: cannot be read ({type(error).__name__})"]
    if not isinstance(value, dict):
        return None, [f"{label}: expected object"]
    return value, []


def _route_score(route: Mapping[str, Any], payload: Mapping[str, Any], haystack: str) -> tuple[int, list[str]]:
    for field in ("repository", "application", "module"):
        requested = _text(payload.get(field)).casefold()
        actual = _text(route.get(field)).casefold()
        if requested and requested != actual:
            return 0, []

    score = 0
    hits: list[str] = []
    for raw in route.get("match", []):
        phrase = _text(raw).casefold()
        if phrase and phrase in haystack:
            score += 1000 + len(phrase)
            hits.append(phrase)

    route_id = _text(route.get("id")).casefold()
    if route_id and route_id in haystack:
        score += 800
        hits.append(route_id)

    requested_module = _text(payload.get("module")).casefold()
    route_module = _text(route.get("module")).casefold()
    if requested_module and requested_module == route_module:
        score += 700
        hits.append(f"module:{route_module}")

    requested_application = _text(payload.get("application")).casefold()
    route_application = _text(route.get("application")).casefold()
    if requested_application and requested_application == route_application:
        score += 100

    requested_repository = _text(payload.get("repository")).casefold()
    route_repository = _text(route.get("repository")).casefold()
    if requested_repository and requested_repository == route_repository:
        score += 100

    # A repository/application match alone is too broad. At least one feature,
    # route-id, or exact module signal must identify the route.
    return (score, hits) if hits else (0, [])


def _validate_routes(routes: list[Any]) -> list[str]:
    errors: list[str] = []
    for index, route in enumerate(routes):
        if not isinstance(route, Mapping):
            errors.append(f"routes[{index}]: expected object")
            continue
        for field in ("id", "repository", "application", "module"):
            value = route.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_QUERY_TEXT:
                errors.append(f"routes[{index}].{field}: expected bounded non-empty string")
        for field, required in (
            ("match", True),
            ("initial_memory_paths", True),
            ("expand_only_if", False),
            ("backend_expansion_paths", False),
        ):
            value = route.get(field, [] if not required else None)
            if not isinstance(value, list) or (required and not value):
                errors.append(f"routes[{index}].{field}: expected {'non-empty ' if required else ''}array")
                continue
            if len(value) > MAX_MEMORY_PATHS:
                errors.append(f"routes[{index}].{field}: exceeds bounded limit")
            if any(not isinstance(item, str) or not item.strip() or len(item.strip()) > MAX_QUERY_TEXT for item in value):
                errors.append(f"routes[{index}].{field}: expected bounded non-empty strings")
    return sorted(set(errors))


def _validate_registry(registry: Mapping[str, Any]) -> list[str]:
    repositories = registry.get("repositories")
    if not isinstance(repositories, list) or len(repositories) > MAX_ROUTES:
        return ["module registry repositories are invalid or exceed the bounded limit"]
    errors: list[str] = []
    for repository_index, repository in enumerate(repositories):
        if not isinstance(repository, Mapping):
            errors.append(f"module registry.repositories[{repository_index}]: expected object")
            continue
        if not _text(repository.get("name")):
            errors.append(f"module registry.repositories[{repository_index}].name: expected non-empty string")
        if repository.get("memoryIndex") is not None and not _text(repository.get("memoryIndex")):
            errors.append(f"module registry.repositories[{repository_index}].memoryIndex: expected non-empty string")
        for field in ("modules", "components"):
            values = repository.get(field)
            if values is not None and (
                not isinstance(values, list) or any(not _text(value) for value in values)
            ):
                errors.append(f"module registry.repositories[{repository_index}].{field}: expected string array")
        for collection_name in ("applications", "services", "packages", "sharedPackages"):
            collection = repository.get(collection_name)
            if collection is None:
                continue
            if not isinstance(collection, list):
                errors.append(f"module registry.repositories[{repository_index}].{collection_name}: expected array")
                continue
            for item_index, item in enumerate(collection):
                prefix = f"module registry.repositories[{repository_index}].{collection_name}[{item_index}]"
                if isinstance(item, str):
                    if not item.strip():
                        errors.append(f"{prefix}: expected non-empty string")
                    continue
                if not isinstance(item, Mapping):
                    errors.append(f"{prefix}: expected object or string")
                    continue
                if not _text(item.get("name")):
                    errors.append(f"{prefix}.name: expected non-empty string")
                for field in ("modules", "components", "sourceAreas"):
                    values = item.get(field)
                    if values is not None and (
                        not isinstance(values, list) or any(not _text(value) for value in values)
                    ):
                        errors.append(f"{prefix}.{field}: expected string array")
                if item.get("memoryIndex") is not None and not _text(item.get("memoryIndex")):
                    errors.append(f"{prefix}.memoryIndex: expected non-empty string")
    return sorted(set(errors))


def _registry_units(repository: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    units: list[Mapping[str, Any]] = []
    for collection_name in ("applications", "services", "packages", "sharedPackages"):
        collection = repository.get(collection_name)
        if isinstance(collection, list):
            units.extend(item for item in collection if isinstance(item, Mapping))
    return units


def _registry_modules(owner: Mapping[str, Any]) -> set[str]:
    values: list[Any] = []
    for field in ("modules", "components", "sourceAreas"):
        raw = owner.get(field)
        if isinstance(raw, list):
            values.extend(raw)
    return {_text(value).casefold() for value in values if _text(value)}


def _registry_unit_matches_module(
    repository: Mapping[str, Any],
    unit: Mapping[str, Any],
    requested_module: str,
) -> bool:
    requested = requested_module.casefold()
    modules = _registry_modules(unit)
    if requested in modules:
        return True
    return (
        repository.get("status") == "source-area-only"
        and not modules
        and bool(_text(unit.get("memoryIndex")))
        and requested == _text(unit.get("name")).casefold()
    )


def _safe_memory_path(memory_root: Path, index_root: Path, raw: Any) -> tuple[Path | None, str | None]:
    text = _text(raw).replace("\\", "/")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts:
        return None, "unsafe memory path"
    lowered = [part.casefold() for part in pure.parts]
    if (
        any(part == "_archive" or part.startswith(".reformat-staging-") for part in lowered)
        or pure.name.casefold() == "learning-store.json"
        or pure.name.casefold().endswith(".lock")
        or any(part in {"compatibility-redirects", "compatibility_redirects"} for part in lowered)
    ):
        return None, "unsafe excluded memory path"
    candidate = index_root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(index_root.resolve(strict=True))
        resolved.relative_to(memory_root.resolve(strict=True))
    except ValueError:
        return None, "unsafe memory path"
    except OSError:
        return None, "unsafe unavailable memory boundary"
    if _contains_symlink(candidate, memory_root):
        return None, "unsafe symlinked memory path"
    if _excluded_memory_path(resolved, memory_root.resolve(strict=True)):
        return None, "unsafe excluded memory path"
    return candidate, None


def _source_receipt(path: Path, memory_root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return {
        "path": path.relative_to(memory_root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "modifiedAt": _iso(modified),
    }


def _registry_status(registry: Mapping[str, Any] | None, route: Mapping[str, Any] | None) -> str:
    if registry is None:
        return "unavailable"
    if route is None:
        return "not_selected"
    repositories = registry.get("repositories")
    if not isinstance(repositories, list):
        return "invalid"
    repository = _text(route.get("repository")).casefold()
    application = _text(route.get("application")).casefold()
    module = _text(route.get("module")).casefold()
    for item in repositories:
        if not isinstance(item, Mapping) or _text(item.get("name")).casefold() != repository:
            continue
        for app in _registry_units(item):
            if _text(app.get("name")).casefold() != application:
                continue
            if not module or _registry_unit_matches_module(item, app, module):
                return "matched"
            # A route may intentionally address the unit's own nearest index
            # (for example a shared package catalog). The exact application
            # and module identity is a bounded source boundary, not an
            # assertion that the unit contains a formal feature module.
            if module == _text(app.get("name")).casefold() and _text(app.get("memoryIndex")):
                return "matched"
            return "module_not_listed"
        direct_names = {_text(item.get("name")).casefold(), _text(item.get("kind")).casefold()}
        if application in direct_names:
            modules = _registry_modules(item)
            if not module or module in modules:
                return "matched"
            # Repository-level routes can use the repository's own nearest
            # index with an explicit repository-context module.
            if application == _text(item.get("name")).casefold() and module == "repository-context" and _text(item.get("memoryIndex")):
                return "matched"
            return "module_not_listed"
        return "application_not_listed"
    return "repository_not_listed"


def _registry_fallback(
    registry: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    *,
    memory_root: Path,
    index_root: Path,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    if registry is None:
        return None, None, None
    requested_repository = _text(payload.get("repository"))
    requested_application = _text(payload.get("application"))
    requested_module = _text(payload.get("module"))
    if not requested_repository or not requested_application or not requested_module:
        return None, None, None
    repositories = registry.get("repositories")
    if not isinstance(repositories, list):
        return None, None, "module registry repositories are invalid"
    for repository in repositories:
        if not isinstance(repository, Mapping) or _text(repository.get("name")).casefold() != requested_repository.casefold():
            continue
        for application in _registry_units(repository):
            if _text(application.get("name")).casefold() != requested_application.casefold():
                continue
            if not _registry_unit_matches_module(repository, application, requested_module):
                return None, None, None
            raw_index = application.get("memoryIndex") or repository.get("memoryIndex")
            candidate, error = _safe_memory_path(memory_root, index_root, raw_index)
            if error:
                return None, None, error
            route = {
                "routeId": f"registry-fallback:{_slug(requested_repository)}:{_slug(requested_application)}:{_slug(requested_module)}",
                "repository": requested_repository,
                "application": requested_application,
                "module": requested_module,
                "feature": _text(payload.get("feature")) or requested_module,
                "matchedSignals": [f"registry:{requested_repository}/{requested_application}/{requested_module}"],
                "routingConfidence": "module_registry",
            }
            return route, candidate, None
        direct_names = {
            _text(repository.get("name")).casefold(),
            _text(repository.get("kind")).casefold(),
        }
        if requested_application.casefold() in direct_names and requested_module.casefold() in _registry_modules(repository):
            raw_index = repository.get("memoryIndex")
            candidate, error = _safe_memory_path(memory_root, index_root, raw_index)
            if error:
                return None, None, error
            route = {
                "routeId": f"registry-fallback:{_slug(requested_repository)}:{_slug(requested_application)}:{_slug(requested_module)}",
                "repository": requested_repository,
                "application": requested_application,
                "module": requested_module,
                "feature": _text(payload.get("feature")) or requested_module,
                "matchedSignals": [f"registry:{requested_repository}/{requested_application}/{requested_module}"],
                "routingConfidence": "module_registry",
            }
            return route, candidate, None
    return None, None, None


def _lookup_memory_context_locked(
    payload: Mapping[str, Any],
    *,
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Route one input through the bounded local memory indexes without writing."""

    current = _now(now)
    root = Path(memory_root).expanduser() if memory_root is not None else default_memory_root()
    # Preserve the configured lexical path in writeback receipts. Filesystem
    # containment checks below still canonicalize paths before comparison.
    root = root.absolute()
    task_id = (_text(payload.get("taskId")) or None) if isinstance(payload, Mapping) else None
    if not isinstance(payload, Mapping):
        return _lookup_receipt(
            outcome="invalid", task_id=None, memory_root=root, query_fingerprint=None,
            route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=["input: expected object"], generated_at=current,
        )
    if not root.is_dir():
        return _lookup_receipt(
            outcome="unavailable", task_id=task_id, memory_root=root, query_fingerprint=None,
            route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=["memory root is unavailable"], generated_at=current,
        )
    signals = _query_signals(payload)
    query_fingerprint = _fingerprint(signals)
    if not signals or privacy_findings({"query": signals}, "memoryLookup"):
        return _lookup_receipt(
            outcome="invalid", task_id=task_id, memory_root=root, query_fingerprint=query_fingerprint,
            route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=["memory query is empty or contains sensitive content"], generated_at=current,
        )

    index_root = root / "autonomous-engineering"
    project_id = _text(payload.get("projectId"))
    repository = _text(payload.get("repository"))
    project_file: Path | None = None
    if project_id and repository:
        candidate = project_memory_path(root, project_id, repository)
        if candidate.exists() or candidate.is_symlink():
            project_file, project_error = _validate_memory_file(
                candidate,
                memory_root=root,
                containment_root=index_root,
                label="project memory",
            )
            if project_error is None and project_file is not None:
                try:
                    read_project_memory(project_file, project_id, repository)
                except (OSError, UnicodeError, ValueError):
                    project_error = "project memory identity, privacy, or format is invalid"
            if project_error:
                return _lookup_receipt(
                    outcome="invalid", task_id=task_id, memory_root=root,
                    query_fingerprint=query_fingerprint, route=None, selected_paths=[],
                    sources=[], registry_status="invalid", errors=[project_error],
                    generated_at=current,
                )
    route_map, route_errors = _load_json(
        index_root / "route-map.json",
        label="route map",
        memory_root=root,
        containment_root=index_root,
    )
    registry, registry_errors = _load_json(
        index_root / "module-registry.json",
        label="module registry",
        memory_root=root,
        containment_root=index_root,
    )
    if route_map is None:
        if project_file is not None and route_errors == ["route map: unavailable"] and registry_errors == ["module registry: unavailable"]:
            return _lookup_receipt(
                outcome="matched", task_id=task_id, memory_root=root,
                query_fingerprint=query_fingerprint,
                route={"routeId": "project-memory-v1", "repository": repository,
                       "application": "project", "module": "project", "feature": "project",
                       "matchedSignals": ["exact project/repository"],
                       "routingConfidence": "exact_scope"},
                selected_paths=[project_file.relative_to(root).as_posix()],
                sources=[_source_receipt(project_file, root)],
                registry_status="project_memory_only", errors=[], generated_at=current,
            )
        return _lookup_receipt(
            outcome="unavailable" if route_errors == ["route map: unavailable"] else "invalid" if any("unsafe" in error for error in route_errors) else "failed",
            task_id=task_id, memory_root=root, query_fingerprint=query_fingerprint,
            route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=[*route_errors, *registry_errors], generated_at=current,
        )
    routes = route_map.get("routes")
    if not isinstance(routes, list) or len(routes) > MAX_ROUTES:
        return _lookup_receipt(
            outcome="invalid", task_id=task_id, memory_root=root, query_fingerprint=query_fingerprint,
            route=None, selected_paths=[], sources=[], registry_status="invalid",
            errors=["route map routes are invalid or exceed the bounded limit"], generated_at=current,
        )
    route_validation_errors = _validate_routes(routes)
    registry_validation_errors = _validate_registry(registry) if registry is not None else []
    if route_validation_errors or registry_validation_errors:
        return _lookup_receipt(
            outcome="invalid", task_id=task_id, memory_root=root, query_fingerprint=query_fingerprint,
            route=None, selected_paths=[], sources=[], registry_status="invalid",
            errors=[*route_validation_errors, *registry_validation_errors, *registry_errors], generated_at=current,
        )

    haystack = " ".join(signals)
    ranked: list[tuple[int, int, str, Mapping[str, Any], list[str]]] = []
    for route in routes:
        if not isinstance(route, Mapping):
            continue
        score, hits = _route_score(route, payload, haystack)
        if score:
            ranked.append((score, max(map(len, hits), default=0), _text(route.get("id")), route, hits))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected_route = ranked[0][3] if ranked else None
    matched_signals = ranked[0][4] if ranked else []

    fallback_route: dict[str, Any] | None = None
    fallback_path: Path | None = None
    fallback_error: str | None = None
    if selected_route is None:
        fallback_route, fallback_path, fallback_error = _registry_fallback(
            registry,
            payload,
            memory_root=root,
            index_root=index_root,
        )

    selected: list[Path] = [project_file] if project_file is not None else []
    errors = [*registry_errors]
    if fallback_error:
        errors.append(fallback_error)
    for index, containment, label in (
        (root / "MEMORY.md", root, "global memory index"),
        (index_root / "MEMORY.md", index_root, "Evidence-driven agentic engineering memory index"),
    ):
        if not index.exists() and not index.is_symlink():
            continue
        safe_index, index_error = _validate_memory_file(
            index,
            memory_root=root,
            containment_root=containment,
            label=label,
        )
        if index_error:
            errors.append(index_error)
        elif safe_index is not None:
            selected.append(safe_index)
    if selected_route is not None:
        raw_paths = list(selected_route.get("initial_memory_paths", []))
        expansion_signals = [_text(value).casefold() for value in selected_route.get("expand_only_if", [])]
        matched_expansions = [signal for signal in expansion_signals if signal and signal in haystack]
        if matched_expansions:
            raw_paths.extend(selected_route.get("backend_expansion_paths", []))
            matched_signals.extend(f"expansion:{signal}" for signal in matched_expansions)
        if not isinstance(raw_paths, list) or len(raw_paths) > MAX_MEMORY_PATHS:
            errors.append("selected route memory paths are invalid or exceed the bounded limit")
        else:
            for raw in raw_paths:
                candidate, error = _safe_memory_path(root, index_root, raw)
                if error:
                    errors.append(error)
                    continue
                if candidate is not None:
                    safe_candidate, candidate_error = _validate_memory_file(
                        candidate,
                        memory_root=root,
                        containment_root=index_root,
                        label="selected memory path",
                    )
                    if candidate_error:
                        errors.append(
                            f"selected memory path is unavailable: {candidate.relative_to(root).as_posix()}"
                            if candidate_error.endswith(": unavailable")
                            else candidate_error
                        )
                    elif safe_candidate is not None:
                        selected.append(safe_candidate)
    elif fallback_path is not None:
        safe_fallback, fallback_path_error = _validate_memory_file(
            fallback_path,
            memory_root=root,
            containment_root=index_root,
            label="selected memory path",
        )
        if fallback_path_error:
            errors.append(f"selected memory path is unavailable: {fallback_path.relative_to(root).as_posix()}")
        elif safe_fallback is not None:
            selected.append(safe_fallback)

    selected = list(dict.fromkeys(selected))
    selected_paths = [path.relative_to(root).as_posix() for path in selected]
    try:
        sources = [_source_receipt(path, root) for path in selected]
    except (OSError, UnicodeError) as error:
        return _lookup_receipt(
            outcome="failed", task_id=task_id, memory_root=root, query_fingerprint=query_fingerprint,
            route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=[f"selected memory cannot be read ({type(error).__name__})"], generated_at=current,
        )

    route_projection = fallback_route
    if selected_route is not None:
        route_projection = {
            "routeId": _text(selected_route.get("id")),
            "repository": _text(selected_route.get("repository")),
            "application": _text(selected_route.get("application")),
            "module": _text(selected_route.get("module")),
            "feature": _text(payload.get("feature")) or _text(selected_route.get("id")),
            "matchedSignals": matched_signals,
            "routingConfidence": "route_map",
        }
    unsafe = any("unsafe" in error for error in errors)
    registry_failed = bool(registry_errors)
    missing_selected = any("selected memory path is unavailable" in error for error in errors)
    registry_status = "matched" if fallback_route is not None else _registry_status(registry, selected_route)
    if project_file is not None and selected_route is None and fallback_route is None:
        registry_status = "project_memory_only"
    stale_registry = selected_route is not None and registry_status in {"application_not_listed", "module_not_listed"}
    outcome = "invalid" if unsafe else "failed" if registry_failed or missing_selected or stale_registry else "matched" if route_projection is not None or project_file is not None else "no_relevant_memory"
    if route_projection is None and project_file is not None:
        route_projection = {
            "routeId": "project-memory-v1", "repository": repository,
            "application": "project", "module": "project", "feature": "project",
            "matchedSignals": ["exact project/repository"],
            "routingConfidence": "exact_scope",
        }
    return _lookup_receipt(
        outcome=outcome,
        task_id=task_id,
        memory_root=root,
        query_fingerprint=query_fingerprint,
        route=route_projection,
        selected_paths=selected_paths,
        sources=sources,
        registry_status=registry_status,
        errors=errors,
        generated_at=current,
    )


def lookup_memory_context(
    payload: Mapping[str, Any],
    *,
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Route one input while pinning one canonical memory generation."""

    root = Path(memory_root).expanduser() if memory_root is not None else default_memory_root()
    if not root.is_dir():
        return _lookup_memory_context_locked(payload, memory_root=root, now=now)
    try:
        with canonical_memory_lock(root, exclusive=False):
            return _lookup_memory_context_locked(payload, memory_root=root, now=now)
    except (OSError, ValueError) as error:
        current = _now(now)
        task_id = (_text(payload.get("taskId")) or None) if isinstance(payload, Mapping) else None
        signals = _query_signals(payload) if isinstance(payload, Mapping) else []
        return _lookup_receipt(
            outcome="failed",
            task_id=task_id,
            memory_root=root,
            query_fingerprint=_fingerprint(signals) if signals else None,
            route=None,
            selected_paths=[],
            sources=[],
            registry_status="unavailable",
            errors=[f"canonical memory lock unavailable ({type(error).__name__})"],
            generated_at=current,
        )


def derive_module_feature_route(payload: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, str]:
    configured = payload.get("memoryRoute") if isinstance(payload.get("memoryRoute"), Mapping) else {}
    path = _text(record.get("fingerprints", {}).get("path")) if isinstance(record.get("fingerprints"), Mapping) else ""
    parts = PurePosixPath(path).parts if path else ()
    derived_module = parts[1] if len(parts) > 1 and parts[0] in {"apps", "libs", "packages"} else parts[0] if parts else "general"
    explicit_module = _text(configured.get("module")) or _text(payload.get("module"))
    explicit_feature = _text(configured.get("feature")) or _text(payload.get("feature"))
    route_id = _text(configured.get("routeId")) or _text(payload.get("memoryRouteId"))
    confidence = "route" if configured else "explicit" if explicit_module or explicit_feature else "derived"
    return {
        "routeId": route_id or "unmatched-source-derived",
        "projectId": _text(configured.get("projectId")) or _text(record.get("projectId")),
        "repository": _text(configured.get("repository")) or _text(record.get("repository")),
        "application": _text(configured.get("application")) or _text(payload.get("application")) or _text(record.get("repository")),
        "module": explicit_module or derived_module,
        "feature": explicit_feature or _text(record.get("canonicalKey")),
        "routingConfidence": confidence,
    }


def _frontmatter_value(value: Any) -> str:
    return json.dumps(_text(value), ensure_ascii=True)


def _scope_binding(record: Mapping[str, Any]) -> dict[str, Any]:
    access_scope = record.get("accessScope") if isinstance(record.get("accessScope"), list) else []
    return {
        "projectId": _text(record.get("projectId")),
        "repository": _text(record.get("repository")),
        "branchOrTree": _text(record.get("branchOrTree")),
        "tenantId": _text(record.get("tenantId")) or None,
        "accessScope": sorted({_text(value) for value in access_scope if _text(value)}),
    }


def _shared_note_scope_allowed(binding: Mapping[str, Any]) -> bool:
    project_id = _text(binding.get("projectId")).casefold()
    access_scope = binding.get("accessScope")
    if binding.get("tenantId") or not project_id or not isinstance(access_scope, list) or not access_scope:
        return False
    expected_scope = f"project:{project_id}:read"
    return len(access_scope) == 1 and _text(access_scope[0]).casefold() == expected_scope


def _render_note(record: Mapping[str, Any], *, task_id: str, route: Mapping[str, Any], note_id: str, generated_at: datetime) -> str:
    binding = _scope_binding(record)
    lines = [
        "---",
        f"id: {_frontmatter_value(note_id)}",
        "kind: ad_hoc_verified_learning",
        "status: source_document_candidate_not_canonical",
        f"project: {_frontmatter_value(record.get('projectId'))}",
        f"tenant_id: {_frontmatter_value(binding['tenantId']) if binding['tenantId'] else 'null'}",
        f"access_scope: {json.dumps(binding['accessScope'], ensure_ascii=True)}",
        f"repository: {_frontmatter_value(route.get('repository'))}",
        f"application: {_frontmatter_value(route.get('application'))}",
        f"module: {_frontmatter_value(route.get('module'))}",
        f"feature: {_frontmatter_value(route.get('feature'))}",
        f"route_id: {_frontmatter_value(route.get('routeId'))}",
        f"task_id: {_frontmatter_value(task_id)}",
        f"record_id: {_frontmatter_value(record.get('id'))}",
        f"canonical_key: {_frontmatter_value(record.get('canonicalKey'))}",
        f"branch_or_tree: {_frontmatter_value(record.get('branchOrTree'))}",
        f"record_status: {_frontmatter_value(record.get('status'))}",
        f"confidence: {_frontmatter_value(record.get('confidence'))}",
        "canonical_effect: \"none\"",
        f"generated_at: {_frontmatter_value(_iso(generated_at))}",
        "---",
        "",
        f"# {_text(route.get('feature')).replace('-', ' ').title()} learning candidate",
        "",
        "## Evidence-backed result",
        "",
        f"- {_text(record.get('claim'))}",
    ]
    if _text(record.get("businessLogic")):
        lines.append(f"- Business logic: {_text(record.get('businessLogic'))}")
    if _text(record.get("fixStrategy")):
        lines.append(f"- Safe strategy: {_text(record.get('fixStrategy'))}")
    lines.extend(["", "## Source evidence", ""])
    for item in record.get("evidence", []):
        lines.append(
            "- "
            + " | ".join(
                [
                    _text(item.get("repository")),
                    _text(item.get("commitOrTree")),
                    _text(item.get("path")),
                    _text(item.get("symbol")),
                    _text(item.get("check")),
                    _text(item.get("sourceType")),
                ]
            )
        )
    files = record.get("filesInvolved", [])
    if files:
        lines.extend(["", "## Files involved", "", *[f"- `{_text(path)}`" for path in files]])
    lines.extend(
        [
            "",
            "## Retrieval and integration target",
            "",
            f"- Repository: `{_text(route.get('repository'))}`",
            f"- Application: `{_text(route.get('application'))}`",
            f"- Module: `{_text(route.get('module'))}`",
            f"- Feature: `{_text(route.get('feature'))}`",
            f"- Route: `{_text(route.get('routeId'))}`",
            f"- Routing confidence: `{_text(route.get('routingConfidence'))}`",
            f"- Project: `{binding['projectId']}`",
            f"- Branch/tree: `{binding['branchOrTree']}`",
            f"- Tenant: `{binding['tenantId'] or 'none'}`",
            f"- Access scope: `{json.dumps(binding['accessScope'], ensure_ascii=True)}`",
            "- Revalidate this candidate against current source before reuse or canonical promotion.",
            "- An ingester must preserve this exact project, repository, branch/tree, tenant, and access-scope binding.",
            "",
            "## Concurrency boundary",
            "",
            "- This unique no-clobber note does not edit canonical MEMORY.md, route-map.json, module-registry.json, or an existing feature document.",
            "- A memory ingester may merge it only after conflict checking and current-source revalidation.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_no_clobber(path: Path, content: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    result: dict[str, Any] | None = None
    try:
        temporary.chmod(0o600)
        try:
            os.link(temporary, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as error:
                result = {
                    "outcome": "partial",
                    "published": True,
                    "error": f"module/feature note directory fsync failed ({type(error).__name__})",
                }
            else:
                result = {"outcome": "queued", "published": True, "error": None}
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                result = {"outcome": "conflict", "published": False, "error": "existing note path is not a regular file"}
            else:
                same = path.read_text(encoding="utf-8") == content
                result = {
                    "outcome": "already_queued" if same else "conflict",
                    "published": False,
                    "error": None if same else "existing note has different content; no overwrite performed",
                }
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError as error:
                previous_error = result.get("error") if result is not None else None
                cleanup_error = f"module/feature note temporary cleanup failed ({type(error).__name__})"
                result = {
                    "outcome": "partial",
                    "published": bool(result and result.get("published")),
                    "error": f"{previous_error}; {cleanup_error}" if previous_error else cleanup_error,
                }
    return result or {"outcome": "blocked", "published": False, "error": "module/feature note was not published"}


def queue_module_feature_update(
    record: Mapping[str, Any],
    *,
    task_id: str,
    route: Mapping[str, Any],
    memory_root: str | Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Queue one sanitized module/feature note without changing canonical memory."""

    current = _now(now)
    root = Path(memory_root).expanduser() if memory_root is not None else default_memory_root()
    # Preserve the configured lexical path in writeback receipts. Filesystem
    # containment checks below still canonicalize paths before comparison.
    root = root.absolute()
    errors: list[str] = []
    checked = validate_lesson(record)
    if not checked["valid"]:
        errors.extend(checked["errors"])
    task_id = _text(task_id)
    if not task_id:
        errors.append("taskId: expected non-empty string")
    normalized = checked["record"] if checked["valid"] else record
    binding = _scope_binding(normalized)
    required_route = {key: _text(route.get(key)) for key in ("routeId", "projectId", "repository", "application", "module", "feature", "routingConfidence")}
    for key, value in required_route.items():
        if not value or not _SAFE_ROUTE_VALUE.fullmatch(value):
            errors.append(f"route.{key}: invalid or unsafe value")
    if privacy_findings(required_route, "moduleFeatureRoute"):
        errors.append("route: sensitive content")
    if checked["valid"] and required_route["projectId"] != binding["projectId"]:
        errors.append("route.projectId: must match lesson projectId")
    if checked["valid"] and required_route["repository"] != binding["repository"]:
        errors.append("route.repository: must match lesson repository")
    if not root.is_dir():
        errors.append("memory root is unavailable")
    base_receipt = {
        "schemaVersion": WRITEBACK_SCHEMA_VERSION,
        "taskId": task_id or None,
        "recordId": record.get("id") if isinstance(record, Mapping) else None,
        "route": required_route,
        "scopeBinding": binding,
        "notePath": None,
        "noteRelativePath": None,
        "canonicalFilesChanged": False,
        "noClobber": True,
        "ingestionRequired": True,
        "generatedAt": _iso(current),
    }
    if errors:
        return {**base_receipt, "outcome": "blocked", "errors": sorted(set(errors)), "sideEffects": []}
    if not _shared_note_scope_allowed(binding):
        return {
            **base_receipt,
            "outcome": "scope_restricted",
            "ingestionRequired": False,
            "errors": [],
            "sideEffects": [],
        }
    identity = "|".join(
        [
            task_id,
            _text(normalized.get("id")),
            _text(normalized.get("contentHash")),
            required_route["repository"],
            required_route["application"],
            required_route["module"],
            required_route["feature"],
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    note_id = f"ae-module-feature-{digest}"
    try:
        note_time = _now(normalized.get("observedAt"))
    except (TypeError, ValueError):
        note_time = current
    timestamp = note_time.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}-{_slug(required_route['feature'])}-{digest}.md"
    notes_root = root / "extensions" / "ad_hoc" / "notes"
    try:
        notes_root.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return {**base_receipt, "outcome": "blocked", "errors": ["ad-hoc note root escapes approved memory root"], "sideEffects": []}
    if _contains_symlink(notes_root, root):
        return {**base_receipt, "outcome": "blocked", "errors": ["ad-hoc note root uses an unsafe symlink"], "sideEffects": []}
    try:
        notes_root.mkdir(parents=True, exist_ok=True)
        if _contains_symlink(notes_root, root):
            raise OSError("symlinked note root")
        notes_root.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return {**base_receipt, "outcome": "blocked", "errors": ["ad-hoc note root is unsafe or unavailable"], "sideEffects": []}
    note_path = notes_root / filename
    content = _render_note(normalized, task_id=task_id, route=required_route, note_id=note_id, generated_at=note_time)
    try:
        write_result = _write_no_clobber(note_path, content)
    except (OSError, UnicodeError) as error:
        return {**base_receipt, "outcome": "blocked", "errors": [f"module/feature note write failed ({type(error).__name__})"], "sideEffects": []}
    outcome = write_result["outcome"]
    errors = [write_result["error"]] if write_result.get("error") else []
    side_effects: list[str] = []
    if outcome == "queued":
        side_effects.append("ad-hoc module/feature memory note queued")
    elif outcome == "partial" and write_result.get("published"):
        side_effects.append("ad-hoc module/feature memory note queued; durability confirmation failed")
    elif outcome == "partial":
        side_effects.append("temporary module/feature note artifact remains; final note not published")
    return {
        **base_receipt,
        "outcome": outcome,
        "notePath": str(note_path),
        "noteRelativePath": note_path.relative_to(root).as_posix(),
        "errors": errors,
        "sideEffects": side_effects,
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


def lookup_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded local-memory routing before source inspection")
    parser.add_argument("--input-json", type=Path, required=True, help="JSON intake packet, or - for stdin")
    parser.add_argument("--memory-root", type=Path, default=None, help="Approved memory root; defaults to shared storage")
    parser.add_argument("--now", default=None, help="Optional ISO-8601 time for deterministic operation")
    args = parser.parse_args(argv)
    try:
        memory_root = (args.memory_root or default_memory_root()).expanduser().resolve(strict=False)
    except ValueError as error:
        parser.error(str(error))
    try:
        result = lookup_memory_context(_read_payload(args.input_json), memory_root=memory_root, now=args.now)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        result = _lookup_receipt(
            outcome="invalid", task_id=None,
            memory_root=memory_root,
            query_fingerprint=None, route=None, selected_paths=[], sources=[], registry_status="unavailable",
            errors=[f"input: {type(error).__name__}"], generated_at=_now(args.now),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["outcome"] in {"matched", "no_relevant_memory"} else 2


__all__ = [
    "LOOKUP_SCHEMA_VERSION",
    "WRITEBACK_SCHEMA_VERSION",
    "default_memory_root",
    "derive_module_feature_route",
    "lookup_memory_context",
    "lookup_main",
    "queue_module_feature_update",
]
