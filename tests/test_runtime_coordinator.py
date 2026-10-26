from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_execution import build_host_execution_manifest  # noqa: E402
from engine.model_selection import select_model_assignments  # noqa: E402
from engine.native_agent_bridge import unavailable_native_manifest  # noqa: E402
from engine.provider_bridge import unavailable_provider_manifest  # noqa: E402
from engine.runtime_coordinator import (  # noqa: E402
    RuntimeCoordinator,
    RuntimeCoordinatorError,
    validate_runtime_coordinator_config,
)


ADAPTER_SOURCE = """\
import json
import sys

request = json.load(sys.stdin)
operation = request["hostExecution"]["operation"]
if operation == "native-spawn":
    print(json.dumps({"status": "STARTED", "checkpoints": ["runtime:started"], "blockers": []}))
else:
    print(json.dumps({
        "status": "EXECUTED",
        "model": request["model"],
        "checkpoint": {"sequence": 1, "state": "FINAL"},
        "evidenceRefs": ["runtime:provider"],
    }))
"""


class RuntimeCoordinatorTests(unittest.TestCase):
    def _selection(self):
        fixture_path = PACKAGE_ROOT / "tests/fixtures/model-selection/normal-feature.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        policy = json.loads((PACKAGE_ROOT / "config/model-selection-v1.json").read_text(encoding="utf-8"))
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], policy)
        worker = next(item for item in selection["assignments"] if item["role"] == "worker")
        return fixture, policy, worker

    def _config(self, root: Path, *, include_provider: bool = True, include_execution: bool = True):
        fixture, _, worker = self._selection()
        host_id = worker["hostId"]
        adapter = root / "runtime_adapter.py"
        adapter.write_text(ADAPTER_SOURCE, encoding="utf-8")

        native_manifest = unavailable_native_manifest(bridge_id="native", host_id=host_id)
        native_manifest.update({"status": "AVAILABLE", "version": "1.0.0"})
        native_execution = build_host_execution_manifest(
            adapter_id="native-runtime",
            host_id=host_id,
            operation="native-spawn",
            executable=sys.executable,
            arguments=[str(adapter)],
            working_directory=root,
            allowed_root=root,
        ) if include_execution else None

        providers = []
        if include_provider:
            provider_manifest = unavailable_provider_manifest(provider_id="provider", host_id=host_id)
            provider_manifest.update({"status": "AVAILABLE", "authState": "AUTHENTICATED", "version": "1.0.0"})
            provider_manifest["models"] = [
                {"modelId": model, "reasoningEfforts": efforts, "status": "AVAILABLE"}
                for model, efforts in fixture["hostCatalog"]["availableModels"].items()
            ]
            provider_execution = build_host_execution_manifest(
                adapter_id="provider-runtime",
                host_id=host_id,
                operation="provider-execute",
                executable=sys.executable,
                arguments=[str(adapter)],
                working_directory=root,
                allowed_root=root,
            ) if include_execution else None
            providers.append({"providerManifest": provider_manifest, "executionManifest": provider_execution})

        return {
            "schemaVersion": "runtime-coordinator-config-v1",
            "runtimeId": "runtime-test",
            "hostId": host_id,
            "nativeAgent": {"bridgeManifest": native_manifest, "executionManifest": native_execution},
            "providers": providers,
            "sourceBound": True,
            "externalSideEffects": "HOST_CONTROLLED",
        }

    def test_coordinator_registers_and_runs_native_and_provider_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            self.assertEqual({"valid": True, "errors": []}, validate_runtime_coordinator_config(config))
            coordinator = RuntimeCoordinator.from_config(config)
            status = coordinator.status()
            self.assertEqual("AVAILABLE", status["nativeAgent"]["status"])
            self.assertEqual("AVAILABLE", status["providers"][0]["status"])

            _, policy, worker = self._selection()
            slot = {
                "canonicalName": "Runtime Worker",
                "owner": "AE",
                "controller": "AE",
                "role": "worker",
                "scope": {"scopeId": worker["scope"]["scopeId"], "paths": ["src/feature.py"], "authority": "AE"},
                "resourceLimit": {"maxSeconds": 120, "maxOutputTokens": 1000},
                "stopCondition": "receipt",
                "taskId": worker["taskId"],
                "sourceFingerprint": worker["sourceFingerprint"],
            }
            spawned = coordinator.spawn_agent(worker, slot, expected_policy=policy)
            self.assertEqual("STARTED", spawned["status"])
            self.assertTrue(spawned["receipt"]["live"])
            executed = coordinator.execute_provider(worker, expected_policy=policy)
            self.assertEqual("EXECUTED", executed["status"])
            self.assertTrue(executed["receipt"]["live"])

    def test_missing_provider_is_explicitly_unavailable_and_not_simulated(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = RuntimeCoordinator.from_config(self._config(Path(directory), include_provider=False))
            _, policy, worker = self._selection()
            result = coordinator.execute_provider(worker, expected_policy=policy)
            self.assertEqual("CAPABILITY_UNAVAILABLE", result["status"])
            self.assertFalse(result["receipt"]["live"])

    def test_config_rejects_unknown_fields_and_host_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self._config(Path(directory))
            invalid = dict(config, unexpected=True)
            self.assertFalse(validate_runtime_coordinator_config(invalid)["valid"])
            coordinator = RuntimeCoordinator.from_config(config)
            _, policy, worker = self._selection()
            with self.assertRaises(RuntimeCoordinatorError):
                coordinator.execute_provider(dict(worker, hostId="other-host"), expected_policy=policy)

    def test_host_status_reports_the_registered_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "runtime-config.json"
            config_path.write_text(json.dumps(self._config(root)), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(PACKAGE_ROOT / "scripts/host_integration_status.py"),
                    "--json",
                    "--runtime-coordinator-config",
                    str(config_path),
                ],
                cwd=PACKAGE_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            status = json.loads(completed.stdout)
            self.assertEqual("AVAILABLE", status["runtimeCoordinator"]["status"])
            self.assertEqual("AVAILABLE", status["nativeAgentBridge"]["status"])
            self.assertEqual("AVAILABLE", status["modelProvider"]["status"])


if __name__ == "__main__":
    unittest.main()
