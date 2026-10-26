"""Read-only AE boundary for declarative Harness capability manifests.

This module deliberately has no Harness runtime integration.  It validates a
manifest, reports its declared/observed states, and creates a bounded,
task-bound projection only when AE supplies the binding.  It never imports a
Harness agent, executes a command, chooses a model, creates state, or writes a
file.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Mapping


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR_PATH = _PACKAGE_ROOT / "scripts" / "validate_capability_manifest.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location("ae_capability_validator", _VALIDATOR_PATH)
if _VALIDATOR_SPEC is None or _VALIDATOR_SPEC.loader is None:  # pragma: no cover - package corruption
    raise ImportError("cannot load the capability manifest validator")
_VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR)
validate_manifest = _VALIDATOR.validate_manifest


SCHEMA_VERSION = "harness-host-bridge-v1"
UNAVAILABLE = "UNAVAILABLE"
NOT_EXECUTED = "NOT_EXECUTED"
_FINGERPRINT_PREFIX = "sha256:"
_FINGERPRINT_HEX_LENGTH = 64


class HarnessBridgeError(ValueError):
    """Raised when a Harness boundary request is invalid or unsafe."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessBridgeError(f"{label} must be non-empty text")
    return value.strip()


def _source_fingerprint(value: Any) -> str:
    value = _text(value, "source_fingerprint")
    if not value.startswith(_FINGERPRINT_PREFIX) or len(value) != len(_FINGERPRINT_PREFIX) + _FINGERPRINT_HEX_LENGTH:
        raise HarnessBridgeError("source_fingerprint must be sha256:<64 lowercase hex characters>")
    if any(character not in "0123456789abcdef" for character in value[len(_FINGERPRINT_PREFIX):]):
        raise HarnessBridgeError("source_fingerprint must be sha256:<64 lowercase hex characters>")
    return value


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    # The digest is metadata for the report; it does not create state.
    import json

    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def inspect_manifest(
    manifest: Any,
    *,
    expected_fingerprints: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and summarize a declarative manifest without side effects."""

    validation = validate_manifest(manifest, expected_fingerprints=expected_fingerprints)
    capabilities = manifest.get("capabilities", []) if isinstance(manifest, Mapping) else []
    summaries: list[dict[str, Any]] = []
    for capability in capabilities if isinstance(capabilities, list) else []:
        if not isinstance(capability, Mapping):
            continue
        status = capability.get("status") if isinstance(capability.get("status"), Mapping) else {}
        capability_errors = [
            error for error in validation["errors"]
            if str(error).startswith(f"manifest.capabilities[") and capability.get("capabilityId", "") in str(error)
        ]
        available = capability.get("availability") == "AVAILABLE"
        capability_valid = validation["valid"] and available
        summaries.append({
            "capabilityId": capability.get("capabilityId"),
            "availability": capability.get("availability", "INVALID"),
            "documented": status.get("documented", UNAVAILABLE),
            "installed": status.get("installed", UNAVAILABLE),
            "runnable": status.get("runnable", UNAVAILABLE),
            "executed": status.get("executed", UNAVAILABLE),
            "eligible": capability_valid,
            "errors": capability_errors,
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "VALID" if validation["valid"] else "INVALID",
        "eligible": bool(validation["eligible"]),
        "manifestFingerprint": _manifest_digest(manifest) if isinstance(manifest, Mapping) else None,
        "capabilities": summaries,
        "errors": list(validation["errors"]),
    }


def project_capability(
    manifest: Mapping[str, Any],
    capability_id: str,
    *,
    task_id: str,
    repository: str,
    source_fingerprint: str,
    ae_grant: bool = False,
    task_category: str | None = None,
    expected_fingerprints: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create a bounded task projection; never returns an executable payload."""

    if not isinstance(manifest, Mapping):
        raise HarnessBridgeError("manifest must be an object")
    task_id = _text(task_id, "task_id")
    repository = _text(repository, "repository")
    source_fingerprint = _source_fingerprint(source_fingerprint)
    capability_id = _text(capability_id, "capability_id")
    if ae_grant is not True:
        raise HarnessBridgeError("AE grant is required for a task projection")

    validation = inspect_manifest(manifest, expected_fingerprints=expected_fingerprints)
    if validation["status"] != "VALID" or not validation["eligible"]:
        raise HarnessBridgeError("manifest is invalid, stale, unavailable, or otherwise ineligible")
    capability = next(
        (item for item in manifest["capabilities"] if item.get("capabilityId") == capability_id),
        None,
    )
    if not isinstance(capability, Mapping):
        raise HarnessBridgeError("capability is not present in the validated manifest")
    categories = capability.get("taskCategories", [])
    if task_category is not None:
        task_category = _text(task_category, "task_category")
        if task_category not in categories:
            raise HarnessBridgeError("task_category is not supported by the capability")
    scope = capability["scope"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "PROJECTED",
        "taskId": task_id,
        "repository": repository,
        "sourceFingerprint": source_fingerprint,
        "capability": {
            "capabilityId": capability["capabilityId"],
            "provider": capability["provider"],
            "owner": capability["owner"],
            "version": capability["version"],
            "fingerprint": capability["fingerprint"],
            "taskCategory": task_category,
            "readWriteClass": capability["readWriteClass"],
            "scopeClass": scope["scopeClass"],
            "maxRepositories": scope["maxRepositories"],
            "maxPaths": scope["maxPaths"],
        },
        "executionStatus": NOT_EXECUTED,
    }


def execution_status() -> dict[str, Any]:
    """Return the explicit result for the intentionally absent runtime."""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": NOT_EXECUTED,
        "available": False,
        "reason": "Harness execution is outside this read-only AE boundary",
    }

