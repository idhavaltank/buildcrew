from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PACKAGE_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(PACKAGE_ROOT))

from workflow_contracts import (  # noqa: E402
    validate_flutter_trace_contract,
    validate_session_handoff_contract,
)


SCHEMAS = {
    "ae-task-review-binding-v1.schema.json": "ae-task-review-binding-v1.schema.json",
    "ae-authorization-evidence-envelope-v1.schema.json": "ae-authorization-evidence-envelope-v1.schema.json",
    "ae-named-reviewer-assignment-v1.schema.json": "ae-named-reviewer-assignment-v1.schema.json",
    "ae-named-reviewer-receipt-v1.schema.json": "ae-named-reviewer-receipt-v1.schema.json",
    "review-shard-manifest-v1.schema.json": "review-shard-manifest-v1.schema.json",
    "review-finding-ledger-v1.schema.json": "review-finding-ledger-v1.schema.json",
    "review-risk-authorization-v1.schema.json": "review-risk-authorization-v1.schema.json",
    "review-finding-resolution-v1.schema.json": "review-finding-resolution-v1.schema.json",
    "review-coverage-reuse-decision-v1.schema.json": "review-coverage-reuse-decision-v1.schema.json",
    "review-invalidation-plan-v1.schema.json": "review-invalidation-plan-v1.schema.json",
    "review-delta-packet-v1.schema.json": "review-delta-packet-v1.schema.json",
    "session-handoff-v1.schema.json": "session-handoff-v1.schema.json",
    "handoff-privacy-receipt-v1.schema.json": "handoff-privacy-receipt-v1.schema.json",
    "flutter-execution-trace-v1.schema.json": "flutter-execution-trace-v1.schema.json",
    "repository-reuse-assessment-v1.schema.json": "repository-reuse-assessment-v1.schema.json",
    "memory-context-lookup-v1.schema.json": "memory-context-lookup-v1.schema.json",
    "module-feature-memory-writeback-v1.schema.json": "module-feature-memory-writeback-v1.schema.json",
    "progress-checklist-v2.schema.json": "progress-checklist-v2.schema.json",
    "continuity-session-v1.schema.json": "continuity-session-v1.schema.json",
    "continuity-report-v1.schema.json": "continuity-report-v1.schema.json",
    "context-budget-report-v1.schema.json": "context-budget-report-v1.schema.json",
    "host-execution-manifest-v1.schema.json": "host-execution-manifest-v1.schema.json",
    "runtime-coordinator-config-v1.schema.json": "runtime-coordinator-config-v1.schema.json",
}


class FeatureIntegrationTests(unittest.TestCase):
    def test_every_portable_feature_schema_is_strict_json_schema_2020_12(self):
        for filename, schema_id in SCHEMAS.items():
            with self.subTest(schema=filename):
                value = json.loads((PACKAGE_ROOT / "schemas" / filename).read_text(encoding="utf-8"))
                self.assertEqual("https://json-schema.org/draft/2020-12/schema", value["$schema"])
                self.assertEqual(schema_id, value["$id"])
                self.assertFalse(value["additionalProperties"])
                self.assertTrue(value["required"])

    def test_host_status_separates_source_contracts_from_unavailable_live_host_and_flutter_runtime(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_ROOT / "host_integration_status.py"), "--json"],
            cwd=PACKAGE_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        status = json.loads(completed.stdout)
        self.assertEqual("SOURCE_READY", status["namedReviewerContracts"]["status"])
        self.assertEqual("CAPABILITY_UNAVAILABLE", status["namedReviewerHost"]["status"])
        self.assertEqual("SOURCE_READY", status["flutterStaticTrace"]["status"])
        self.assertIn(status["flutterRuntime"]["status"], {"UNAVAILABLE", "AVAILABLE"})
        self.assertIn(status["iosAdapter"]["status"], {"UNAVAILABLE", "AVAILABLE"})
        self.assertEqual([], status["sideEffects"])

    def test_central_handoff_contract_never_transfers_authority(self):
        result = validate_session_handoff_contract({"schemaVersion": "session-handoff-v1"})
        self.assertFalse(result["valid"])
        self.assertFalse(result["authorityTransferred"])

    def test_central_flutter_contract_never_satisfies_runtime_or_device_evidence(self):
        result = validate_flutter_trace_contract(
            {},
            {"platforms": ["flutter"], "decision": "FULL_SAFETY", "matchedRiskSignals": ["offline"]},
            {"riskSignals": ["offline"], "changedPaths": [], "symbols": []},
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["runtimeEvidenceSatisfied"])
        self.assertFalse(result["deviceEvidenceSatisfied"])


if __name__ == "__main__":
    unittest.main()
