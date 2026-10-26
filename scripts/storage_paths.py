"""Resolve shared storage paths without creating directories or moving data."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys


APP_NAME = "buildcrew"
MEMORY_NAMESPACE = "autonomous-engineering"


@dataclass(frozen=True)
class StoragePaths:
    home: Path
    memory_root: Path
    learning_store: Path
    progress_root: Path
    continuity_root: Path
    worklog_root: Path
    worklog_fallback_root: Path


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _application_home() -> Path:
    user_home = Path.home()
    if sys.platform == "darwin":
        return user_home / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        for name in ("LOCALAPPDATA", "APPDATA"):
            value = _configured_path(name)
            if value is not None and value.is_absolute():
                return value / APP_NAME
        return user_home / "AppData" / "Local" / APP_NAME
    xdg_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_home and Path(xdg_home).is_absolute():
        return Path(xdg_home) / APP_NAME
    return user_home / ".local" / "share" / APP_NAME


def resolve_storage_paths(
    *,
    memory_root: str | Path | None = None,
    learning_store: str | Path | None = None,
    progress_root: str | Path | None = None,
    continuity_root: str | Path | None = None,
    worklog_root: str | Path | None = None,
    worklog_fallback_root: str | Path | None = None,
) -> StoragePaths:
    """Resolve explicit paths, tool-neutral settings, legacy settings, then defaults.

    Legacy settings require an explicit value. Merely having another tool's
    storage on disk never redirects this skill's data. Resolution has no writes.
    """

    configured_home = _configured_path("BUILDCREW_HOME")
    if configured_home is not None and not configured_home.is_absolute():
        raise ValueError("BUILDCREW_HOME must be an absolute path")
    home = configured_home if configured_home is not None else _application_home()

    def select(
        explicit: str | Path | None,
        default: Path,
        environment: str,
        legacy_environment: str,
    ) -> Path:
        if explicit is not None:
            return Path(explicit).expanduser()
        configured = _configured_path(environment)
        if configured is not None:
            return configured
        if configured_home is not None:
            return default
        configured_legacy = _configured_path(legacy_environment)
        return configured_legacy if configured_legacy is not None else default

    memory = select(memory_root, home / "memory", "BUILDCREW_MEMORY_ROOT", "CODEX_MEMORY_ROOT")
    if learning_store is not None:
        store = Path(learning_store).expanduser()
    else:
        configured_store = _configured_path("BUILDCREW_LEARNING_STORE") if memory_root is None else None
        legacy_store = (
            _configured_path("AUTONOMOUS_ENGINEERING_LEARNING_STORE")
            if memory_root is None
            and configured_store is None
            and configured_home is None
            and _configured_path("BUILDCREW_MEMORY_ROOT") is None
            else None
        )
        store = configured_store or legacy_store or memory / MEMORY_NAMESPACE / "learning-store.json"

    return StoragePaths(
        home=home,
        memory_root=memory,
        learning_store=store,
        progress_root=select(
            progress_root,
            home / "progress",
            "BUILDCREW_PROGRESS_ROOT",
            "AUTONOMOUS_ENGINEERING_PROGRESS_ROOT",
        ),
        continuity_root=select(
            continuity_root,
            home / "continuity",
            "BUILDCREW_CONTINUITY_ROOT",
            "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT",
        ),
        worklog_root=select(
            worklog_root, home / "worklogs", "BUILDCREW_WORKLOG_ROOT", "CODEX_DAILY_WORKLOG_ROOT",
        ),
        worklog_fallback_root=select(
            worklog_fallback_root,
            home / "worklogs-fallback",
            "BUILDCREW_WORKLOG_FALLBACK_ROOT",
            "CODEX_DAILY_WORKLOG_FALLBACK_ROOT",
        ),
    )


if __name__ == "__main__":
    try:
        print(json.dumps({key: str(value) for key, value in asdict(resolve_storage_paths()).items()}, indent=2))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
