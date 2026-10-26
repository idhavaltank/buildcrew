"""Explicit, bounded host execution for AE transport contracts.

The core skill stays non-activating by default.  This module provides the
smallest portable bridge from the source contracts to a host-owned adapter:
the host supplies an approved executable, fixed arguments, and a manifest;
AE sends one JSON request over stdin and accepts one sanitized JSON response.

The gateway does not provide credentials, choose policy, widen scope, or
perform external side effects.  It only makes an explicitly configured
transport executable and fail-closed when the host capability is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
from typing import Any, Mapping, Protocol, Sequence


MANIFEST_SCHEMA_VERSION = "host-execution-manifest-v1"
STATUSES = {"AVAILABLE", "UNAVAILABLE", "MISCONFIGURED", "NOT_VERIFIED"}
OPERATIONS = {"native-spawn", "provider-execute"}
MAX_ARGUMENTS = 32
MAX_ARGUMENT_LENGTH = 512
MAX_TIMEOUT_SECONDS = 900
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class HostExecutionError(ValueError):
    """Raised when a host execution manifest or response is unsafe."""


class HostExecutionTransport(Protocol):
    """A transport that executes one explicit host-owned adapter request."""

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one request and return a sanitized result."""


def _error(message: str) -> dict[str, Any]:
    return {"valid": False, "errors": [message]}


def validate_host_execution_manifest(value: Any) -> dict[str, Any]:
    """Validate the exact, portable manifest used by the subprocess gateway."""

    if not isinstance(value, Mapping):
        return _error("hostExecutionManifest: expected object")

    required = {
        "schemaVersion",
        "adapterId",
        "hostId",
        "version",
        "status",
        "operation",
        "executable",
        "arguments",
        "workingDirectory",
        "allowedRoot",
        "environmentKeys",
        "executableSha256",
        "timeoutSeconds",
        "maxOutputBytes",
        "sourceBound",
        "externalSideEffects",
    }
    errors: list[str] = []
    errors.extend(f"hostExecutionManifest: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"hostExecutionManifest: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"hostExecutionManifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")

    for key in ("adapterId", "hostId", "version"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"hostExecutionManifest.{key}: expected non-empty string")

    if value.get("status") not in STATUSES:
        errors.append("hostExecutionManifest.status: unsupported value")
    if value.get("operation") not in OPERATIONS:
        errors.append("hostExecutionManifest.operation: unsupported value")

    executable = value.get("executable")
    if not isinstance(executable, str) or not executable.strip() or not Path(executable).is_absolute():
        errors.append("hostExecutionManifest.executable: expected an absolute path")
    for key in ("workingDirectory", "allowedRoot"):
        path = value.get(key)
        if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
            errors.append(f"hostExecutionManifest.{key}: expected an absolute path")

    arguments = value.get("arguments")
    if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS or not all(
        isinstance(item, str) and item and len(item) <= MAX_ARGUMENT_LENGTH for item in arguments
    ):
        errors.append("hostExecutionManifest.arguments: expected at most 32 non-empty strings")

    environment_keys = value.get("environmentKeys")
    if not isinstance(environment_keys, list) or len(environment_keys) > 32 or not all(
        isinstance(item, str) and ENVIRONMENT_KEY_PATTERN.fullmatch(item) for item in environment_keys
    ):
        errors.append("hostExecutionManifest.environmentKeys: expected safe environment-key names")

    executable_sha256 = value.get("executableSha256")
    if not isinstance(executable_sha256, str) or not SHA256_PATTERN.fullmatch(executable_sha256):
        errors.append("hostExecutionManifest.executableSha256: expected lowercase SHA-256")

    for key, minimum, maximum in (
        ("timeoutSeconds", 1, MAX_TIMEOUT_SECONDS),
        ("maxOutputBytes", 1024, MAX_OUTPUT_BYTES),
    ):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
            errors.append(f"hostExecutionManifest.{key}: expected integer from {minimum} to {maximum}")

    if value.get("sourceBound") is not True:
        errors.append("hostExecutionManifest.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("hostExecutionManifest.externalSideEffects: must be HOST_CONTROLLED")
    return {"valid": not errors, "errors": errors}


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_host_execution_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Check a manifest and its executable without starting the adapter."""

    validation = validate_host_execution_manifest(value)
    if not validation["valid"]:
        return {"status": "MISCONFIGURED", "proof": "; ".join(validation["errors"]), "blockers": validation["errors"]}
    if value["status"] != "AVAILABLE":
        return {
            "status": value["status"],
            "proof": f"host manifest declares {value['status'].lower()} capability",
            "blockers": [f"manifest status is {value['status']}"] if value["status"] != "AVAILABLE" else [],
        }

    executable = Path(value["executable"]).expanduser().resolve()
    working_directory = Path(value["workingDirectory"]).expanduser().resolve()
    allowed_root = Path(value["allowedRoot"]).expanduser().resolve()
    blockers: list[str] = []
    if not executable.is_file() or not os.access(executable, os.X_OK):
        blockers.append("configured executable is missing or not executable")
    if not allowed_root.is_dir():
        blockers.append("configured allowedRoot is missing or not a directory")
    if not working_directory.is_dir():
        blockers.append("configured workingDirectory is missing or not a directory")
    elif not _is_below(working_directory, allowed_root):
        blockers.append("workingDirectory must be below allowedRoot")
    if executable.is_file():
        try:
            if _sha256(executable) != value["executableSha256"]:
                blockers.append("configured executable SHA-256 does not match the manifest")
        except OSError:
            blockers.append("configured executable could not be hashed")
    return {
        "status": "AVAILABLE" if not blockers else "UNAVAILABLE",
        "proof": (
            "validated approved host executable and bounded working directory"
            if not blockers
            else "; ".join(blockers)
        ),
        "blockers": blockers,
    }


class _OutputBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.total = 0
        self.overflow = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes, target: bytearray) -> None:
        with self._lock:
            if self.total + len(chunk) > self.maximum:
                self.overflow = True
                return
            self.total += len(chunk)
            target.extend(chunk)


def _safe_text(value: Any, *, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\x00", "").split())
    return text[:limit] or None


def _safe_text_list(value: Any, *, limit: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        safe = _safe_text(item)
        if safe is not None:
            result.append(safe)
    return result


def _sanitize_adapter_result(value: Mapping[str, Any], operation: str) -> dict[str, Any]:
    """Keep only fields consumed by the existing bridge contracts."""

    result: dict[str, Any] = {}
    for key in ("status", "model"):
        safe = _safe_text(value.get(key), limit=128)
        if safe is not None:
            result[key] = safe
    if isinstance(value.get("live"), bool):
        result["live"] = value["live"]
    if operation == "native-spawn":
        result["checkpoints"] = _safe_text_list(value.get("checkpoints"))
    else:
        result["evidenceRefs"] = _safe_text_list(value.get("evidenceRefs"))
        checkpoint = value.get("checkpoint")
        if isinstance(checkpoint, Mapping) and set(checkpoint) == {"sequence", "state"}:
            sequence = checkpoint.get("sequence")
            state = checkpoint.get("state")
            if (
                isinstance(sequence, int)
                and not isinstance(sequence, bool)
                and sequence >= 0
                and state in {"NONE", "SAVED", "PARTIAL", "FINAL"}
            ):
                result["checkpoint"] = {"sequence": sequence, "state": state}
    result["blockers"] = _safe_text_list(value.get("blockers"))
    return result


class SubprocessHostExecution:
    """Run one fixed command through a host manifest, without shell expansion."""

    def __init__(self, manifest: Mapping[str, Any]):
        validation = validate_host_execution_manifest(manifest)
        if not validation["valid"]:
            raise HostExecutionError("invalid host execution manifest: " + "; ".join(validation["errors"]))
        self.manifest = dict(manifest)

    def _environment(self) -> dict[str, str]:
        # Credentials are never copied wholesale. The host explicitly names
        # the small set of variables its adapter is allowed to inherit.
        environment = {"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C")}
        for key in self.manifest["environmentKeys"]:
            if key in os.environ:
                environment[key] = os.environ[key]
        return environment

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass

    def _run(self, request: Mapping[str, Any]) -> dict[str, Any]:
        probe = probe_host_execution_manifest(self.manifest)
        if probe["status"] != "AVAILABLE":
            return {
                "status": "CAPABILITY_UNAVAILABLE",
                "live": False,
                "blockers": list(probe.get("blockers", [])) or [str(probe["proof"])],
            }

        command = [self.manifest["executable"], *self.manifest["arguments"]]
        payload = (
            json.dumps(dict(request), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            + b"\n"
        )
        process: subprocess.Popen[bytes] | None = None
        stdout = bytearray()
        stderr = bytearray()
        budget = _OutputBudget(int(self.manifest["maxOutputBytes"]))
        timed_out = False
        try:
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            process = subprocess.Popen(
                command,
                cwd=str(Path(self.manifest["workingDirectory"]).expanduser().resolve()),
                env=self._environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name == "posix",
                creationflags=creation_flags,
            )

            def drain(stream: Any, target: bytearray) -> None:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        return
                    budget.append(chunk, target)
                    if budget.overflow and process is not None:
                        self._terminate(process)
                        return

            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()
            assert process.stdin is not None
            process.stdin.write(payload)
            process.stdin.close()
            try:
                process.wait(timeout=int(self.manifest["timeoutSeconds"]))
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process)
                process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=5)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        except (OSError, ValueError) as exc:
            if process is not None:
                self._terminate(process)
            return {
                "status": "FAILED",
                "live": process is not None,
                "blockers": [f"host adapter could not start: {type(exc).__name__}"],
            }

        if timed_out:
            return {"status": "TIMED_OUT", "live": True, "blockers": ["host adapter exceeded its timeout"]}
        if budget.overflow:
            return {"status": "FAILED", "live": True, "blockers": ["host adapter output exceeded its configured limit"]}
        if process.returncode != 0:
            return {
                "status": "FAILED",
                "live": True,
                "blockers": [f"host adapter exited with code {process.returncode}"],
            }

        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {
                "status": "FAILED",
                "live": True,
                "blockers": ["host adapter did not return one JSON object on stdout"],
            }
        if not isinstance(decoded, Mapping):
            return {"status": "FAILED", "live": True, "blockers": ["host adapter returned a non-object JSON value"]}
        result = _sanitize_adapter_result(decoded, self.manifest["operation"])
        result.setdefault("live", True)
        return result

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(request, Mapping):
            return {"status": "FAILED", "blockers": ["host execution request must be an object"]}
        request_with_contract = dict(request)
        request_with_contract["hostExecution"] = {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "operation": self.manifest["operation"],
            "sideEffects": "HOST_CONTROLLED",
        }
        return self._run(request_with_contract)


class SubprocessNativeAgentTransport:
    """Concrete NativeAgentTransport backed by an explicit host adapter."""

    def __init__(self, manifest: Mapping[str, Any]):
        if manifest.get("operation") != "native-spawn":
            raise HostExecutionError("native transport requires operation native-spawn")
        self._runner = SubprocessHostExecution(manifest)

    def spawn(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        response = dict(self._runner.run(request))
        response.setdefault("checkpoints", [])
        response.setdefault("blockers", [])
        return response


class SubprocessProviderTransport:
    """Concrete ProviderTransport backed by an explicit host adapter."""

    def __init__(self, manifest: Mapping[str, Any]):
        if manifest.get("operation") != "provider-execute":
            raise HostExecutionError("provider transport requires operation provider-execute")
        self._runner = SubprocessHostExecution(manifest)

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        response = dict(self._runner.run(request))
        response.setdefault("evidenceRefs", [])
        response.setdefault("checkpoint", {"sequence": 0, "state": "NONE"})
        response.setdefault("blockers", [])
        return response


def build_host_execution_manifest(
    *,
    adapter_id: str,
    host_id: str,
    operation: str,
    executable: str | Path,
    arguments: Sequence[str] = (),
    working_directory: str | Path,
    allowed_root: str | Path,
    timeout_seconds: int = 120,
    max_output_bytes: int = 1024 * 1024,
    environment_keys: Sequence[str] = (),
    status: str = "AVAILABLE",
    version: str = "1.0.0",
) -> dict[str, Any]:
    """Build a manifest from host-owned paths and pin the executable bytes."""

    executable_path = Path(executable).expanduser().resolve()
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "adapterId": adapter_id,
        "hostId": host_id,
        "version": version,
        "status": status,
        "operation": operation,
        "executable": str(executable_path),
        "arguments": list(arguments),
        "workingDirectory": str(Path(working_directory).expanduser().resolve()),
        "allowedRoot": str(Path(allowed_root).expanduser().resolve()),
        "environmentKeys": list(environment_keys),
        "executableSha256": _sha256(executable_path),
        "timeoutSeconds": timeout_seconds,
        "maxOutputBytes": max_output_bytes,
        "sourceBound": True,
        "externalSideEffects": "HOST_CONTROLLED",
    }
    validation = validate_host_execution_manifest(manifest)
    if not validation["valid"]:
        raise HostExecutionError("built host execution manifest is invalid: " + "; ".join(validation["errors"]))
    return manifest


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "HostExecutionError",
    "HostExecutionTransport",
    "SubprocessHostExecution",
    "SubprocessNativeAgentTransport",
    "SubprocessProviderTransport",
    "build_host_execution_manifest",
    "probe_host_execution_manifest",
    "validate_host_execution_manifest",
]
