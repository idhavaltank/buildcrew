"""One-time project onboarding for an unfamiliar repository.

The host calls :func:`inspect_onboarding` on the first input after installing
the skill.  It classifies the repository, returns the appropriate first-time
message, and records only a small marker below an explicit external state
root.  Later inputs read that marker and continue through the normal bounded
memory/context workflow without repeating the introduction.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .context_state import validate_state_root
from .project_context import EXCLUDED_DIRECTORIES, resolve_repository_root
from .safety_boundaries import redact_value


SCHEMA_VERSION = "project-onboarding-v1"
ASSESSMENT_SCHEMA_VERSION = "project-onboarding-assessment-v1"
PROJECT_STATES = {"NEW_PROJECT", "EXISTING_WITHOUT_DOCUMENTATION", "EXISTING_WITH_DOCUMENTATION"}
ONBOARDING_STATUSES = {"PENDING", "COMPLETED"}
MAX_SCAN_FILES = 2_000
MAX_EVIDENCE_PATHS = 20

INTRO_MESSAGE = (
    "Before we proceed, I’ll first establish an understanding of your project "
    "so I can provide accurate, consistent, and context-aware assistance. "
    "Depending on your project’s current state, I’ll review the available "
    "documentation and codebase or gather the initial requirements directly "
    "from you. This initial step may take a little extra time; once the "
    "necessary context is established, future requests will be handled more "
    "efficiently with fewer repetitive questions."
)

SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".dart", ".ex", ".exs", ".go", ".h", ".hpp",
    ".java", ".js", ".jsx", ".kt", ".m", ".mm", ".php", ".py", ".rb", ".rs",
    ".scala", ".sh", ".sql", ".swift", ".ts", ".tsx", ".vue", ".svelte",
}
MANIFEST_NAMES = {
    "Cargo.toml", "Gemfile", "Makefile", "Package.swift", "composer.json",
    "go.mod", "package.json", "pubspec.yaml", "pubspec.yml", "pyproject.toml",
    "requirements.txt", "settings.gradle", "build.gradle", "build.gradle.kts",
    "pom.xml", "mix.exs", "CMakeLists.txt", "*.csproj", "*.sln",
}
DOCUMENTATION_NAMES = {
    "README", "README.md", "README.rst", "ARCHITECTURE.md", "CONTRIBUTING.md",
    "ENGINEERING.md", "SPECIFICATION.md", "TECHNICAL-DESIGN.md", "DESIGN.md",
    "TESTING_GUIDE.md", "AGENTS.md", "AGENT.md", "CLAUDE.md",
}
DOCUMENTATION_DIRECTORIES = {"docs", "documentation", "design", "plans", "specs"}


class ProjectOnboardingError(ValueError):
    """Raised when onboarding state or boundaries are unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, label: str, *, maximum: int = 500, required: bool = True) -> str:
    if not isinstance(value, str):
        if required:
            raise ProjectOnboardingError(f"{label} must be text")
        return ""
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        raise ProjectOnboardingError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ProjectOnboardingError(f"{label} exceeds {maximum} characters")
    if "\x00" in normalized:
        raise ProjectOnboardingError(f"{label} contains a NUL character")
    return normalized


def _safe_scope_id(*, repository_root: Path, project_id: str, host_id: str, installation_id: str) -> str:
    return _fingerprint(
        {
            "repositoryRoot": repository_root.as_posix(),
            "projectId": _text(project_id, "project_id"),
            "hostId": _text(host_id, "host_id"),
            "installationId": _text(installation_id, "installation_id"),
        }
    )[7:]


def _is_manifest(name: str) -> bool:
    return name in MANIFEST_NAMES or any(name.endswith(pattern.removeprefix("*")) for pattern in MANIFEST_NAMES if pattern.startswith("*"))


def _is_documentation(path: Path, root: Path) -> bool:
    if path.name in DOCUMENTATION_NAMES:
        return True
    relative = path.relative_to(root)
    return bool(relative.parts and relative.parts[0].casefold() in DOCUMENTATION_DIRECTORIES and path.suffix.casefold() in {".md", ".rst", ".adoc", ".txt"})


def classify_project_state(repository_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Classify a repository using bounded, read-only filesystem evidence."""

    root = resolve_repository_root(repository_root)
    source_paths: list[str] = []
    manifest_paths: list[str] = []
    documentation_paths: list[str] = []
    scanned = 0
    truncated = False

    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in sorted(directories)
            if name not in EXCLUDED_DIRECTORIES and not (current_path / name).is_symlink()
        ]
        for filename in sorted(filenames):
            if scanned >= MAX_SCAN_FILES:
                truncated = True
                break
            scanned += 1
            path = current_path / filename
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix.casefold() in SOURCE_EXTENSIONS:
                source_paths.append(relative)
            if _is_manifest(filename):
                manifest_paths.append(relative)
            if _is_documentation(path, root):
                documentation_paths.append(relative)
        if truncated:
            break

    implementation_paths = sorted(set(source_paths + manifest_paths))
    documentation_paths = sorted(set(documentation_paths))
    has_implementation = bool(implementation_paths)
    has_documentation = bool(documentation_paths)
    if not has_implementation:
        project_state = "NEW_PROJECT"
        next_action = "Gather the project purpose, requirements, technical stack, scope, and priorities before implementation."
    elif has_documentation:
        project_state = "EXISTING_WITH_DOCUMENTATION"
        next_action = "Review the relevant documentation and validate it against the current implementation before proceeding."
    else:
        project_state = "EXISTING_WITHOUT_DOCUMENTATION"
        next_action = "Inspect the structure, dependencies, configuration, and execution path, then create reusable project context."

    evidence = sorted(set(implementation_paths + documentation_paths))[:MAX_EVIDENCE_PATHS]
    gaps = ["bounded project scan reached its file limit"] if truncated else []
    return {
        "schemaVersion": ASSESSMENT_SCHEMA_VERSION,
        "projectState": project_state,
        "hasImplementation": has_implementation,
        "hasDocumentation": has_documentation,
        "scannedFiles": scanned,
        "evidencePaths": evidence,
        "gaps": gaps,
        "nextAction": next_action,
        "sourceOnly": True,
        "sideEffects": [],
    }


def _state_path(state_root: str | os.PathLike[str], repository_root: Path, scope_id: str) -> Path:
    external_root = validate_state_root(state_root, repository_root)
    return external_root / "project-onboarding" / f"{scope_id}.json"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectOnboardingError(f"onboarding state is unreadable: {type(exc).__name__}") from exc
    return value if isinstance(value, dict) else None


def validate_onboarding_state(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["onboardingState: expected object"]}
    required = {
        "schemaVersion", "scopeId", "projectId", "hostId", "installationId", "projectState",
        "status", "introShown", "repositoryFingerprint", "assessment", "context", "createdAt", "updatedAt",
        "sideEffects",
    }
    errors.extend(f"onboardingState: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"onboardingState: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"onboardingState.schemaVersion: expected {SCHEMA_VERSION}")
    for key in ("scopeId", "projectId", "hostId", "installationId", "repositoryFingerprint", "createdAt", "updatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"onboardingState.{key}: expected non-empty string")
    if value.get("projectState") not in PROJECT_STATES:
        errors.append("onboardingState.projectState: unsupported value")
    if value.get("status") not in ONBOARDING_STATUSES:
        errors.append("onboardingState.status: unsupported value")
    if not isinstance(value.get("introShown"), bool):
        errors.append("onboardingState.introShown: expected boolean")
    if not isinstance(value.get("assessment"), Mapping):
        errors.append("onboardingState.assessment: expected object")
    if value.get("context") is not None and not isinstance(value.get("context"), Mapping):
        errors.append("onboardingState.context: expected object or null")
    if value.get("sideEffects") != []:
        errors.append("onboardingState.sideEffects: must be empty")
    return {"valid": not errors, "errors": errors}


def _new_state(
    *,
    scope_id: str,
    project_id: str,
    host_id: str,
    installation_id: str,
    assessment: Mapping[str, Any],
    status: str,
    context: Mapping[str, Any] | None,
    repository_root: Path,
    now: str,
) -> dict[str, Any]:
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "scopeId": scope_id,
        "projectId": project_id,
        "hostId": host_id,
        "installationId": installation_id,
        "projectState": assessment["projectState"],
        "status": status,
        "introShown": True,
        "repositoryFingerprint": _fingerprint({"path": repository_root.as_posix(), "projectState": assessment["projectState"]}),
        "assessment": dict(assessment),
        "context": dict(context) if context is not None else None,
        "createdAt": now,
        "updatedAt": now,
        "sideEffects": [],
    }
    check = validate_onboarding_state(state)
    if not check["valid"]:
        raise ProjectOnboardingError("generated onboarding state failed validation: " + "; ".join(check["errors"]))
    return state


def inspect_onboarding(
    repository_root: str | os.PathLike[str],
    *,
    state_root: str | os.PathLike[str],
    project_id: str,
    host_id: str,
    installation_id: str,
) -> dict[str, Any]:
    """Return the one-time onboarding decision for a host/project scope."""

    root = resolve_repository_root(repository_root)
    project = _text(project_id, "project_id")
    host = _text(host_id, "host_id")
    installation = _text(installation_id, "installation_id")
    assessment = classify_project_state(root)
    scope_id = _safe_scope_id(repository_root=root, project_id=project, host_id=host, installation_id=installation)
    path = _state_path(state_root, root, scope_id)
    existing = _read_state(path)
    if existing is not None:
        check = validate_onboarding_state(existing)
        if not check["valid"]:
            raise ProjectOnboardingError("stored onboarding state failed validation: " + "; ".join(check["errors"]))
        if existing["status"] == "COMPLETED":
            return {
                "schemaVersion": ASSESSMENT_SCHEMA_VERSION,
                "status": "COMPLETED",
                "needsOnboarding": False,
                "showIntro": False,
                "message": None,
                "projectState": existing["projectState"],
                "assessment": assessment,
                "state": existing,
                "persistence": "external",
                "sideEffects": [],
            }
        return {
            "schemaVersion": ASSESSMENT_SCHEMA_VERSION,
            "status": "PENDING",
            "needsOnboarding": True,
            "showIntro": False,
            "message": None,
            "projectState": existing["projectState"],
            "assessment": assessment,
            "state": existing,
            "persistence": "external",
            "sideEffects": [],
        }

    now = _now()
    state = _new_state(
        scope_id=scope_id,
        project_id=project,
        host_id=host,
        installation_id=installation,
        assessment=assessment,
        status="PENDING",
        context=None,
        repository_root=root,
        now=now,
    )
    try:
        _write_json(path, state)
        persistence = "external"
    except (OSError, ProjectOnboardingError) as exc:
        persistence = "unavailable"
        state["persistenceLimitation"] = f"onboarding marker was not persisted: {type(exc).__name__}"
    return {
        "schemaVersion": ASSESSMENT_SCHEMA_VERSION,
        "status": "PENDING",
        "needsOnboarding": True,
        "showIntro": True,
        "message": INTRO_MESSAGE,
        "projectState": assessment["projectState"],
        "assessment": assessment,
        "state": state,
        "persistence": persistence,
        "sideEffects": [],
    }


def _sanitize_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ProjectOnboardingError("context must be an object")
    allowed = {"summary", "decisions", "references", "nextAction"}
    unknown = set(context) - allowed
    if unknown:
        raise ProjectOnboardingError("context contains unsupported fields: " + ", ".join(sorted(map(str, unknown))))
    summary = _text(redact_value(context.get("summary")), "context.summary", maximum=2_000)
    next_action = _text(redact_value(context.get("nextAction")), "context.nextAction", maximum=500)
    decisions = context.get("decisions", [])
    references = context.get("references", [])
    if not isinstance(decisions, list) or not all(isinstance(item, str) for item in decisions):
        raise ProjectOnboardingError("context.decisions must be a string array")
    if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
        raise ProjectOnboardingError("context.references must be a string array")
    return {
        "summary": summary,
        "decisions": [_text(redact_value(item), "context.decisions[]", maximum=500) for item in decisions[:32]],
        "references": [_text(redact_value(item), "context.references[]", maximum=500) for item in references[:32]],
        "nextAction": next_action,
    }


def complete_onboarding(
    repository_root: str | os.PathLike[str],
    *,
    state_root: str | os.PathLike[str],
    project_id: str,
    host_id: str,
    installation_id: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist sanitized project context and suppress future introductions."""

    root = resolve_repository_root(repository_root)
    project = _text(project_id, "project_id")
    host = _text(host_id, "host_id")
    installation = _text(installation_id, "installation_id")
    assessment = classify_project_state(root)
    scope_id = _safe_scope_id(repository_root=root, project_id=project, host_id=host, installation_id=installation)
    path = _state_path(state_root, root, scope_id)
    existing = _read_state(path)
    sanitized_context = _sanitize_context(context)
    if existing is not None:
        check = validate_onboarding_state(existing)
        if not check["valid"]:
            raise ProjectOnboardingError("stored onboarding state failed validation: " + "; ".join(check["errors"]))
    now = _now()
    created_at = existing.get("createdAt", now) if isinstance(existing, Mapping) else now
    state = _new_state(
        scope_id=scope_id,
        project_id=project,
        host_id=host,
        installation_id=installation,
        assessment=assessment,
        status="COMPLETED",
        context=sanitized_context,
        repository_root=root,
        now=created_at,
    )
    state["updatedAt"] = now
    _write_json(path, state)
    return {
        "schemaVersion": ASSESSMENT_SCHEMA_VERSION,
        "status": "COMPLETED",
        "needsOnboarding": False,
        "showIntro": False,
        "message": None,
        "projectState": assessment["projectState"],
        "assessment": assessment,
        "state": state,
        "persistence": "external",
        "sideEffects": [],
    }


__all__ = [
    "INTRO_MESSAGE",
    "ProjectOnboardingError",
    "classify_project_state",
    "complete_onboarding",
    "inspect_onboarding",
    "validate_onboarding_state",
]
