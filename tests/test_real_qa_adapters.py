from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.qa_adapters import BrowserQAAdapter, FlutterQAAdapter, IOSSimulatorQAAdapter, validate_adapter_manifest  # noqa: E402
from engine.qa_runner import AdapterRegistry, TARGET_TO_KIND, run_qa_workflow  # noqa: E402
from engine.real_qa_adapters import (  # noqa: E402
    ADBAndroidTransport,
    FlutterRuntimeTransport,
    IOSSimulatorTransport,
    PlaywrightBrowserTransport,
    browser_manifest,
    flutter_manifest,
    ios_manifest,
)


def _fake_adb(command, _timeout, _binary):
    args = list(command[3:])
    if args == ["get-state"]:
        return 0, b"device\n", b""
    if args[-2:] == ["getprop", "ro.build.version.release"]:
        return 0, b"14\n", b""
    if args[-2:] == ["getprop", "ro.build.display.id"]:
        return 0, b"sdk_gphone64_x86_64/UP1A.231005.007/1234567:userdebug/test-keys\n", b""
    if args[:2] == ["exec-out", "screencap"]:
        return 0, b"\x89PNG\r\n\x1a\nminimal", b""
    if args[:3] == ["shell", "uiautomator", "dump"]:
        return 0, b"<hierarchy text=\"Ready\" />", b""
    return 0, b"OK\n", b""


def _fake_flutter(command, _timeout, _binary):
    args = list(command[1:])
    if args == ["devices", "--machine"]:
        return 0, json.dumps([{"id": "flutter-1", "name": "Flutter device", "targetPlatform": "android-arm64", "version": "14"}]).encode(), b""
    if args and args[0] == "screenshot":
        output_path = Path(args[args.index("--out") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nminimal")
        return 0, b"screenshot complete", b""
    if args and args[0] == "test":
        return 0, b"flutter test passed", b""
    return 1, b"", b"unsupported flutter command"


def _fake_xcrun(command, _timeout, _binary):
    args = list(command[1:])
    if args == ["simctl", "list", "devices", "--json"]:
        devices = {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{
                    "name": "iPhone 16",
                    "udid": "ios-1",
                    "state": "Booted",
                    "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-16",
                }]
            }
        }
        return 0, json.dumps(devices).encode(), b""
    if args[:3] == ["simctl", "io", "ios-1"] and args[3] == "screenshot":
        Path(args[4]).write_bytes(b"\x89PNG\r\n\x1a\nminimal")
        return 0, b"screenshot complete", b""
    return 0, b"OK", b""


class RealQAAdapterTests(unittest.TestCase):
    def test_ios_and_flutter_manifests_are_supported_by_the_shared_contract(self):
        self.assertEqual({"WEB": "browser", "MOBILE": "android_emulator", "IOS": "ios_simulator", "FLUTTER": "flutter"}, TARGET_TO_KIND)
        for manifest in (
            ios_manifest(adapter_id="ios", host_id="test-host", target_allowlist=["ios-1"]),
            flutter_manifest(adapter_id="flutter", host_id="test-host", target_allowlist=["flutter-1"]),
        ):
            result = validate_adapter_manifest(manifest)
            self.assertTrue(result["valid"], result["errors"])

    def test_flutter_transport_probes_device_and_runs_project_relative_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            test_file = root / "integration_test" / "smoke_test.dart"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("void main() {}", encoding="utf-8")
            artifacts = Path(directory) / "artifacts"
            transport = FlutterRuntimeTransport(
                project_root=root,
                artifacts_dir=artifacts,
                device_id="flutter-1",
                command_runner=_fake_flutter,
            )
            probe = transport.probe({"timeoutSeconds": 10})
            self.assertEqual("AVAILABLE", probe["status"])
            result = transport.run_workflow(
                {"runId": "flutter-run", "timeoutSeconds": 10},
                [
                    {"stepId": "test", "action": "run_test", "entryPoint": "integration_test/smoke_test.dart", "evidenceRequired": ["command"]},
                    {"stepId": "screen", "action": "screenshot", "evidenceRequired": ["screenshot"]},
                ],
            )
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertTrue((artifacts / "flutter-run" / "screen.png").is_file())

    def test_ios_simulator_transport_probes_booted_device_and_captures_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory) / "artifacts"
            transport = IOSSimulatorTransport(
                udid="ios-1",
                artifacts_dir=artifacts,
                command_runner=_fake_xcrun,
            )
            probe = transport.probe({"timeoutSeconds": 10})
            self.assertEqual("AVAILABLE", probe["status"])
            result = transport.run_workflow(
                {"runId": "ios-run", "timeoutSeconds": 10},
                [{"stepId": "screen", "action": "screenshot", "evidenceRequired": ["screenshot", "device"]}],
            )
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertTrue((artifacts / "ios-run" / "screen.png").is_file())

    def test_runner_dispatches_ios_and_flutter_to_matching_transports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            test_file = root / "integration_test" / "smoke_test.dart"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("void main() {}", encoding="utf-8")
            artifacts = Path(directory) / "artifacts"
            registry = AdapterRegistry()
            registry.register(IOSSimulatorQAAdapter(ios_manifest(adapter_id="ios", host_id="test-host", target_allowlist=["ios-1"]), IOSSimulatorTransport(udid="ios-1", artifacts_dir=artifacts, command_runner=_fake_xcrun)))
            registry.register(FlutterQAAdapter(flutter_manifest(adapter_id="flutter", host_id="test-host", target_allowlist=["flutter-1"]), FlutterRuntimeTransport(project_root=root, artifacts_dir=artifacts, device_id="flutter-1", command_runner=_fake_flutter)))
            workflow = {
                "schemaVersion": "qa-workflow-map-v1", "workflowId": "mobile-workflow", "requestId": "mobile-request",
                "taskId": "mobile-task", "sourceFingerprint": "a" * 64, "objective": "Run mobile smoke",
                "steps": [
                    {"stepId": "ios-screen", "title": "iOS screenshot", "target": "IOS", "entryPoint": "screen", "action": "screenshot", "expected": "screenshot captured", "evidenceRequired": ["screenshot"]},
                    {"stepId": "flutter-screen", "title": "Flutter screenshot", "target": "FLUTTER", "entryPoint": "screen", "action": "screenshot", "expected": "screenshot captured", "evidenceRequired": ["screenshot"]},
                ],
                "unresolved": [], "status": "READY", "generatedAt": "2026-08-25T10:00:00Z",
            }
            packet = {
                "schemaVersion": "qa-execution-packet-v1", "packetId": "mobile-packet", "requestId": "mobile-request",
                "workflowId": "mobile-workflow", "taskId": "mobile-task", "sourceFingerprint": "a" * 64,
                "targetAllowlist": {"repository": "repo", "webTargets": [], "androidTargets": [], "iosTargets": ["ios-1"], "flutterTargets": ["flutter-1"], "testDataScope": ["synthetic"]},
                "requiredCapabilities": ["device"], "requiredEvidence": ["screenshot"], "stepIds": ["ios-screen", "flutter-screen"], "timeoutSeconds": 30,
                "isolation": {"externalState": "DISPOSABLE_ONLY", "network": "DISABLED", "credentials": "NONE"},
                "mode": "REQUIRED", "status": "READY", "issuedAt": "2026-08-25T10:00:00Z", "expiresAt": "2026-08-25T11:00:00Z",
            }
            result = run_qa_workflow(workflow, packet, registry)
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertEqual({"ios", "flutter"}, {item["adapterId"] for item in result["adapterReceipts"]})

    def test_adb_missing_runtime_is_explicit(self):
        transport = ADBAndroidTransport(serial="emulator-5554", artifacts_dir=tempfile.mkdtemp(), adb_binary="/missing/adb")
        result = transport.probe({"targetIdentity": {"name": "emulator-5554"}})
        self.assertEqual("UNAVAILABLE", result["status"])

    def test_adb_workflow_uses_device_identity_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = ADBAndroidTransport(
                serial="emulator-5554",
                artifacts_dir=directory,
                adb_binary="adb",
                command_runner=_fake_adb,
            )
            request = {
                "runId": "android-run",
                "targetIdentity": {"name": "emulator-5554"},
                "timeoutSeconds": 10,
                "externalState": "DISPOSABLE_ONLY",
            }
            result = transport.run_workflow(request, [
                {"stepId": "screen", "action": "screenshot", "evidenceRequired": ["screenshot", "device"]},
                {"stepId": "ready", "action": "assert_text", "expected": "Ready", "evidenceRequired": ["command"]},
            ])
            self.assertEqual("PASS", result["status"])
            self.assertEqual("14", result["targetIdentity"]["version"])
            self.assertTrue((Path(directory) / "android-run" / "screen.png").is_file())

    def test_adb_mutating_install_requires_disposable_or_approved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = ADBAndroidTransport(serial="emulator-5554", artifacts_dir=directory, adb_binary="adb", command_runner=_fake_adb)
            result = transport.run_workflow(
                {"runId": "install-run", "targetIdentity": {"name": "emulator-5554"}, "timeoutSeconds": 10, "externalState": "NONE"},
                [{"stepId": "install", "action": "install", "evidenceRequired": ["command"]}],
            )
            self.assertEqual("FAILED", result["status"])
            self.assertIn("requires disposable or explicitly approved", result["blockers"][0])

    @unittest.skipUnless(os.environ.get("AUTONOMOUS_ENGINEERING_PLAYWRIGHT_NODE_MODULES") and os.environ.get("AUTONOMOUS_ENGINEERING_BROWSER_EXECUTABLE"), "real Playwright host not configured")
    def test_real_playwright_browser_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PlaywrightBrowserTransport(
                node_modules_root=os.environ["AUTONOMOUS_ENGINEERING_PLAYWRIGHT_NODE_MODULES"],
                artifacts_dir=directory,
                allowed_origins=["data:"],
                executable_path=os.environ["AUTONOMOUS_ENGINEERING_BROWSER_EXECUTABLE"],
            )
            html = "<title>AE</title><button id='go'>Go</button><p id='status' hidden>Ready</p><script>document.querySelector('#go').onclick=()=>document.querySelector('#status').hidden=false</script>"
            url = "data:text/html," + quote(html, safe="")
            request = {
                "runId": "browser-run",
                "targetIdentity": {"name": "browser-target", "version": "unknown", "build": "unknown", "environment": "probe"},
                "timeoutSeconds": 30,
            }
            probe = transport.probe(request)
            self.assertEqual("AVAILABLE", probe["status"])
            request["targetIdentity"] = probe["runtimeIdentity"]
            result = transport.run_workflow(request, [
                {"stepId": "open", "action": "open", "entryPoint": url, "evidenceRequired": ["screenshot"]},
                {"stepId": "click", "action": "click", "entryPoint": "#go", "evidenceRequired": ["screenshot"]},
                {"stepId": "assert", "action": "assert_text", "entryPoint": "#status", "expected": "Ready", "evidenceRequired": ["log", "network", "trace"]},
            ])
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertTrue((Path(directory) / "browser-run" / "open.png").is_file())
            self.assertTrue((Path(directory) / "browser-run" / "run.trace.zip").is_file())

            workflow = {
                "schemaVersion": "qa-workflow-map-v1", "workflowId": "browser-workflow", "requestId": "browser-request",
                "taskId": "browser-task", "sourceFingerprint": "a" * 64, "objective": "Run browser smoke",
                "steps": [{"stepId": "open", "title": "Open", "target": "WEB", "entryPoint": url, "action": "open", "expected": "page loaded", "evidenceRequired": ["screenshot"]}],
                "unresolved": [], "status": "READY", "generatedAt": "2026-08-25T10:00:00Z",
            }
            packet = {
                "schemaVersion": "qa-execution-packet-v1", "packetId": "browser-packet", "requestId": "browser-request",
                "workflowId": "browser-workflow", "taskId": "browser-task", "sourceFingerprint": "a" * 64,
                "targetAllowlist": {"repository": "repo", "webTargets": ["browser-target"], "androidTargets": [], "testDataScope": ["synthetic"]},
                "requiredCapabilities": ["browser"], "requiredEvidence": ["screenshot"], "stepIds": ["open"], "timeoutSeconds": 30,
                "isolation": {"externalState": "DISPOSABLE_ONLY", "network": "ALLOWLISTED", "credentials": "NONE"},
                "mode": "REQUIRED", "status": "READY", "issuedAt": "2026-08-25T10:00:00Z", "expiresAt": "2026-08-25T11:00:00Z",
            }
            registry = AdapterRegistry()
            registry.register(BrowserQAAdapter(browser_manifest(adapter_id="playwright-browser", host_id="test-host", target_allowlist=["browser-target"]), transport))
            runner_result = run_qa_workflow(workflow, packet, registry)
            self.assertEqual("PASS", runner_result["status"], json.dumps(runner_result, indent=2))
            self.assertTrue(runner_result["live"])


if __name__ == "__main__":
    unittest.main()
