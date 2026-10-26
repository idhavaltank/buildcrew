import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.compatibility import compare_static, compare_static_set, validate_static_compatibility, validate_static_compatibility_set
from engine.evidence_observability import (
    build_event,
    build_evidence_matrix,
    validate_evidence_event,
    validate_evidence_matrix,
    validate_event_sequence,
    validate_receipt_freshness,
)
from engine.promotion_simulation import simulate_promotion, validate_promotion_simulation
from engine.qa_contracts import validate_qa_contract
from engine.shadow_binding import bind_context_shadow
from engine.project_context import discover_project_context
from engine.policy_resolver import resolve_policy


class LowRiskLayerTests(unittest.TestCase):
    def test_all_qa_contracts_accept_valid_shapes_and_reject_unknown_fields(self):
        values = {
            "workflow-map": {"schemaVersion":"qa-workflow-map-v1","workflowId":"w","requestId":"q","taskId":"t","sourceFingerprint":"source-1234","objective":"check","steps":[{"stepId":"s1","title":"check","target":"WEB","entryPoint":"/","action":"open","expected":"visible","evidenceRequired":["source"]}],"unresolved":[],"status":"READY","generatedAt":"2026-01-01T00:00:00Z"},
            "execution-packet": {"schemaVersion":"qa-execution-packet-v1","packetId":"p","requestId":"q","workflowId":"w","taskId":"t","sourceFingerprint":"source-1234","targetAllowlist":{"repository":"repo","webTargets":["/"],"androidTargets":[],"testDataScope":["synthetic"]},"requiredCapabilities":[],"requiredEvidence":["source"],"stepIds":["s1"],"timeoutSeconds":10,"isolation":{"externalState":"NONE","network":"DISABLED","credentials":"NONE"},"mode":"ADVISORY","status":"READY","issuedAt":"2026-01-01T00:00:00Z","expiresAt":"2026-01-01T01:00:00Z"},
            "finding": {"schemaVersion":"qa-finding-v1","findingId":"f","packetId":"p","taskId":"t","sourceFingerprint":"source-1234","severity":"LOW","status":"CONFIRMED","title":"x","observed":"x","expected":"y","affectedSteps":["s1"],"evidenceRefs":["e1"],"blocking":False,"createdAt":"2026-01-01T00:00:00Z","residualRisk":[]},
            "receipt": {"schemaVersion":"qa-receipt-v1","receiptId":"r","packetId":"p","requestId":"q","taskId":"t","sourceFingerprint":"source-1234","adapter":"source_only","runtimeIdentity":{"target":"source","version":"1","build":"local"},"startedAt":"2026-01-01T00:00:00Z","finishedAt":"2026-01-01T00:01:00Z","status":"PASS","steps":[{"stepId":"s1","status":"PASS","evidenceRefs":["e1"]}],"evidence":[{"evidenceId":"e1","kind":"source","summary":"source","contentHash":"hash1234","ref":"file"}],"findings":[],"skippedOrBlocked":[],"sideEffects":[],"terminal":True,"residualRisk":[]},
        }
        for kind, value in values.items():
            self.assertTrue(validate_qa_contract(kind, value)["valid"])
            invalid = copy.deepcopy(value); invalid["unexpected"] = True
            self.assertFalse(validate_qa_contract(kind, invalid)["valid"])

    def test_context_binding_is_shadow_only_and_detects_fingerprint_conflict(self):
        with __import__('tempfile').TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            result = discover_project_context(root)
            context, receipt = result["contextMap"], result["receipt"]
            policy = resolve_policy({"requestType":"NEW_TASK","targetPaths":["."],"acceptanceCriteria":[]}, context)
            bound = bind_context_shadow({"taskId":"t"}, context, receipt, policy)
            self.assertEqual(bound["contextShadow"]["enforced"], False)
            self.assertEqual(bound["contextShadow"]["status"], "SHADOW_PASS")
            altered = copy.deepcopy(receipt); altered["fingerprints"] = {}
            self.assertEqual(bind_context_shadow({"taskId":"t"}, context, altered, policy)["contextShadow"]["status"], "SHADOW_CONFLICT")

    def test_static_comparison_never_executes(self):
        result = compare_static({"route":"NEW_TASK"}, {"route":"REVIEW_ONLY"})
        self.assertEqual(result["status"], "DIFFERENT")
        self.assertFalse(result["executed"])
        self.assertTrue(validate_static_compatibility(result)["valid"])
        self.assertEqual(result["sideEffects"], [])

        compatible = compare_static(
            {
                "route": "NEW_TASK", "complexity": "normal", "riskTier": "normal", "requiredCapabilities": ["tests"],
                "requiredEvidence": ["source"], "acceptanceCoverage": ["search"], "policyBehavior": "bounded", "sourceFingerprint": "a" * 64,
            },
            {
                "route": "new_task", "complexity": "NORMAL", "risk": "NORMAL", "requiredCapabilities": ["tests"],
                "requiredCoverage": ["source"], "coverage": ["search"], "policy": "BOUNDED", "sourceFingerprint": "a" * 64,
            },
        )
        self.assertEqual(compatible["status"], "COMPATIBLE")
        self.assertNotIn("/home/", json.dumps(compare_static({"route": "/home/operator/project", "token": "hidden"}, {})))

        projection_set = {
            name: {
                "route": "NEW_TASK", "complexity": "normal", "riskTier": "normal",
                "requiredCapabilities": ["tests"], "requiredEvidence": ["source"],
                "acceptanceCoverage": ["search"], "policyBehavior": "bounded", "sourceFingerprint": "a" * 64,
            }
            for name in ("web", "mobile", "flutter", "candidate", "policy")
        }
        set_report = compare_static_set(projection_set, anchor="web")
        self.assertEqual(set_report["status"], "COMPATIBLE")
        self.assertEqual(set_report["projections"], list(projection_set))
        self.assertTrue(validate_static_compatibility_set(set_report)["valid"])

    def test_promotion_simulation_rolls_back_without_live_change(self):
        result = simulate_promotion({"sourceFingerprint":"candidate"}, activation_requested=True)
        self.assertEqual(result["terminalStatus"], "SIMULATED_AND_ROLLED_BACK")
        self.assertFalse(result["liveChanged"])
        self.assertEqual(result["sideEffects"], [])
        self.assertEqual([stage["stage"] for stage in result["stages"]], ["candidate-validation", "installation", "canary", "activation", "rollback", "recovery"])
        self.assertTrue(validate_promotion_simulation(result)["valid"])
        failed = simulate_promotion({}, canary_pass=False, activation_requested=True)
        self.assertEqual(failed["terminalStatus"], "SIMULATION_BLOCKED")
        self.assertEqual(failed["stages"][-1]["status"], "PASS")

    def test_evidence_status_is_sanitized_and_source_only(self):
        event = build_event("CHECK", "t", "SOURCE_ONLY", source_fingerprint="s", details={"token":"secret"})
        self.assertEqual(event["details"]["token"], "[REDACTED]")
        matrix = build_evidence_matrix(["static"])
        self.assertEqual(matrix["status"], "SOURCE_ONLY")
        self.assertFalse(matrix["liveExecution"])
        self.assertTrue(validate_evidence_event(event)["valid"])
        self.assertTrue(validate_evidence_matrix(matrix)["valid"])

        first = build_event("CHECK", "t", "STARTED", source_fingerprint="s", sequence=0, occurred_at="2026-08-25T10:00:00Z")
        second = build_event("CHECK", "t", "PASS", source_fingerprint="s", sequence=1, occurred_at="2026-08-25T10:01:00Z")
        self.assertTrue(validate_event_sequence([first, second])["valid"])
        self.assertFalse(validate_event_sequence([second, first])["valid"])
        self.assertEqual(validate_receipt_freshness({"finishedAt": "2026-08-25T09:00:00Z"}, now="2026-08-25T10:00:00Z", max_age_seconds=7200)["status"], "CURRENT")
        self.assertEqual(validate_receipt_freshness({"finishedAt": "2026-08-23T09:00:00Z"}, now="2026-08-25T10:00:00Z", max_age_seconds=7200)["status"], "STALE")

    def test_new_layer_clis_accept_valid_input_and_fail_malformed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.json"
            right = root / "right.json"
            candidate = root / "candidate.json"
            left.write_text(json.dumps({
                "route": "NEW_TASK", "complexity": "normal", "riskTier": "normal",
                "requiredCapabilities": ["tests"], "requiredEvidence": ["source"],
                "acceptanceCoverage": ["search"], "sourceFingerprint": "a" * 64,
            }), encoding="utf-8")
            right.write_text(left.read_text(encoding="utf-8"), encoding="utf-8")
            candidate.write_text(json.dumps({"candidateId": "candidate-1", "sourceFingerprint": "b" * 64}), encoding="utf-8")

            comparison = subprocess.run(
                [sys.executable, "-B", "scripts/compare_static_compatibility.py", str(left), str(right)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(comparison.returncode, 0, comparison.stderr)
            self.assertTrue(json.loads(comparison.stdout)["validation"]["valid"])

            multi_comparison = subprocess.run(
                [
                    sys.executable, "-B", "scripts/compare_static_compatibility.py",
                    "--projection", f"web={left}", "--projection", f"mobile={right}",
                    "--projection", f"flutter={left}", "--projection", f"candidate={right}",
                    "--projection", f"policy={left}", "--anchor", "web",
                ],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(multi_comparison.returncode, 0, multi_comparison.stderr)
            self.assertTrue(json.loads(multi_comparison.stdout)["validation"]["valid"])

            promotion = subprocess.run(
                [sys.executable, "-B", "scripts/simulate_promotion.py", str(candidate), "--activation-requested"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(promotion.returncode, 0, promotion.stderr)
            self.assertTrue(json.loads(promotion.stdout)["validation"]["valid"])

            evidence = subprocess.run(
                [sys.executable, "-B", "scripts/build_evidence_status.py", "task-1", "c" * 64, "--requirement", "source"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            evidence_output = json.loads(evidence.stdout)
            self.assertTrue(evidence_output["validation"]["event"]["valid"])
            self.assertTrue(evidence_output["validation"]["matrix"]["valid"])

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            for script, arguments in (
                ("scripts/compare_static_compatibility.py", [str(malformed), str(right)]),
                ("scripts/simulate_promotion.py", [str(malformed)]),
            ):
                with self.subTest(script=script):
                    failed = subprocess.run(
                        [sys.executable, "-B", script, *arguments],
                        cwd=ROOT, capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(failed.returncode, 2)

    def test_on_disk_low_risk_fixtures_cover_valid_invalid_and_five_projections(self):
        fixture_root = ROOT / "tests" / "fixtures" / "low-risk"
        valid_request = json.loads((fixture_root / "qa-request-valid.json").read_text(encoding="utf-8"))
        invalid_request = json.loads((fixture_root / "qa-request-unknown-field.json").read_text(encoding="utf-8"))
        self.assertTrue(validate_qa_contract("request", valid_request)["valid"])
        self.assertFalse(validate_qa_contract("request", invalid_request)["valid"])
        projections = {
            name: json.loads((fixture_root / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("web", "mobile", "flutter", "candidate", "policy")
        }
        self.assertTrue(validate_static_compatibility_set(compare_static_set(projections))["valid"])
        promotion_candidate = json.loads((fixture_root / "promotion-candidate.json").read_text(encoding="utf-8"))
        self.assertTrue(validate_promotion_simulation(simulate_promotion(promotion_candidate, activation_requested=True))["valid"])


if __name__ == "__main__":
    unittest.main()
