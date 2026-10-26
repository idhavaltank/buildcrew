from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_execution import (  # noqa: E402
    HostExecutionError,
    SubprocessNativeAgentTransport,
    SubprocessProviderTransport,
    build_host_execution_manifest,
    probe_host_execution_manifest,
    validate_host_execution_manifest,
)
from engine.model_selection import select_model_assignments  # noqa: E402
from engine.native_agent_bridge import NativeAgentBridge, unavailable_native_manifest  # noqa: E402
from engine.provider_bridge import ModelProviderBridge, unavailable_provider_manifest  # noqa: E402


ADAPTER = """\
import json
import sys
import time

request = json.load(sys.stdin)
mode = request.get("mode", "native")
operation = request.get("hostExecution", {}).get("operation")
if mode == "timeout":
    time.sleep(2)
elif mode == "large":
    print("x" * 10000)
    raise SystemExit(0)
elif mode == "provider" or operation == "provider-execute":
    print(json.dumps({
        "status": "EXECUTED",
        "model": request["model"],
        "checkpoint": {"sequence": 1, "state": "FINAL"},
        "evidenceRefs": ["adapter:provider"],
    }))
else:
    print(json.dumps({"status": "STARTED", "checkpoints": ["adapter:started"], "blockers": []}))
"""


class HostExecutionTests(unittest.TestCase):
    def _manifest(self, root: Path, *, operation: str = "native-spawn", **overrides):
        adapter = root / "adapter.py"
        adapter.write_text(ADAPTER, encoding="utf-8")
        manifest = build_host_execution_manifest(
            adapter_id="test-adapter",
            host_id="test-host",
            operation=operation,
            executable=sys.executable,
            arguments=[str(adapter)],
            working_directory=root,
            allowed_root=root,
            timeout_seconds=overrides.pop("timeout_seconds", 5),
            max_output_bytes=overrides.pop("max_output_bytes", 64 * 1024),
            **overrides,
        )
        manifest.update(overrides)
        return manifest

    def test_manifest_is_exact_and_pins_an_executable(self):
        result = validate_host_execution_manifest({"schemaVersion": "host-execution-manifest-v1"})
        self.assertFalse(result["valid"])
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory))
            self.assertEqual({"valid": True, "errors": []}, validate_host_execution_manifest(manifest))
            self.assertEqual("AVAILABLE", probe_host_execution_manifest(manifest)["status"])
            invalid = dict(manifest, shell=True)
            self.assertFalse(validate_host_execution_manifest(invalid)["valid"])
            tampered = dict(manifest, executableSha256="0" * 64)
            self.assertEqual("UNAVAILABLE", probe_host_execution_manifest(tampered)["status"])

    def test_native_transport_executes_only_when_manifest_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            response = SubprocessNativeAgentTransport(manifest).spawn({"taskId": "task-1"})
            self.assertEqual("STARTED", response["status"])
            self.assertTrue(response["live"])
            self.assertEqual(["adapter:started"], response["checkpoints"])

            unavailable = dict(manifest, status="UNAVAILABLE")
            blocked = SubprocessNativeAgentTransport(unavailable).spawn({"taskId": "task-2"})
            self.assertEqual("CAPABILITY_UNAVAILABLE", blocked["status"])
            self.assertFalse(blocked["live"])

    def test_provider_transport_returns_bounded_provider_result(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory), operation="provider-execute")
            response = SubprocessProviderTransport(manifest).execute({"mode": "provider", "model": "model-balanced"})
            self.assertEqual("EXECUTED", response["status"])
            self.assertTrue(response["live"])
            self.assertEqual(["adapter:provider"], response["evidenceRefs"])

    def test_timeout_and_output_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeout_manifest = self._manifest(root, timeout_seconds=1)
            timed_out = SubprocessNativeAgentTransport(timeout_manifest).spawn({"mode": "timeout"})
            self.assertEqual("TIMED_OUT", timed_out["status"])
            self.assertTrue(timed_out["live"])

            output_manifest = self._manifest(root, max_output_bytes=1024)
            oversized = SubprocessNativeAgentTransport(output_manifest).spawn({"mode": "large"})
            self.assertEqual("FAILED", oversized["status"])
            self.assertTrue(any("output exceeded" in item for item in oversized["blockers"]))

    def test_wrong_operation_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._manifest(Path(directory), operation="native-spawn")
            with self.assertRaises(HostExecutionError):
                SubprocessProviderTransport(manifest)

    def test_existing_bridges_can_use_the_gateway_and_preserve_non_live_state(self):
        fixture_path = PACKAGE_ROOT / "tests/fixtures/model-selection/normal-feature.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        policy = json.loads((PACKAGE_ROOT / "config/model-selection-v1.json").read_text(encoding="utf-8"))
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], policy)
        worker = next(item for item in selection["assignments"] if item["role"] == "worker")
        slot = {
            "canonicalName": "Worker Adapter",
            "owner": "AE",
            "controller": "AE",
            "role": "worker",
            "scope": {"scopeId": worker["scope"]["scopeId"], "paths": ["src/feature.py"], "authority": "AE"},
            "resourceLimit": {"maxSeconds": 120, "maxOutputTokens": 1000},
            "stopCondition": "receipt",
            "taskId": worker["taskId"],
            "sourceFingerprint": worker["sourceFingerprint"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_host = self._manifest(root)
            native_manifest = unavailable_native_manifest(bridge_id="native", host_id=worker["hostId"])
            native_manifest.update({"status": "AVAILABLE", "version": "1.0.0"})
            native_result = NativeAgentBridge(native_manifest, SubprocessNativeAgentTransport(native_host)).spawn(
                worker, slot, expected_policy=policy
            )
            self.assertEqual("STARTED", native_result["status"])
            self.assertTrue(native_result["receipt"]["live"])

            provider_host = self._manifest(root, operation="provider-execute")
            provider_manifest = unavailable_provider_manifest(provider_id="provider", host_id=worker["hostId"])
            provider_manifest.update({"status": "AVAILABLE", "authState": "AUTHENTICATED", "version": "1.0.0"})
            provider_manifest["models"] = [
                {"modelId": model, "reasoningEfforts": efforts, "status": "AVAILABLE"}
                for model, efforts in fixture["hostCatalog"]["availableModels"].items()
            ]
            provider_result = ModelProviderBridge(
                provider_manifest, SubprocessProviderTransport(provider_host)
            ).execute(worker, expected_policy=policy)
            self.assertEqual("EXECUTED", provider_result["status"])
            self.assertTrue(provider_result["receipt"]["live"])

            unavailable_host = dict(provider_host, status="UNAVAILABLE")
            unavailable_result = ModelProviderBridge(
                provider_manifest, SubprocessProviderTransport(unavailable_host)
            ).execute(worker, expected_policy=policy)
            self.assertEqual("CAPABILITY_UNAVAILABLE", unavailable_result["status"])
            self.assertFalse(unavailable_result["receipt"]["live"])


if __name__ == "__main__":
    unittest.main()
