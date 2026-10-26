import os
import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from run_independent_closure import build_closure, contract_checks  # noqa: E402


class IndependentClosureTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("general_CLOSURE_CHILD"):
            self.skipTest("closure subprocess must not recursively run closure tests")

    def test_fallback_keeps_candidate_coverage_open(self):
        root = Path(__file__).resolve().parents[1]
        report = build_closure(root, None, "test-reviewer")
        self.assertFalse(report["closureEligible"])
        self.assertEqual(report["phases"]["2"]["status"], "MISSING_COVERAGE")
        self.assertEqual(report["sideEffects"], [])

    def test_contract_corpus_contains_negative_cases(self):
        root = Path(__file__).resolve().parents[1]
        checks = contract_checks(root)
        names = {item["name"] for item in checks}
        self.assertIn("valid-packet.json", names)
        self.assertIn("invalid-malformed.json", names)
        self.assertTrue(all(item["passed"] for item in checks), checks)

    def test_candidate_aware_closure_is_eligible_when_configured(self):
        candidate_root = os.environ.get("general_CANDIDATE_ROOT")
        if not candidate_root:
            self.skipTest("general_CANDIDATE_ROOT is not configured")
        root = Path(__file__).resolve().parents[1]
        report = build_closure(root, Path(candidate_root), "test-reviewer")
        self.assertTrue(report["closureEligible"], report)
        self.assertEqual(report["phases"]["2"]["status"], "PASS")
        self.assertEqual(report["runtimeStatus"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
