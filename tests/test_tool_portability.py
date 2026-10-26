from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.task_packet import profile_task, validate_task_packet  # noqa: E402
from engine.tool_adapter import (  # noqa: E402
    CONTROLLER_ID,
    compare_task_across_adapters,
    evaluate_task_against_adapter,
    validate_tool_adapter,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
ADAPTER_ROOT = FIXTURE_ROOT / "tool-adapters"
TOOL_IDS = {"codex", "claude", "cursor", "windsurf", "gemini", "qodo", "antigravity"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ToolPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_json(FIXTURE_ROOT / "portable-task-packet.json")
        self.adapters = [load_json(path) for path in sorted(ADAPTER_ROOT.glob("*.json"))]

    def test_portable_packet_validates_and_uses_fast_route(self):
        self.assertEqual(validate_task_packet(self.packet), {"valid": True, "errors": []})
        profile = profile_task(self.packet)
        self.assertEqual(profile["status"], "READY")
        self.assertEqual(profile["route"], "FAST_DAILY")
        self.assertEqual(profile["budget"]["maxWorkers"], 0)
        self.assertFalse(profile["executed"])
        self.assertEqual(profile["sideEffects"], [])

    def test_destructive_or_external_work_requires_approval(self):
        packet = json.loads(json.dumps(self.packet))
        packet["riskSignals"] = ["production"]
        packet["allowedActions"]["external"] = True
        packet["scope"]["externalSideEffects"] = True
        profile = profile_task(packet)
        self.assertEqual(profile["route"], "FULL_SAFETY")
        self.assertEqual(profile["status"], "BLOCKED")
        self.assertEqual(profile["approvalGate"]["status"], "BLOCKED")

    def test_packet_scope_cannot_hide_an_allowed_external_action(self):
        packet = json.loads(json.dumps(self.packet))
        packet["allowedActions"]["external"] = True
        packet["scope"]["externalSideEffects"] = False
        validation = validate_task_packet(packet)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("externalSideEffects" in error for error in validation["errors"]))

    def test_approved_external_work_can_pass_the_approval_gate(self):
        packet = json.loads(json.dumps(self.packet))
        packet["riskSignals"] = ["deployment"]
        packet["allowedActions"]["external"] = True
        packet["scope"]["externalSideEffects"] = True
        packet["approval"] = {"status": "APPROVED", "approvalId": "approval-1", "reason": "approved for the test fixture"}
        profile = profile_task(packet)
        self.assertEqual(profile["status"], "READY")
        self.assertEqual(profile["approvalGate"]["status"], "APPROVED")

    def test_all_named_tool_fixtures_share_the_same_adapter_contract(self):
        self.assertEqual({adapter["tool"]["toolId"] for adapter in self.adapters}, TOOL_IDS)
        for adapter in self.adapters:
            validation = validate_tool_adapter(adapter)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(adapter["authority"]["controller"], CONTROLLER_ID)
            self.assertFalse(any(adapter["authority"][key] for key in adapter["authority"] if key.startswith("can")))

    def test_controller_id_matches_schema_and_rejects_other_authority(self):
        schema = load_json(PACKAGE_ROOT / "schemas/tool-adapter-manifest-v1.schema.json")
        self.assertEqual(schema["$defs"]["authority"]["properties"]["controller"]["const"], CONTROLLER_ID)
        adapter = json.loads(json.dumps(self.adapters[0]))
        adapter["authority"]["controller"] = "previous-controller"
        result = validate_tool_adapter(adapter)
        self.assertFalse(result["valid"])
        self.assertTrue(any(CONTROLLER_ID in error for error in result["errors"]))

    def test_each_tool_can_evaluate_the_same_source_task(self):
        results = [evaluate_task_against_adapter(self.packet, adapter) for adapter in self.adapters]
        self.assertEqual({result["status"] for result in results}, {"READY"})
        self.assertEqual({result["route"] for result in results}, {"FAST_DAILY"})
        self.assertTrue(all(not result["executed"] and result["sideEffects"] == [] for result in results))

    def test_cross_tool_report_is_source_only(self):
        report = compare_task_across_adapters(self.packet, self.adapters)
        self.assertEqual(report["overallStatus"], "COMPATIBLE")
        self.assertTrue(report["routeAgreement"])
        self.assertEqual(len(report["results"]), len(TOOL_IDS))
        self.assertTrue(report["sourceOnly"])
        self.assertFalse(report["executed"])
        self.assertEqual(report["sideEffects"], [])

    def test_missing_browser_capability_is_not_promoted_to_runtime_pass(self):
        packet = json.loads(json.dumps(self.packet))
        packet["requiredEvidence"] = ["source", "browser"]
        for adapter in self.adapters:
            with self.subTest(tool=adapter["tool"]["toolId"]):
                result = evaluate_task_against_adapter(packet, adapter)
                self.assertEqual(result["status"], "MISSING_COVERAGE")
                self.assertEqual(result["limitedCapabilities"], ["browser"])

    def test_inspector_emits_a_valid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            command = [
                sys.executable,
                str(PACKAGE_ROOT / "scripts" / "inspect_tool_capabilities.py"),
                "--tool-id",
                "test-tool",
                "--display-name",
                "Test Tool",
                "--repository",
                str(PACKAGE_ROOT),
                "--output",
                str(output),
            ]
            completed = subprocess.run(command, cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            manifest = load_json(output)
            self.assertEqual(validate_tool_adapter(manifest), {"valid": True, "eligible": True, "errors": []})


if __name__ == "__main__":
    unittest.main()
