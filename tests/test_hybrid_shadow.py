import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from classify_hybrid_shadow import (
    build_report,
    load_cases,
    project_general,
    reconcile_projection,
)


class HybridShadowTests(unittest.TestCase):
    def test_question_is_lightweight_direct(self):
        result = project_general({
            "rawText": "What does this utility do?",
            "requestType": "question",
        })
        self.assertEqual(result["route"], "DIRECT")
        self.assertEqual(result["complexity"], "LIGHTWEIGHT")
        self.assertEqual(result["riskTier"], "LOW")

    def test_authorization_escalates_risk(self):
        result = project_general({
            "rawText": "Review tenant authorization for this endpoint",
            "requestType": "review",
        })
        self.assertEqual(result["route"], "REVIEW_ONLY")
        self.assertEqual(result["complexity"], "COUPLED")
        self.assertEqual(result["riskTier"], "HIGH")

    def test_candidate_unavailable_has_no_side_effects(self):
        report = build_report([{
            "name": "candidate-missing",
            "request": {"rawText": "Explain this", "requestType": "question"},
        }])
        result = report["results"][0]
        self.assertEqual(result["comparison"]["status"], "CANDIDATE_UNAVAILABLE")
        self.assertEqual(report["sideEffects"], [])
        self.assertEqual(result["general"]["authority"],
                         "buildcrew")

    def test_offline_queue_mutation_is_high_risk(self):
        result = project_general({
            "rawText": "Make this mutation work offline with queue replay",
            "requestType": "change",
        })
        self.assertEqual(result["complexity"], "COUPLED")
        self.assertEqual(result["riskTier"], "HIGH")

    def test_candidate_cannot_lower_general_risk_or_replace_route_authority(self):
        general = project_general({
            "rawText": "Review tenant authorization for this endpoint",
            "requestType": "review",
        })
        candidate = {
            "status": "AVAILABLE",
            "route": "DIRECT",
            "complexity": "LIGHTWEIGHT",
            "riskTier": "LOW",
            "riskReasons": ["candidate-underestimate"],
            "requiredCapabilities": ["security-review"],
            "requiredCoverage": ["authorization-flow"],
        }
        result = reconcile_projection(general, candidate)
        self.assertEqual(result["final"]["route"], "REVIEW_ONLY")
        self.assertEqual(result["final"]["riskTier"], "HIGH")
        self.assertEqual(result["status"], "CONFLICT")
        self.assertTrue(result["conflicts"])

    def test_candidate_capabilities_and_coverage_are_unioned(self):
        general = project_general({
            "rawText": "Implement this change",
            "requestType": "change",
            "requiredCapabilities": ["source-analysis"],
            "requiredCoverage": ["acceptance-flow"],
        })
        result = reconcile_projection(general, {
            "status": "AVAILABLE",
            "route": "NEW_TASK",
            "complexity": "NORMAL",
            "riskTier": "LOW",
            "riskReasons": [],
            "requiredCapabilities": ["regression-analysis"],
            "requiredCoverage": ["runtime-proof"],
        })
        self.assertEqual(
            result["final"]["requiredCapabilities"],
            ["regression-analysis", "source-analysis"],
        )
        self.assertEqual(
            result["final"]["requiredCoverage"],
            ["acceptance-flow", "runtime-proof"],
        )

    def test_candidate_unavailable_reconciliation_preserves_source_decision(self):
        general = project_general({
            "rawText": "Explain this utility",
            "requestType": "question",
        })
        result = reconcile_projection(
            general,
            {
                "status": "CANDIDATE_UNAVAILABLE",
                "route": None,
                "complexity": "LIGHTWEIGHT",
                "riskTier": "LOW",
                "riskReasons": [],
                "requiredCapabilities": [],
                "requiredCoverage": [],
            },
        )
        self.assertEqual(result["status"], "CANDIDATE_UNAVAILABLE")
        self.assertEqual(result["final"]["route"], "DIRECT")
        self.assertEqual(result["conflicts"], [])

    def test_shadow_expectation_is_checked_against_reconciled_decision(self):
        report = build_report([{
            "name": "expected-question",
            "request": {"rawText": "Explain this", "requestType": "question"},
            "expected": {
                "route": "DIRECT",
                "complexity": "LIGHTWEIGHT",
                "riskTier": "LOW",
            },
        }])
        self.assertTrue(report["expectationsPassed"])
        self.assertTrue(report["results"][0]["expectation"]["passed"])

    def test_bundled_scenario_corpus_has_ten_expected_cases(self):
        corpus = Path(__file__).resolve().parent / "fixtures" / "shadow"
        report = build_report(load_cases(corpus))
        self.assertEqual(report["caseCount"], 10)
        self.assertTrue(report["expectationsPassed"])
        self.assertTrue(all(
            item["comparison"]["status"] == "CANDIDATE_UNAVAILABLE"
            for item in report["results"]
        ))

    def test_shadow_report_redacts_credential_assignments(self):
        report = build_report([{
            "name": "redaction",
            "request": {
                "rawText": "Review token=super-secret-value",
                "requestType": "review",
            },
        }])
        rendered = str(report)
        self.assertNotIn("super-secret-value", rendered)
        self.assertIn("[REDACTED]", rendered)


if __name__ == "__main__":
    unittest.main()
