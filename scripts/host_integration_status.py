#!/usr/bin/env python3
"""Report host integration readiness without launching or mutating anything."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_execution import probe_host_execution_manifest  # noqa: E402
from engine.runtime_coordinator import RuntimeCoordinator, RuntimeCoordinatorError  # noqa: E402


def _executable_detected(value: str | Path) -> bool:
    candidate = Path(value).expanduser()
    resolved = Path(shutil.which(str(value)) or candidate)
    return resolved.is_file() and os.access(resolved, os.X_OK)


def _playwright_detected(module_root: str | None) -> bool:
    if shutil.which("playwright"):
        return True
    if not module_root:
        return False
    root = Path(module_root).expanduser()
    package = root if root.name == "playwright" else root / "playwright"
    return bool(shutil.which("node") and package.is_dir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--playwright-node-modules", type=Path, default=None, help="Optional approved Node modules root for Playwright detection")
    parser.add_argument("--browser-executable", type=Path, default=None, help="Optional approved browser executable for detection")
    parser.add_argument("--browser-origin", action="append", default=[], help="Approved browser origin; repeat for multiple origins")
    parser.add_argument("--browser-artifacts-dir", type=Path, help="Existing browser evidence directory")
    parser.add_argument("--adb-path", default="adb", help="ADB executable to detect")
    parser.add_argument("--android-serial", help="Optional explicit Android serial for status context")
    parser.add_argument("--android-artifacts-dir", type=Path, help="Existing Android evidence directory")
    parser.add_argument("--flutter-path", default="flutter", help="Flutter executable to detect")
    parser.add_argument("--flutter-project-root", type=Path, help="Flutter project root for runtime readiness")
    parser.add_argument("--flutter-device", help="Explicit Flutter device identifier")
    parser.add_argument("--flutter-artifacts-dir", type=Path, help="Existing Flutter evidence directory")
    parser.add_argument("--xcrun-path", default="xcrun", help="xcrun executable to detect")
    parser.add_argument("--ios-udid", help="Explicit iOS Simulator UDID for status context")
    parser.add_argument("--ios-artifacts-dir", type=Path, help="Existing iOS evidence directory")
    parser.add_argument(
        "--host-execution-manifest",
        type=Path,
        help="Optional approved host-execution manifest to probe",
    )
    parser.add_argument(
        "--runtime-coordinator-config",
        type=Path,
        help="Optional runtime coordinator config to probe",
    )
    args = parser.parse_args(argv)
    playwright_root = str(args.playwright_node_modules or os.environ.get("AUTONOMOUS_ENGINEERING_PLAYWRIGHT_NODE_MODULES") or "") or None
    browser_executable = args.browser_executable or (Path(os.environ["AUTONOMOUS_ENGINEERING_BROWSER_EXECUTABLE"]) if os.environ.get("AUTONOMOUS_ENGINEERING_BROWSER_EXECUTABLE") else None)
    browser_runtime = _playwright_detected(playwright_root)
    browser_binary = (
        _executable_detected(browser_executable)
        if browser_executable
        else any(_executable_detected(item) for item in ("google-chrome", "chromium", "firefox"))
    )
    browser_detected = browser_runtime and browser_binary
    browser_execution_ready = bool(
        browser_detected
        and args.browser_origin
        and args.browser_artifacts_dir
        and args.browser_artifacts_dir.is_dir()
    )
    adb_runtime = _executable_detected(args.adb_path)
    android_execution_ready = bool(
        adb_runtime
        and args.android_serial
        and args.android_artifacts_dir
        and args.android_artifacts_dir.is_dir()
    )
    flutter_runtime = _executable_detected(args.flutter_path)
    flutter_execution_ready = bool(
        flutter_runtime
        and args.flutter_project_root
        and args.flutter_project_root.is_dir()
        and args.flutter_device
        and args.flutter_artifacts_dir
        and args.flutter_artifacts_dir.is_dir()
    )
    xcrun_runtime = _executable_detected(args.xcrun_path)
    ios_execution_ready = bool(
        xcrun_runtime
        and args.ios_udid
        and args.ios_artifacts_dir
        and args.ios_artifacts_dir.is_dir()
    )
    if args.host_execution_manifest:
        try:
            host_manifest = json.loads(args.host_execution_manifest.read_text(encoding="utf-8"))
            host_execution = probe_host_execution_manifest(host_manifest)
        except (OSError, json.JSONDecodeError) as exc:
            host_execution = {
                "status": "MISCONFIGURED",
                "proof": f"host execution manifest could not be read: {type(exc).__name__}",
                "blockers": [type(exc).__name__],
            }
    else:
        host_execution = {
            "status": "CONFIGURATION_REQUIRED",
            "proof": "no explicit host-execution manifest was supplied",
            "blockers": ["host execution is opt-in"],
        }
    if args.runtime_coordinator_config:
        try:
            runtime_config = json.loads(args.runtime_coordinator_config.read_text(encoding="utf-8"))
            runtime_coordinator = RuntimeCoordinator.from_config(runtime_config).status()
        except (OSError, json.JSONDecodeError, RuntimeCoordinatorError) as exc:
            runtime_coordinator = {
                "status": "MISCONFIGURED",
                "proof": f"runtime coordinator config could not be loaded: {type(exc).__name__}",
                "blockers": [type(exc).__name__],
            }
    else:
        runtime_coordinator = {
            "status": "CONFIGURATION_REQUIRED",
            "proof": "no runtime coordinator config was supplied",
            "blockers": ["runtime coordination is opt-in"],
        }
    if runtime_coordinator.get("status") == "MISCONFIGURED":
        runtime_native = {"status": "MISCONFIGURED", "proof": runtime_coordinator["proof"]}
        runtime_provider = {"status": "MISCONFIGURED", "proof": runtime_coordinator["proof"]}
    elif isinstance(runtime_coordinator.get("nativeAgent"), dict):
        runtime_native = {
            "status": runtime_coordinator["nativeAgent"]["status"],
            "proof": "runtime coordinator native-agent registry",
        }
        provider_values = runtime_coordinator.get("providers", [])
        provider_available = any(
            item.get("status") == "AVAILABLE"
            for item in provider_values
            if isinstance(item, dict)
        )
        runtime_provider = {
            "status": "AVAILABLE" if provider_available else "CAPABILITY_UNAVAILABLE",
            "proof": "runtime coordinator provider registry",
        }
    else:
        runtime_native = {"status": "CONFIGURATION_REQUIRED", "proof": "no runtime coordinator config was supplied"}
        runtime_provider = {"status": "CONFIGURATION_REQUIRED", "proof": "no runtime coordinator config was supplied"}
    browser_proof = (
        "Playwright and browser executable detected; explicit origin and artifact configuration is still required"
        if browser_detected and not browser_execution_ready
        else "browser runtime prerequisites detected and execution configuration is complete"
        if browser_execution_ready
        else "Playwright Node module or browser executable is unavailable"
    )
    android_proof = (
        "ADB detected; explicit serial and artifact configuration is still required"
        if adb_runtime and not android_execution_ready
        else "ADB and execution configuration are present; device state still requires a probe"
        if android_execution_ready
        else "adb executable is unavailable"
    )
    flutter_proof = (
        "Flutter SDK detected; explicit project, device, and artifact configuration is still required"
        if flutter_runtime and not flutter_execution_ready
        else "Flutter SDK and execution configuration are present; device state still requires a probe"
        if flutter_execution_ready
        else "Flutter SDK executable is unavailable"
    )
    ios_proof = (
        "xcrun detected; explicit Simulator UDID and artifact configuration is still required"
        if xcrun_runtime and not ios_execution_ready
        else "xcrun and execution configuration are present; Simulator state still requires a probe"
        if ios_execution_ready
        else "xcrun executable is unavailable"
    )
    status = {
        "browserAdapter": {
            "status": "AVAILABLE" if browser_detected else "UNAVAILABLE",
            "runtimeDetected": browser_detected,
            "executionReady": browser_execution_ready,
            "proof": browser_proof,
        },
        "androidAdapter": {
            "status": "AVAILABLE" if adb_runtime else "UNAVAILABLE",
            "runtimeDetected": adb_runtime,
            "executionReady": android_execution_ready,
            "target": args.android_serial,
            "proof": android_proof,
        },
        "iosAdapter": {
            "status": "AVAILABLE" if xcrun_runtime else "UNAVAILABLE",
            "runtimeDetected": xcrun_runtime,
            "executionReady": ios_execution_ready,
            "target": args.ios_udid,
            "proof": ios_proof,
        },
        "qaRuntimeRunner": {"status": "SOURCE_READY", "proof": "runner emits blocked receipts when adapters are unavailable"},
        "modelProvider": runtime_provider,
        "nativeAgentBridge": runtime_native,
        "hostExecution": host_execution,
        "runtimeCoordinator": runtime_coordinator,
        "namedReviewerContracts": {"status": "SOURCE_READY", "proof": "strict supplemental assignment, authority-envelope, shard, and receipt validators are available"},
        "namedReviewerHost": {"status": "CAPABILITY_UNAVAILABLE", "proof": "no authenticated named-role catalog, durable claim store, or observed model/effort host was supplied"},
        "reviewInvalidation": {"status": "SOURCE_READY", "proof": "pure shard reuse and affected-only invalidation planner is available; it does not dispatch reviews"},
        "sessionHandoffContracts": {"status": "SOURCE_READY", "proof": "external-root, redacted, expiry-bound handoff source contracts are available"},
        "flutterStaticTrace": {"status": "SOURCE_READY", "proof": "ten-stage static evidence validator is available and cannot satisfy runtime/device evidence"},
        "flutterRuntime": {
            "status": "AVAILABLE" if flutter_runtime else "UNAVAILABLE",
            "runtimeDetected": flutter_runtime,
            "executionReady": flutter_execution_ready,
            "target": args.flutter_device,
            "proof": flutter_proof,
        },
        "candidateActivation": {"status": "UNAVAILABLE", "proof": "no active host, canary switch, or owner approval was supplied"},
        "projectContextGate": {"status": "SOURCE_READY", "proof": "enforced preflight contract is available to the controller"},
        "ragProvider": {"status": "CONFIGURATION_REQUIRED", "proof": "no approved retrieval/writeback provider was supplied"},
        "harness": {"status": "SOURCE_READY", "proof": "manifest importer/projection is bounded; execution remains host-owned"},
        "observability": {"status": "SOURCE_READY", "proof": "durable sink is available only with an explicit external state root"},
        "installation": {"status": "CONFIGURATION_REQUIRED", "proof": "no approved active root, host installer, or owner approval was supplied"},
        "sideEffects": [],
    }
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        for key, value in status.items():
            if key != "sideEffects":
                print(f"{key}: {value['status']} — {value['proof']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
