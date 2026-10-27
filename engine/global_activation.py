"""Install and verify BuildCrew's reversible global Codex instruction."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping


SCHEMA_VERSION = "global-buildcrew-activation-v1"
BLOCK_START = "<!-- BUILDCREW_GLOBAL_ACTIVATION_START -->"
BLOCK_END = "<!-- BUILDCREW_GLOBAL_ACTIVATION_END -->"
STATE_RELATIVE_PATH = Path("activation") / "global-codex.json"

GLOBAL_INSTRUCTIONS = f"""{BLOCK_START}
## BuildCrew global Codex activation

BuildCrew is the default engineering workflow for every input in every project
in this Codex installation. Do not require the user to mention BuildCrew.

For every input, load the installed BuildCrew skill and classify the request:
simple questions, explanations, status checks, and ordinary conversation use
BuildCrew's lightweight lookup-only route; planning, implementation, testing,
review, debugging, investigation, migration, security, deployment, and release
work use the full evidence-first engineering workflow. Keep the developer in
control, preserve unrelated settings and work, and report unsupported host
capabilities honestly.

The host integration must pass a sanitized intake to BuildCrew's universal
input adapter before responding. Actionable work starts or resumes its exact
task; completed, blocked, or paused work closes the task. This global rule is
host-wide and applies across repositories. Project onboarding and task state
remain in the shared external BUILDCREW_HOME, never in the repository.
{BLOCK_END}"""

ACTIVE_CONTROLLER_MARKERS = (
    "single engineering workflow",
    "lifecycle controller",
    "engineering controller",
    "autonomous engineering",
)


class GlobalActivationError(ValueError):
    """Raised when global activation cannot be applied safely."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def resolve_codex_home(codex_home: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the host's global Codex directory without creating it."""

    if codex_home is not None:
        return Path(codex_home).expanduser().resolve(strict=False)
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise GlobalActivationError("CODEX_HOME must be an absolute path")
        return path.resolve(strict=False)
    return (Path.home() / ".codex").resolve(strict=False)


def resolve_state_root(state_root: str | os.PathLike[str] | None = None) -> Path:
    if state_root is not None:
        path = Path(state_root).expanduser()
    else:
        configured = os.environ.get("BUILDCREW_HOME", "").strip()
        path = Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "buildcrew"
    if not path.is_absolute():
        raise GlobalActivationError("global activation state root must be absolute")
    return path.resolve(strict=False)


def _state_path(state_root: Path) -> Path:
    return state_root / STATE_RELATIVE_PATH


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise GlobalActivationError(f"cannot read {path}: {type(exc).__name__}") from exc


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _without_block(content: str) -> str:
    start = content.find(BLOCK_START)
    if start < 0:
        return content
    end = content.find(BLOCK_END, start)
    if end < 0:
        raise GlobalActivationError("BuildCrew activation block is incomplete")
    end += len(BLOCK_END)
    before = content[:start].rstrip()
    after = content[end:].lstrip()
    if before and after:
        return f"{before}\n\n{after}\n"
    if before:
        return before + "\n"
    if after:
        return after + "\n"
    return ""


def _with_block(content: str) -> str:
    base = _without_block(content).rstrip()
    return f"{base}\n\n{GLOBAL_INSTRUCTIONS}\n" if base else GLOBAL_INSTRUCTIONS + "\n"


def _conflicts(content: str) -> list[str]:
    lowered = content.casefold()
    if BLOCK_START.casefold() in lowered:
        return []
    return [marker for marker in ACTIVE_CONTROLLER_MARKERS if marker in lowered and "disabled" not in lowered]


def validate_global_activation(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["activation state must be an object"]}
    required = {"schemaVersion", "status", "codexHome", "instructionsPath", "stateRoot", "managedHash", "priorHash", "backupPath", "createdAt", "updatedAt"}
    errors.extend(f"missing {key}" for key in sorted(required - set(value)))
    errors.extend(f"unknown {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if value.get("status") not in {"CONFIGURED", "ROLLED_BACK"}:
        errors.append("status is unsupported")
    for key in ("codexHome", "instructionsPath", "stateRoot", "managedHash", "createdAt", "updatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"{key} must be non-empty text")
    if value.get("priorHash") is not None and not isinstance(value.get("priorHash"), str):
        errors.append("priorHash must be text or null")
    if value.get("backupPath") is not None and not isinstance(value.get("backupPath"), str):
        errors.append("backupPath must be text or null")
    return {"valid": not errors, "errors": errors}


def configure_global_activation(
    *,
    codex_home: str | os.PathLike[str] | None = None,
    state_root: str | os.PathLike[str] | None = None,
    allow_controller_conflict: bool = False,
) -> dict[str, Any]:
    """Add or refresh only BuildCrew's managed block in global AGENTS.md."""

    home = resolve_codex_home(codex_home)
    state = resolve_state_root(state_root)
    instructions_path = home / "AGENTS.md"
    existing = _read_text(instructions_path)
    conflicts = _conflicts(existing)
    if conflicts and not allow_controller_conflict:
        raise GlobalActivationError("active controller conflict detected: " + ", ".join(conflicts))
    updated = _with_block(existing)
    prior_hash = _sha256(existing.encode("utf-8")) if instructions_path.exists() else None
    managed_hash = _sha256(updated.encode("utf-8"))
    state_path = _state_path(state)
    backup_path: Path | None = None
    if updated != existing:
        if instructions_path.exists() and instructions_path.is_symlink():
            raise GlobalActivationError("global AGENTS.md is a symlink; refusing to follow it")
        if instructions_path.exists():
            backup_path = state / "rollback" / f"AGENTS.md.{_now().replace(':', '-')}.bak"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(instructions_path, backup_path)
        _atomic_write(instructions_path, updated)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "CONFIGURED",
        "codexHome": home.as_posix(),
        "instructionsPath": instructions_path.as_posix(),
        "stateRoot": state.as_posix(),
        "managedHash": managed_hash,
        "priorHash": prior_hash,
        "backupPath": backup_path.as_posix() if backup_path else None,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _atomic_write(state_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return {"status": "CONFIGURED", "changed": updated != existing, "state": record, "conflicts": conflicts}


def verify_global_activation(*, codex_home: str | os.PathLike[str] | None = None, state_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    home = resolve_codex_home(codex_home)
    state = resolve_state_root(state_root)
    instructions_path = home / "AGENTS.md"
    state_path = _state_path(state)
    content = _read_text(instructions_path)
    state_value: dict[str, Any] | None = None
    if state_path.is_file():
        try:
            state_value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            state_value = None
    checks = {
        "globalInstructionsPresent": BLOCK_START in content and BLOCK_END in content,
        "statePresent": state_value is not None,
        "stateValid": bool(state_value and validate_global_activation(state_value)["valid"]),
        "managedHashMatches": bool(state_value and _sha256(content.encode("utf-8")) == state_value.get("managedHash")),
    }
    return {"status": "CONFIGURED" if all(checks.values()) else "NOT_VERIFIED", "checks": checks, "instructionsPath": instructions_path.as_posix(), "statePath": state_path.as_posix()}


def rollback_global_activation(*, codex_home: str | os.PathLike[str] | None = None, state_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    home = resolve_codex_home(codex_home)
    state = resolve_state_root(state_root)
    instructions_path = home / "AGENTS.md"
    existing = _read_text(instructions_path)
    if BLOCK_START not in existing:
        return {"status": "ROLLED_BACK", "changed": False, "reason": "managed block was not present"}
    updated = _without_block(existing)
    if updated:
        _atomic_write(instructions_path, updated)
    elif instructions_path.exists():
        instructions_path.unlink()
    state_path = _state_path(state)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ROLLED_BACK",
        "codexHome": home.as_posix(),
        "instructionsPath": instructions_path.as_posix(),
        "stateRoot": state.as_posix(),
        "managedHash": _sha256(updated.encode("utf-8")),
        "priorHash": None,
        "backupPath": None,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _atomic_write(state_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return {"status": "ROLLED_BACK", "changed": True, "state": record}
