import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from audit_hybrid_release import (
    DEFAULT_CORPUS,
    DEFAULT_PACKET,
    DEFAULT_RECEIPT,
    build_audit_report,
    sanitize,
    validate_matrix,
)


class HybridAuditTests(unittest.TestCase):
    def audit(self):
        return build_audit_report(
            corpus=DEFAULT_CORPUS,
            packet_path=DEFAULT_PACKET,
            receipt_path=DEFAULT_RECEIPT,
        )

    def test_passing_fallback_audit_is_technical_pass_with_no_side_effects(self):
        report = self.audit()

        self.assertTrue(report["technicalPass"])
        self.assertEqual(report["terminalStatus"], "TECHNICAL_PASS_LIMITED")
        self.assertEqual(report["sideEffects"], [])
        self.assertTrue(report["fixtureChecks"]["packet"]["valid"])
        self.assertTrue(report["fixtureChecks"]["receipt"]["completionEligible"])
        self.assertTrue(report["matrix"]["valid"])
        self.assertEqual(report["statuses"]["activation"]["state"], "UNAVAILABLE")

    def test_missing_candidate_is_explicitly_unverified_but_fallback_remains_valid(self):
        report = self.audit()
        candidate = next(
            item for item in report["matrix"]["requirements"]
            if item["scenario"] == "candidate-unavailable"
        )

        self.assertTrue(report["technicalPass"])
        self.assertEqual(report["modes"]["candidateAware"]["state"], "NOT_REQUESTED")
        self.assertEqual(candidate["observedExit"], "HYBRID_UNVERIFIED")
        self.assertTrue(candidate["passed"])

    def test_invalid_candidate_aware_source_fails_closed(self):
        report = build_audit_report(
            corpus=DEFAULT_CORPUS,
            packet_path=DEFAULT_PACKET,
            receipt_path=DEFAULT_RECEIPT,
            candidate_root=Path(__file__).resolve().parent / "not-a-candidate",
        )

        self.assertFalse(report["technicalPass"])
        self.assertEqual(report["statuses"]["static"]["state"], "FAIL")
        self.assertEqual(report["sideEffects"], [])

    def test_reviewer_timeout_is_terminal_but_not_completion(self):
        report = self.audit()
        timeout = next(
            item for item in report["matrix"]["requirements"]
            if item["scenario"] == "reviewer-timeout"
        )

        self.assertEqual(timeout["expectedExit"], "BLOCKED")
        self.assertEqual(timeout["observedExit"], "BLOCKED")
        self.assertTrue(timeout["passed"])

    def test_stale_memory_scenario_keeps_current_source_authoritative(self):
        report = self.audit()
        stale = next(
            item for item in report["matrix"]["requirements"]
            if item["scenario"] == "stale-rag-conflict"
        )

        self.assertEqual(stale["observedExit"], "SOURCE_WINS_NEEDS_REVIEW")
        self.assertIn("current source wins", stale["evidence"][0].lower())
        self.assertEqual(report["statuses"]["learning"]["state"], "PASS")

    def test_incomplete_matrix_fails_closed(self):
        report = self.audit()
        incomplete = report["matrix"]["requirements"][:-1]
        matrix = validate_matrix(incomplete)

        self.assertFalse(matrix["valid"])
        self.assertTrue(any("missing requirement ownership" in error for error in matrix["errors"]))

    def test_sanitizer_redacts_credential_like_values_before_report_output(self):
        report = sanitize({"evidence": "Fix token=not-for-a-report"})

        self.assertEqual(report["evidence"], "Fix token=[REDACTED]")


if __name__ == "__main__":
    unittest.main()
