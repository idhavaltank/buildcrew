from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.flutter_execution_trace import (  # noqa: E402
    STAGE_ORDER,
    applicability_policy_fingerprint,
    derive_required_stages,
    derive_trace_requirement,
    derive_trace_status,
    validate_flutter_execution_trace,
)
from engine.mobile_workflow import classify_mobile_workflow  # noqa: E402


def build_trace(decision, risks):
    required = derive_required_stages(decision, {"riskSignals": risks, "changedPaths": ["lib/sync.dart"], "symbols": ["SyncQueue"]})
    stages = []
    for stage_id in STAGE_ORDER:
        affected = stage_id in required["requiredStages"]
        stages.append(
            {
                "stageId": stage_id,
                "applicability": "AFFECTED" if affected else "NOT_APPLICABLE",
                "status": "PASS" if affected else "NOT_APPLICABLE",
                "paths": ["lib/sync.dart"] if affected else [],
                "symbols": ["SyncQueue"] if affected else [],
                "evidenceRefs": [f"source:{stage_id}"] if affected else [],
                "residualRisk": [],
            }
        )
    return {
        "schemaVersion": "flutter-execution-trace-v1",
        "traceId": "trace-1",
        "taskId": "task-1",
        "repositoryId": "repo-1",
        "sourceFingerprint": "sha256:" + "a" * 64,
        "overallDiffFingerprint": "sha256:" + "b" * 64,
        "route": decision["decision"],
        "policyVersion": required["policyVersion"],
        "policyFingerprint": applicability_policy_fingerprint(),
        "riskSignals": risks,
        "required": required["required"],
        "stages": stages,
        "overallStatus": derive_trace_status(stages, required=required["required"]),
        "runtimeEvidenceStatus": "NOT_VERIFIED",
        "generatedAt": "2026-09-16T08:00:00Z",
    }


class FlutterExecutionTraceTests(unittest.TestCase):
    def test_full_safety_offline_sync_maps_to_static_stages_and_passes(self):
        decision = classify_mobile_workflow({
            "platform": "Flutter",
            "objective": "Repair offline sync queue and lifecycle replay",
        })
        risks = decision["matchedRiskSignals"]
        trace = build_trace(decision, risks)
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {"riskSignals": risks, "changedPaths": ["lib/sync.dart"], "symbols": ["SyncQueue"]},
        )
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("PASS", result["derivedStatus"])
        self.assertEqual("NOT_VERIFIED", trace["runtimeEvidenceStatus"])

    def test_stage_set_must_be_exact_ordered_and_unique(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        trace = build_trace(decision, decision["matchedRiskSignals"])
        trace["stages"][0], trace["stages"][1] = trace["stages"][1], trace["stages"][0]
        self.assertFalse(
            validate_flutter_execution_trace(
                trace,
                decision,
                {"riskSignals": decision["matchedRiskSignals"], "changedPaths": [], "symbols": []},
            )["valid"]
        )

    def test_required_route_cannot_pass_with_every_stage_not_applicable(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        trace = build_trace(decision, decision["matchedRiskSignals"])
        for stage in trace["stages"]:
            stage.update({"applicability": "NOT_APPLICABLE", "status": "NOT_APPLICABLE", "paths": [], "symbols": [], "evidenceRefs": []})
        trace["overallStatus"] = "PASS"
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {"riskSignals": decision["matchedRiskSignals"], "changedPaths": [], "symbols": []},
        )
        self.assertFalse(result["valid"])
        self.assertEqual("MISSING_COVERAGE", result["derivedStatus"])

    def test_status_precedence_is_fail_then_blocked_then_missing(self):
        stages = [
            {
                "stageId": stage_id,
                "applicability": "AFFECTED",
                "status": "PASS",
                "paths": ["lib/a.dart"],
                "symbols": [],
                "evidenceRefs": ["source:evidence"],
                "residualRisk": [],
            }
            for stage_id in STAGE_ORDER
        ]
        stages[0]["status"] = "MISSING_COVERAGE"
        self.assertEqual("MISSING_COVERAGE", derive_trace_status(stages, required=True))
        stages[1]["status"] = "BLOCKED"
        self.assertEqual("BLOCKED", derive_trace_status(stages, required=True))
        stages[2]["status"] = "FAIL"
        self.assertEqual("FAIL", derive_trace_status(stages, required=True))

    def test_fast_daily_presentation_change_does_not_require_full_trace(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Adjust settings widget spacing"})
        self.assertFalse(derive_trace_requirement(decision)["required"])

    def test_unknown_trusted_risk_signal_cannot_be_silently_ignored(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        trace = build_trace(decision, decision["matchedRiskSignals"])
        trace["riskSignals"] = sorted(trace["riskSignals"] + ["unmapped-boundary"])
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {"riskSignals": trace["riskSignals"], "changedPaths": [], "symbols": []},
        )
        self.assertFalse(result["valid"])
        self.assertIn("unmapped", " ".join(result["errors"]))

    def test_affected_stage_requires_evidence_even_when_caller_claims_pass(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        trace = build_trace(decision, decision["matchedRiskSignals"])
        affected = next(stage for stage in trace["stages"] if stage["applicability"] == "AFFECTED")
        affected["evidenceRefs"] = []
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {"riskSignals": decision["matchedRiskSignals"], "changedPaths": ["lib/sync.dart"], "symbols": ["SyncQueue"]},
        )
        self.assertFalse(result["valid"])
        self.assertEqual("MISSING_COVERAGE", result["derivedStatus"])

    def test_affected_stage_must_bind_to_trusted_changed_paths_or_symbols(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        trace = build_trace(decision, decision["matchedRiskSignals"])
        for stage in trace["stages"]:
            if stage["applicability"] == "AFFECTED":
                stage["paths"] = ["lib/unrelated.dart"]
                stage["symbols"] = ["Unrelated"]
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {
                "riskSignals": decision["matchedRiskSignals"],
                "changedPaths": ["lib/sync.dart"],
                "symbols": ["SyncQueue"],
            },
        )
        self.assertFalse(result["valid"])
        self.assertEqual("MISSING_COVERAGE", result["derivedStatus"])

    def test_full_safety_without_any_trusted_stage_mapping_is_missing_coverage(self):
        decision = classify_mobile_workflow({"platform": "Flutter", "objective": "Repair offline queue"})
        decision = {**decision, "matchedRiskSignals": []}
        trace = build_trace(decision, [])
        regression = next(stage for stage in trace["stages"] if stage["stageId"] == "regression")
        regression.update(
            {
                "applicability": "AFFECTED",
                "status": "PASS",
                "paths": ["lib/sync.dart"],
                "symbols": ["SyncQueue"],
                "evidenceRefs": ["source:regression"],
            }
        )
        trace["overallStatus"] = "PASS"
        result = validate_flutter_execution_trace(
            trace,
            decision,
            {"riskSignals": [], "changedPaths": ["lib/sync.dart"], "symbols": ["SyncQueue"]},
        )
        self.assertFalse(result["valid"])
        self.assertEqual("MISSING_COVERAGE", result["derivedStatus"])


if __name__ == "__main__":
    unittest.main()
