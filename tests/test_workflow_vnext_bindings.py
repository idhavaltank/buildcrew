import copy
import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

from engine.model_selection import select_model_assignments  # noqa: E402
from workflow_contracts import validate_model_assignment_binding  # noqa: E402


class WorkflowVNextBindingTests(unittest.TestCase):
    def setUp(self):
        fixture_root = PACKAGE_ROOT / "tests" / "fixtures" / "model-selection"
        fixture = json.loads((fixture_root / "normal-feature.json").read_text())
        policy = json.loads((PACKAGE_ROOT / "config" / "model-selection-v1.json").read_text())
        self.assignment = select_model_assignments(
            fixture["taskFacts"], fixture["hostCatalog"], policy
        )["assignments"][1]
        self.assignment["issuedAt"] = "2026-08-24T00:00:00Z"
        self.assignment["expiresAt"] = "2099-01-01T00:00:00Z"

    def test_matching_model_assignment_and_receipt_are_current(self):
        receipt = {"modelAssignment": copy.deepcopy(self.assignment)}
        result = validate_model_assignment_binding(
            receipt,
            {"modelAssignment": copy.deepcopy(self.assignment)},
        )
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("CURRENT", result["status"])

    def test_changed_project_policy_or_fingerprint_invalidates_receipt(self):
        for field in ("projectId", "policyFingerprint", "sourceFingerprint"):
            with self.subTest(field=field):
                receipt_assignment = copy.deepcopy(self.assignment)
                receipt_assignment[field] = "changed-value"
                result = validate_model_assignment_binding(
                    {"modelAssignment": receipt_assignment},
                    {"modelAssignment": copy.deepcopy(self.assignment)},
                )
                self.assertFalse(result["valid"])
                self.assertEqual("INVALIDATED", result["status"])


if __name__ == "__main__":
    unittest.main()

