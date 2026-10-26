"""Real browser, Android, iOS Simulator, and Flutter QA transports.

The AE QA contracts stay dependency-free, while this module provides explicit
host transports for Playwright and ADB.  A transport is never auto-created:
the caller must supply an approved runtime, target allowlist, and artifact
directory.  Missing runtimes return UNAVAILABLE/NOT_VERIFIED instead of a
simulated pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit


_SAFE_PART = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_SERIAL = re.compile(r"^[A-Za-z0-9._:-]+$")


class RealQAAdapterError(ValueError):
    """Raised when a real host transport is configured unsafely."""


def _safe_part(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160 or not _SAFE_PART.fullmatch(text):
        raise RealQAAdapterError(f"{label} contains unsafe characters")
    return text


def _redact(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?:[A-Za-z]:[\\/]|/(?:home|tmp|var|workspace)/)[^\s,;]+", "[HOST_PATH]", text)
    return text[:2000]


def _logical_ref(kind: str, run_id: str, step_id: str, suffix: str) -> str:
    return f"{kind}/{_safe_part(run_id, 'runId')}/{_safe_part(step_id, 'stepId')}.{suffix}"


def _origin(value: str) -> str:
    if value == "data:":
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RealQAAdapterError("browser allowlist entries must be http(s) origins or data:")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RealQAAdapterError("browser allowlist entries must not contain a path, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}"


def _url_is_allowed(value: str, allowed_origins: Sequence[str]) -> bool:
    if value.startswith("data:"):
        return "data:" in allowed_origins
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return f"{parsed.scheme}://{parsed.netloc}" in allowed_origins


class PlaywrightBrowserTransport:
    """Invoke an approved Playwright Node runtime through a bounded helper."""

    def __init__(
        self,
        *,
        node_modules_root: str | Path,
        artifacts_dir: str | Path,
        allowed_origins: Sequence[str],
        browser_name: str = "chromium",
        executable_path: str | Path | None = None,
        node_binary: str = "node",
        helper_script: str | Path | None = None,
        headless: bool = True,
    ) -> None:
        self.node_modules_root = Path(node_modules_root).expanduser()
        self.artifacts_dir = Path(artifacts_dir).expanduser()
        self.allowed_origins = tuple(_origin(item.strip()) for item in allowed_origins if str(item).strip())
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise RealQAAdapterError("browser_name must be chromium, firefox, or webkit")
        self.browser_name = browser_name
        self.executable_path = str(Path(executable_path).expanduser()) if executable_path else None
        self.node_binary = node_binary
        self.helper_script = Path(helper_script).expanduser() if helper_script else Path(__file__).resolve().parents[1] / "scripts" / "qa_playwright_runner.cjs"
        self.headless = bool(headless)

    @property
    def configured(self) -> bool:
        module_root = self.node_modules_root / "playwright" if self.node_modules_root.name != "playwright" else self.node_modules_root
        return bool(
            shutil.which(self.node_binary)
            and module_root.is_dir()
            and self.helper_script.is_file()
            and self.allowed_origins
            and (not self.executable_path or Path(self.executable_path).is_file())
        )

    def _payload(self, mode: str, request: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "mode": mode,
            "request": dict(request),
            "steps": [dict(step) for step in steps],
            "playwrightRoot": str(self.node_modules_root),
            "artifactsDir": str(self.artifacts_dir),
            "allowedOrigins": list(self.allowed_origins),
            "browserName": self.browser_name,
            "executablePath": self.executable_path,
            "headless": self.headless,
        }

    def _execute(self, mode: str, request: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "UNAVAILABLE",
                "blockers": ["Playwright Node runtime, helper, executable, or browser allowlist is unavailable"],
            }
        if mode == "run":
            for step in steps:
                action = str(step.get("action", "")).strip().lower()
                if action in {"open", "navigate"} and not _url_is_allowed(str(step.get("entryPoint", "")), self.allowed_origins):
                    return {"status": "BLOCKED", "blockers": [f"browser URL is outside the approved origin allowlist for step {step.get('stepId', 'unknown')}"]}
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        command = [self.node_binary, str(self.helper_script)]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(self._payload(mode, request, steps), ensure_ascii=True),
                text=True,
                capture_output=True,
                check=False,
                timeout=max(10, int(request.get("timeoutSeconds", 30)) + 15),
                env={**os.environ, "AUTONOMOUS_ENGINEERING_PLAYWRIGHT_ROOT": str(self.node_modules_root)},
            )
        except subprocess.TimeoutExpired:
            return {"status": "NOT_VERIFIED", "blockers": ["Playwright host run timed out"]}
        except OSError as exc:
            return {"status": "UNAVAILABLE", "blockers": [f"Playwright host process unavailable: {type(exc).__name__}"]}
        output = completed.stdout.strip()
        try:
            result = json.loads(output) if output else {}
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, Mapping):
            result = {}
        if completed.returncode != 0:
            blockers = [str(item) for item in result.get("blockers", []) if isinstance(item, str)]
            if not blockers:
                blockers = [f"Playwright host process exited with code {completed.returncode}"]
            stderr = _redact(completed.stderr.strip())
            if stderr:
                blockers.append(stderr)
            return {"status": "NOT_VERIFIED", "blockers": blockers}
        return dict(result)

    def probe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._execute("probe", request, [])

    def run_workflow(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        return self._execute("run", request, workflow_steps)

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "NOT_VERIFIED", "blockers": ["Playwright workflow steps were not supplied"]}


CommandRunner = Callable[[Sequence[str], int, bool], tuple[int, bytes, bytes]]


class ADBAndroidTransport:
    """Run bounded Android emulator/device workflows through explicit ADB."""

    def __init__(
        self,
        *,
        serial: str,
        artifacts_dir: str | Path,
        adb_binary: str = "adb",
        apk_path: str | Path | None = None,
        package_name: str | None = None,
        activity_name: str | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.serial = _safe_part(serial, "android serial")
        self.artifacts_dir = Path(artifacts_dir).expanduser()
        self.adb_binary = adb_binary
        self.apk_path = Path(apk_path).expanduser() if apk_path else None
        self.package_name = package_name.strip() if package_name else None
        self.activity_name = activity_name.strip() if activity_name else None
        self.command_runner = command_runner

    @property
    def configured(self) -> bool:
        return bool(self.command_runner is not None or shutil.which(self.adb_binary) or Path(self.adb_binary).is_file())

    def _run(self, args: Sequence[str], timeout: int, *, binary: bool = False) -> tuple[int, bytes, bytes]:
        command = [self.adb_binary, "-s", self.serial, *[str(item) for item in args]]
        if self.command_runner is not None:
            return self.command_runner(command, timeout, binary)
        completed = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
        stdout = completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout).encode()
        stderr = completed.stderr if isinstance(completed.stderr, bytes) else str(completed.stderr).encode()
        return completed.returncode, stdout, stderr

    def _text(self, args: Sequence[str], timeout: int) -> str:
        code, stdout, stderr = self._run(args, timeout)
        if code != 0:
            raise RealQAAdapterError(_redact(stderr.decode("utf-8", "replace")) or f"adb exited with code {code}")
        return stdout.decode("utf-8", "replace")

    def _identity(self, request: Mapping[str, Any], timeout: int = 30) -> dict[str, str]:
        state = self._text(["get-state"], timeout).strip()
        if state != "device":
            raise RealQAAdapterError(f"ADB target state is {state or 'unknown'}, expected device")
        version = self._text(["shell", "getprop", "ro.build.version.release"], timeout).strip() or "unknown"
        build = self._text(["shell", "getprop", "ro.build.display.id"], timeout).strip() or "unknown"
        target = str(request.get("targetIdentity", {}).get("name", self.serial))
        return {"name": target, "version": version, "build": build, "environment": f"android:{self.serial}"}

    def probe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["adb executable is unavailable"]}
        try:
            identity = self._identity(request)
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}
        return {"status": "AVAILABLE", "runtimeIdentity": identity}

    def _write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _step_ref(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> str:
        return _logical_ref("android", str(request["runId"]), str(step["stepId"]), suffix)

    def _artifact_path(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> Path:
        return self.artifacts_dir / _safe_part(request["runId"], "runId") / f"{_safe_part(step['stepId'], 'stepId')}.{suffix}"

    def _parse_coordinates(self, value: str) -> tuple[int, int]:
        try:
            parsed = json.loads(value)
            x, y = int(parsed["x"]), int(parsed["y"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            match = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*", value)
            if not match:
                raise RealQAAdapterError("tap entryPoint must be JSON {x,y} or x,y")
            x, y = int(match.group(1)), int(match.group(2))
        if x < 0 or y < 0 or x > 10000 or y > 10000:
            raise RealQAAdapterError("tap coordinates are outside the safe range")
        return x, y

    def run_workflow(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["adb executable is unavailable"]}
        try:
            identity = self._identity(request)
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}

        run_dir = self.artifacts_dir / _safe_part(request["runId"], "runId")
        run_dir.mkdir(parents=True, exist_ok=True)
        command_log: list[dict[str, str]] = []
        artifact_refs: list[str] = []
        step_results: list[dict[str, Any]] = []
        blockers: list[str] = []
        for step in workflow_steps:
            step_id = str(step["stepId"])
            action = str(step.get("action", "")).strip().lower()
            refs: list[str] = []
            status = "PASS"
            try:
                external_state = str(request.get("externalState", "NONE"))
                if action in {"install", "clear_data"} and external_state not in {"DISPOSABLE_ONLY", "EXPLICIT_APPROVAL"}:
                    raise RealQAAdapterError(f"Android action {action} requires disposable or explicitly approved external state")
                if action == "install":
                    if self.apk_path is None or not self.apk_path.is_file():
                        raise RealQAAdapterError("Android install requires an existing explicitly configured APK path")
                    self._text(["install", "-r", str(self.apk_path)], int(request["timeoutSeconds"]))
                    command_log.append({"stepId": step_id, "action": "install", "status": "PASS"})
                elif action in {"launch", "open"}:
                    if not self.package_name:
                        raise RealQAAdapterError("Android launch requires an explicitly configured package name")
                    self._text(["shell", "monkey", "-p", self.package_name, "1"], int(request["timeoutSeconds"]))
                    command_log.append({"stepId": step_id, "action": "launch", "status": "PASS"})
                elif action == "tap":
                    x, y = self._parse_coordinates(str(step.get("entryPoint", "")))
                    self._text(["shell", "input", "tap", str(x), str(y)], int(request["timeoutSeconds"]))
                    command_log.append({"stepId": step_id, "action": "tap", "status": "PASS"})
                elif action in {"input_text", "type"}:
                    value = str(step.get("entryPoint", ""))
                    if not value or any(ord(char) < 32 for char in value):
                        raise RealQAAdapterError("Android input_text requires printable text")
                    encoded = value.replace(" ", "%s")
                    self._text(["shell", "input", "text", encoded], int(request["timeoutSeconds"]))
                    command_log.append({"stepId": step_id, "action": "input_text", "status": "PASS"})
                elif action == "screenshot":
                    code, stdout, stderr = self._run(["exec-out", "screencap", "-p"], int(request["timeoutSeconds"]), binary=True)
                    if code != 0 or not stdout.startswith(b"\x89PNG"):
                        raise RealQAAdapterError(_redact(stderr.decode("utf-8", "replace")) or "ADB did not return a PNG screenshot")
                    self._write(self._artifact_path(request, step, "png"), stdout)
                    refs.append(self._step_ref(request, step, "png"))
                    artifact_refs.append(refs[-1])
                elif action == "assert_text":
                    dump = self._text(["shell", "uiautomator", "dump", "/dev/tty"], int(request["timeoutSeconds"]))
                    expected = str(step.get("expected", ""))
                    if expected not in dump:
                        raise RealQAAdapterError("expected text was not present in the Android UI hierarchy")
                    command_log.append({"stepId": step_id, "action": "assert_text", "status": "PASS"})
                elif action == "wait":
                    seconds = float(step.get("entryPoint", "0"))
                    if seconds < 0 or seconds > 30:
                        raise RealQAAdapterError("Android wait must be between 0 and 30 seconds")
                    time.sleep(seconds)
                    command_log.append({"stepId": step_id, "action": "wait", "status": "PASS"})
                elif action == "clear_data":
                    if not self.package_name:
                        raise RealQAAdapterError("Android clear_data requires an explicitly configured package name")
                    self._text(["shell", "pm", "clear", self.package_name], int(request["timeoutSeconds"]))
                    command_log.append({"stepId": step_id, "action": "clear_data", "status": "PASS"})
                else:
                    raise RealQAAdapterError(f"unsupported Android QA action: {action}")

                required = {str(item).lower() for item in step.get("evidenceRequired", [])}
                if "device" in required:
                    ref = self._step_ref(request, step, "device.json")
                    self._write(self._artifact_path(request, step, "device.json"), json.dumps(identity, sort_keys=True).encode())
                    refs.append(ref)
                    artifact_refs.append(ref)
                if "command" in required or "log" in required:
                    ref = self._step_ref(request, step, "command.json")
                    self._write(self._artifact_path(request, step, "command.json"), json.dumps(command_log, sort_keys=True).encode())
                    refs.append(ref)
                    artifact_refs.append(ref)
                unsupported = required - {"device", "command", "log", "screenshot"}
                if unsupported:
                    raise RealQAAdapterError(f"unsupported Android evidence kinds: {', '.join(sorted(unsupported))}")
                if not refs and required:
                    raise RealQAAdapterError("Android step did not produce the requested evidence")
            except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
                status = "FAILED"
                blockers.append(f"{step_id}: {_redact(str(exc)) or type(exc).__name__}")
            step_results.append({"stepId": step_id, "status": status, "evidenceRefs": sorted(set(refs))})

        command_ref = f"android/{_safe_part(request['runId'], 'runId')}/run.command.json"
        self._write(run_dir / "run.command.json", json.dumps(command_log, sort_keys=True).encode())
        artifact_refs.append(command_ref)
        if any(item["status"] != "PASS" for item in step_results):
            status = "FAILED" if any(item["status"] == "FAILED" for item in step_results) else "NOT_VERIFIED"
        else:
            status = "PASS"
        return {
            "status": status,
            "targetIdentity": identity,
            "stepResults": step_results,
            "artifactRefs": sorted(set(artifact_refs)),
            "blockers": sorted(set(blockers)),
        }


class FlutterRuntimeTransport:
    """Run explicit Flutter tests against one host-selected device."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        artifacts_dir: str | Path,
        device_id: str,
        target_platform: str = "android",
        flutter_binary: str = "flutter",
        command_runner: CommandRunner | None = None,
    ) -> None:
        if target_platform not in {"android", "ios"}:
            raise RealQAAdapterError("Flutter target_platform must be android or ios")
        self.project_root = Path(project_root).expanduser().resolve()
        self.artifacts_dir = Path(artifacts_dir).expanduser()
        self.device_id = _safe_part(device_id, "Flutter device id")
        self.target_platform = target_platform
        self.flutter_binary = flutter_binary
        self.command_runner = command_runner

    @property
    def configured(self) -> bool:
        return bool(
            self.project_root.is_dir()
            and (self.command_runner is not None or shutil.which(self.flutter_binary) or Path(self.flutter_binary).is_file())
        )

    def _run(self, args: Sequence[str], timeout: int, *, binary: bool = False) -> tuple[int, bytes, bytes]:
        command = [self.flutter_binary, *[str(item) for item in args]]
        if self.command_runner is not None:
            return self.command_runner(command, timeout, binary)
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        stdout = completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout).encode()
        stderr = completed.stderr if isinstance(completed.stderr, bytes) else str(completed.stderr).encode()
        return completed.returncode, stdout, stderr

    def _devices(self, timeout: int) -> list[Mapping[str, Any]]:
        code, stdout, stderr = self._run(["devices", "--machine"], timeout)
        if code != 0:
            raise RealQAAdapterError(_redact(stderr.decode("utf-8", "replace")) or f"flutter devices exited with code {code}")
        try:
            value = json.loads(stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise RealQAAdapterError("Flutter device discovery returned invalid JSON") from exc
        if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
            raise RealQAAdapterError("Flutter device discovery returned an invalid device list")
        return list(value)

    def _identity(self, timeout: int = 30) -> dict[str, str]:
        for device in self._devices(timeout):
            if str(device.get("id", "")) != self.device_id:
                continue
            target_platform = str(device.get("targetPlatform", "")).lower()
            if self.target_platform not in target_platform:
                raise RealQAAdapterError(
                    f"Flutter target {self.device_id} is not a {self.target_platform} device"
                )
            return {
                "name": self.device_id,
                "version": str(device.get("version") or device.get("sdk") or "unknown"),
                "build": target_platform or "flutter",
                "environment": f"flutter:{self.target_platform}:{self.device_id}",
            }
        raise RealQAAdapterError(f"Flutter target {self.device_id} was not found")

    def probe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["Flutter project or executable is unavailable"]}
        try:
            return {"status": "AVAILABLE", "runtimeIdentity": self._identity(int(request.get("timeoutSeconds", 30)))}
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}

    def _project_file(self, value: Any, label: str) -> tuple[Path, str]:
        relative = str(value or "").strip()
        if not relative or relative.startswith(("/", "\\")):
            raise RealQAAdapterError(f"{label} must be a project-relative file")
        candidate = (self.project_root / relative).resolve()
        try:
            relative_path = candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise RealQAAdapterError(f"{label} escapes the Flutter project root") from exc
        if not candidate.is_file():
            raise RealQAAdapterError(f"{label} does not exist")
        return candidate, relative_path.as_posix()

    def _artifact_path(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> Path:
        return self.artifacts_dir / _safe_part(request["runId"], "runId") / f"{_safe_part(step['stepId'], 'stepId')}.{suffix}"

    def _write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _step_ref(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> str:
        return _logical_ref("flutter", str(request["runId"]), str(step["stepId"]), suffix)

    def run_workflow(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["Flutter project or executable is unavailable"]}
        try:
            identity = self._identity(int(request.get("timeoutSeconds", 30)))
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}

        run_dir = self.artifacts_dir / _safe_part(request["runId"], "runId")
        run_dir.mkdir(parents=True, exist_ok=True)
        command_log: list[dict[str, Any]] = []
        artifact_refs: list[str] = []
        step_results: list[dict[str, Any]] = []
        blockers: list[str] = []
        for step in workflow_steps:
            step_id = str(step["stepId"])
            action = str(step.get("action", "")).strip().lower()
            required = {str(item).lower() for item in step.get("evidenceRequired", [])}
            refs: list[str] = []
            step_status = "PASS"
            try:
                timeout = int(request["timeoutSeconds"])
                output = b""
                error = b""
                if action in {"run_test", "test", "drive"}:
                    _path, relative = self._project_file(step.get("entryPoint"), "Flutter test entryPoint")
                    code, output, error = self._run(["test", relative, "-d", self.device_id, "--machine"], timeout)
                    command_log.append({"stepId": step_id, "action": action, "exitCode": code})
                    if code != 0:
                        raise RealQAAdapterError(_redact(error.decode("utf-8", "replace")) or "Flutter test failed")
                elif action == "screenshot":
                    output_path = self._artifact_path(request, step, "png")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    code, output, error = self._run(["screenshot", "--out", str(output_path), "-d", self.device_id], timeout)
                    command_log.append({"stepId": step_id, "action": action, "exitCode": code})
                    if code != 0 or not output_path.is_file() or not output_path.read_bytes().startswith(b"\x89PNG"):
                        raise RealQAAdapterError(_redact(error.decode("utf-8", "replace")) or "Flutter did not produce a PNG screenshot")
                    refs.append(self._step_ref(request, step, "png"))
                    artifact_refs.append(refs[-1])
                elif action == "wait":
                    seconds = float(step.get("entryPoint", "0"))
                    if seconds < 0 or seconds > 30:
                        raise RealQAAdapterError("Flutter wait must be between 0 and 30 seconds")
                    time.sleep(seconds)
                    command_log.append({"stepId": step_id, "action": action, "status": "PASS"})
                else:
                    raise RealQAAdapterError(f"unsupported Flutter QA action: {action}")

                if "device" in required:
                    ref = self._step_ref(request, step, "device.json")
                    self._write(self._artifact_path(request, step, "device.json"), json.dumps(identity, sort_keys=True).encode())
                    refs.append(ref)
                    artifact_refs.append(ref)
                if required & {"command", "log"}:
                    ref = self._step_ref(request, step, "command.json")
                    self._write(
                        self._artifact_path(request, step, "command.json"),
                        json.dumps({"commands": command_log, "stdout": _redact(output.decode("utf-8", "replace"))}, sort_keys=True).encode(),
                    )
                    refs.append(ref)
                    artifact_refs.append(ref)
                unsupported = required - {"device", "command", "log", "screenshot"}
                if unsupported:
                    raise RealQAAdapterError(f"unsupported Flutter evidence kinds: {', '.join(sorted(unsupported))}")
                if not refs:
                    raise RealQAAdapterError("Flutter step did not produce the requested evidence")
            except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
                step_status = "FAILED"
                blockers.append(f"{step_id}: {_redact(str(exc)) or type(exc).__name__}")
            step_results.append({"stepId": step_id, "status": step_status, "evidenceRefs": sorted(set(refs))})

        command_ref = f"flutter/{_safe_part(request['runId'], 'runId')}/run.command.json"
        self._write(run_dir / "run.command.json", json.dumps(command_log, sort_keys=True).encode())
        artifact_refs.append(command_ref)
        status = "PASS" if all(item["status"] == "PASS" for item in step_results) else "FAILED"
        return {
            "status": status,
            "targetIdentity": identity,
            "stepResults": step_results,
            "artifactRefs": sorted(set(artifact_refs)),
            "blockers": sorted(set(blockers)),
        }


class IOSSimulatorTransport:
    """Run bounded iOS Simulator workflows through an explicit xcrun simctl."""

    def __init__(
        self,
        *,
        udid: str,
        artifacts_dir: str | Path,
        xcrun_binary: str = "xcrun",
        app_path: str | Path | None = None,
        bundle_id: str | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.udid = _safe_part(udid, "iOS Simulator UDID")
        self.artifacts_dir = Path(artifacts_dir).expanduser()
        self.xcrun_binary = xcrun_binary
        self.app_path = Path(app_path).expanduser() if app_path else None
        self.bundle_id = bundle_id.strip() if bundle_id else None
        self.command_runner = command_runner

    @property
    def configured(self) -> bool:
        return bool(self.command_runner is not None or shutil.which(self.xcrun_binary) or Path(self.xcrun_binary).is_file())

    def _run(self, args: Sequence[str], timeout: int, *, binary: bool = False) -> tuple[int, bytes, bytes]:
        command = [self.xcrun_binary, "simctl", *[str(item) for item in args]]
        if self.command_runner is not None:
            return self.command_runner(command, timeout, binary)
        completed = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
        stdout = completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout).encode()
        stderr = completed.stderr if isinstance(completed.stderr, bytes) else str(completed.stderr).encode()
        return completed.returncode, stdout, stderr

    def _devices(self, timeout: int) -> list[tuple[str, Mapping[str, Any]]]:
        code, stdout, stderr = self._run(["list", "devices", "--json"], timeout)
        if code != 0:
            raise RealQAAdapterError(_redact(stderr.decode("utf-8", "replace")) or f"simctl exited with code {code}")
        try:
            value = json.loads(stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise RealQAAdapterError("iOS Simulator discovery returned invalid JSON") from exc
        devices = value.get("devices") if isinstance(value, Mapping) else None
        if not isinstance(devices, Mapping):
            raise RealQAAdapterError("iOS Simulator discovery returned no devices")
        return [(str(runtime), item) for runtime, items in devices.items() if isinstance(items, list) for item in items if isinstance(item, Mapping)]

    def _identity(self, timeout: int = 30) -> dict[str, str]:
        for runtime, device in self._devices(timeout):
            if str(device.get("udid", "")) != self.udid:
                continue
            if str(device.get("state", "")) != "Booted":
                raise RealQAAdapterError(f"iOS Simulator {self.udid} is not Booted")
            return {
                "name": self.udid,
                "version": runtime.rsplit(".", 1)[-1],
                "build": str(device.get("deviceTypeIdentifier") or runtime),
                "environment": f"ios-simulator:{self.udid}",
            }
        raise RealQAAdapterError(f"iOS Simulator {self.udid} was not found")

    def probe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["xcrun simctl is unavailable"]}
        try:
            return {"status": "AVAILABLE", "runtimeIdentity": self._identity(int(request.get("timeoutSeconds", 30)))}
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}

    def _artifact_path(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> Path:
        return self.artifacts_dir / _safe_part(request["runId"], "runId") / f"{_safe_part(step['stepId'], 'stepId')}.{suffix}"

    def _write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _step_ref(self, request: Mapping[str, Any], step: Mapping[str, Any], suffix: str) -> str:
        return _logical_ref("ios", str(request["runId"]), str(step["stepId"]), suffix)

    def run_workflow(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if not self.configured:
            return {"status": "UNAVAILABLE", "blockers": ["xcrun simctl is unavailable"]}
        try:
            identity = self._identity(int(request.get("timeoutSeconds", 30)))
        except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
            return {"status": "UNAVAILABLE", "blockers": [_redact(str(exc)) or type(exc).__name__]}

        run_dir = self.artifacts_dir / _safe_part(request["runId"], "runId")
        run_dir.mkdir(parents=True, exist_ok=True)
        command_log: list[dict[str, Any]] = []
        artifact_refs: list[str] = []
        step_results: list[dict[str, Any]] = []
        blockers: list[str] = []
        for step in workflow_steps:
            step_id = str(step["stepId"])
            action = str(step.get("action", "")).strip().lower()
            required = {str(item).lower() for item in step.get("evidenceRequired", [])}
            refs: list[str] = []
            step_status = "PASS"
            try:
                timeout = int(request["timeoutSeconds"])
                output = b""
                error = b""
                if action == "install":
                    if request.get("externalState") not in {"DISPOSABLE_ONLY", "EXPLICIT_APPROVAL"}:
                        raise RealQAAdapterError("iOS install requires disposable or explicitly approved external state")
                    if self.app_path is None or not self.app_path.is_file():
                        raise RealQAAdapterError("iOS install requires an existing explicitly configured app path")
                    code, output, error = self._run(["install", self.udid, str(self.app_path)], timeout)
                elif action in {"launch", "open"}:
                    if not self.bundle_id:
                        raise RealQAAdapterError("iOS launch requires an explicitly configured bundle id")
                    code, output, error = self._run(["launch", self.udid, self.bundle_id], timeout)
                elif action == "screenshot":
                    output_path = self._artifact_path(request, step, "png")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    code, output, error = self._run(["io", self.udid, "screenshot", str(output_path)], timeout)
                    if code == 0 and (not output_path.is_file() or not output_path.read_bytes().startswith(b"\x89PNG")):
                        code = 1
                        error = b"simctl did not produce a PNG screenshot"
                    if code == 0:
                        refs.append(self._step_ref(request, step, "png"))
                        artifact_refs.append(refs[-1])
                elif action == "open_url":
                    url = str(step.get("entryPoint", ""))
                    parsed = urlsplit(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
                        raise RealQAAdapterError("iOS open_url requires a query-free http(s) URL")
                    code, output, error = self._run(["openurl", self.udid, url], timeout)
                elif action == "log":
                    code, output, error = self._run(["spawn", self.udid, "log", "show", "--last", "1m", "--style", "compact"], timeout)
                elif action == "wait":
                    seconds = float(step.get("entryPoint", "0"))
                    if seconds < 0 or seconds > 30:
                        raise RealQAAdapterError("iOS wait must be between 0 and 30 seconds")
                    time.sleep(seconds)
                    code = 0
                    command_log.append({"stepId": step_id, "action": action, "status": "PASS"})
                else:
                    raise RealQAAdapterError(f"unsupported iOS Simulator QA action: {action}")
                command_log.append({"stepId": step_id, "action": action, "exitCode": code})
                if code != 0:
                    raise RealQAAdapterError(_redact(error.decode("utf-8", "replace")) or f"simctl action {action} failed")

                if "device" in required:
                    ref = self._step_ref(request, step, "device.json")
                    self._write(self._artifact_path(request, step, "device.json"), json.dumps(identity, sort_keys=True).encode())
                    refs.append(ref)
                    artifact_refs.append(ref)
                if required & {"command", "log"}:
                    ref = self._step_ref(request, step, "command.json")
                    self._write(self._artifact_path(request, step, "command.json"), json.dumps(command_log, sort_keys=True).encode())
                    refs.append(ref)
                    artifact_refs.append(ref)
                unsupported = required - {"device", "command", "log", "screenshot"}
                if unsupported:
                    raise RealQAAdapterError(f"unsupported iOS evidence kinds: {', '.join(sorted(unsupported))}")
                if not refs:
                    raise RealQAAdapterError("iOS step did not produce the requested evidence")
            except (OSError, RealQAAdapterError, subprocess.TimeoutExpired) as exc:
                step_status = "FAILED"
                blockers.append(f"{step_id}: {_redact(str(exc)) or type(exc).__name__}")
            step_results.append({"stepId": step_id, "status": step_status, "evidenceRefs": sorted(set(refs))})

        command_ref = f"ios/{_safe_part(request['runId'], 'runId')}/run.command.json"
        self._write(run_dir / "run.command.json", json.dumps(command_log, sort_keys=True).encode())
        artifact_refs.append(command_ref)
        status = "PASS" if all(item["status"] == "PASS" for item in step_results) else "FAILED"
        return {
            "status": status,
            "targetIdentity": identity,
            "stepResults": step_results,
            "artifactRefs": sorted(set(artifact_refs)),
            "blockers": sorted(set(blockers)),
        }


def browser_manifest(*, adapter_id: str, host_id: str, target_allowlist: Sequence[str]) -> dict[str, Any]:
    from .qa_adapters import unavailable_manifest

    manifest = unavailable_manifest(adapter_id=adapter_id, kind="browser", host_id=host_id)
    manifest.update({
        "status": "AVAILABLE",
        "version": "playwright-host-v1",
        "capabilities": ["launch", "navigate", "interact", "screenshot", "log", "trace", "network"],
        "targetAllowlist": list(target_allowlist),
        "evidenceKinds": ["screenshot", "log", "trace", "network"],
    })
    return manifest


def android_manifest(*, adapter_id: str, host_id: str, target_allowlist: Sequence[str]) -> dict[str, Any]:
    from .qa_adapters import unavailable_manifest

    manifest = unavailable_manifest(adapter_id=adapter_id, kind="android_emulator", host_id=host_id)
    manifest.update({
        "status": "AVAILABLE",
        "version": "adb-host-v1",
        "capabilities": ["probe", "install", "launch", "tap", "input_text", "screenshot", "device", "log"],
        "targetAllowlist": list(target_allowlist),
        "evidenceKinds": ["screenshot", "device", "command", "log"],
    })
    return manifest


def ios_manifest(*, adapter_id: str, host_id: str, target_allowlist: Sequence[str]) -> dict[str, Any]:
    from .qa_adapters import unavailable_manifest

    manifest = unavailable_manifest(adapter_id=adapter_id, kind="ios_simulator", host_id=host_id)
    manifest.update({
        "status": "AVAILABLE",
        "version": "xcrun-simctl-host-v1",
        "capabilities": ["probe", "install", "launch", "open_url", "screenshot", "device", "log"],
        "targetAllowlist": list(target_allowlist),
        "evidenceKinds": ["screenshot", "device", "command", "log"],
    })
    return manifest


def flutter_manifest(*, adapter_id: str, host_id: str, target_allowlist: Sequence[str]) -> dict[str, Any]:
    from .qa_adapters import unavailable_manifest

    manifest = unavailable_manifest(adapter_id=adapter_id, kind="flutter", host_id=host_id)
    manifest.update({
        "status": "AVAILABLE",
        "version": "flutter-cli-host-v1",
        "capabilities": ["probe", "run_test", "screenshot", "device", "log", "command"],
        "targetAllowlist": list(target_allowlist),
        "evidenceKinds": ["screenshot", "device", "command", "log"],
    })
    return manifest
