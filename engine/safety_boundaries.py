"""Fail-closed path, capability-input, and privacy boundaries."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


class SafetyBoundaryError(ValueError):
    """Raised when a persistence or capability boundary is unsafe."""


SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|cookie|dsn)\s*[:=]\s*)[^\s,;]+"
)
BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}")
PII_PATTERN = re.compile(r"(?i)(\b(?:email|phone|customer[_-]?id)\s*[:=]\s*)[^\s,;]+")
FORBIDDEN_CAPABILITY_KEYS = {
    "prompt",
    "systemprompt",
    "instructions",
    "callback",
    "callbackurl",
    "command",
    "commands",
    "executable",
    "exec",
    "credential",
    "credentials",
    "secret",
    "token",
    "password",
    "model",
    "modelid",
    "lifecycle",
    "activate",
    "activation",
    "spawn",
    "writeauthority",
    "stateRoot",
}
SENSITIVE_MAPPING_KEYS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "customerid",
    "dsn",
    "email",
    "password",
    "phone",
    "privatekey",
    "secret",
    "token",
}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def canonical_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def validate_protected_path(
    target: str | os.PathLike[str],
    *,
    protected_roots: Iterable[str | os.PathLike[str]] = (),
    allowed_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Reject symlink-aware overlap and paths outside an optional allowlist."""

    raw = Path(target).expanduser()
    resolved = canonical_path(raw)
    errors: list[str] = []
    if raw.is_symlink():
        errors.append("target is a symlink")
    protected = [canonical_path(item) for item in protected_roots]
    for boundary in protected:
        if _inside(resolved, boundary) or _inside(boundary, resolved):
            errors.append(f"target overlaps protected root: {boundary}")
    if allowed_root is not None:
        allowed = canonical_path(allowed_root)
        if not _inside(resolved, allowed):
            errors.append(f"target is outside allowed root: {allowed}")
    return {"valid": not errors, "path": resolved.as_posix(), "errors": errors}


def assert_safe_path(
    target: str | os.PathLike[str],
    *,
    protected_roots: Iterable[str | os.PathLike[str]] = (),
    allowed_root: str | os.PathLike[str] | None = None,
) -> Path:
    result = validate_protected_path(
        target,
        protected_roots=protected_roots,
        allowed_root=allowed_root,
    )
    if not result["valid"]:
        raise SafetyBoundaryError("; ".join(result["errors"]))
    return Path(result["path"])


def redact_value(value: Any) -> Any:
    """Return a portable copy with credentials and common PII removed."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            rendered_key = str(key)
            normalized_key = rendered_key.replace("_", "").replace("-", "").lower()
            result[rendered_key] = (
                "[REDACTED]"
                if normalized_key in SENSITIVE_MAPPING_KEYS
                else redact_value(item)
            )
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if not isinstance(value, str):
        return value
    redacted = SECRET_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
    return PII_PATTERN.sub(r"\1[REDACTED]", redacted)


def validate_untrusted_capability(value: Any, *, path: str = "capability") -> dict[str, Any]:
    """Reject executable/authority-bearing capability proposals before selection."""

    errors: list[str] = []

    def walk(item: Any, location: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                if normalized in {key.replace("_", "").replace("-", "").lower() for key in FORBIDDEN_CAPABILITY_KEYS}:
                    errors.append(f"{location}.{key}: forbidden authority-bearing field")
                walk(child, f"{location}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{location}[{index}]")
        elif isinstance(item, str) and ("://" in item or item.lstrip().startswith(("python ", "bash ", "sh ", "node "))):
            errors.append(f"{location}: executable or remote instruction content is not allowed")

    walk(value, path)
    return {"valid": not errors, "errors": errors}
