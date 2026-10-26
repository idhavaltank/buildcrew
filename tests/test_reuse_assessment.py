from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_gate import enforce_project_context_gate, validate_enforced_gate  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402
from engine.project_context import discover_project_context  # noqa: E402
from engine.reuse_assessment import validate_reuse_assessment  # noqa: E402


class ReuseAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")
        discovered = discover_project_context(
            self.root,
            targets=["."],
            task_type="NEW_TASK",
            acceptance_criteria=["Add a reusable empty state"],
        )
        self.context = discovered["contextMap"]
        self.receipt = discovered["receipt"]
        self.policy = resolve_policy(
            {
                "requestType": "NEW_TASK",
                "targetPaths": ["."],
                "acceptanceCriteria": ["Add a reusable empty state"],
            },
            self.context,
        )
        self.intake = {
            "taskId": "task-reuse-1",
            "repositoryId": self.context["repository"]["repositoryId"],
            "sourceFingerprint": self.context["fingerprints"]["sourceFingerprint"],
            "contextFingerprint": self.context["fingerprints"]["contextFingerprint"],
            "taskScopeFingerprint": self.context["fingerprints"]["taskScopeFingerprint"],
            "requestType": "NEW_TASK",
            "normalizedObjective": "Add a reusable empty state",
            "targetPaths": ["."],
            "acceptanceCriteria": ["Add a reusable empty state"],
            "mutationRequested": True,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def assessment(self, *, decision: str = "REUSE") -> dict:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        value = {
            "schemaVersion": "repository-reuse-assessment-v1",
            "taskId": self.intake["taskId"],
            "repositoryId": self.intake["repositoryId"],
            "sourceFingerprint": self.intake["sourceFingerprint"],
            "taskScopeFingerprint": self.intake["taskScopeFingerprint"],
            "targetPaths": ["."],
            "searchTerms": ["empty state", "shared component"],
            "candidates": [{
                "path": "src/components/empty-state.tsx",
                "symbol": "EmptyState",
                "publicBoundary": "@app/components",
                "evidence": ["representative profile-screen usage"],
                "fit": "compatible",
            }],
            "representativeEvidence": ["src/screens/profile.tsx imports EmptyState"],
            "decision": decision,
            "chosenBoundary": "@app/components#EmptyState",
            "rationale": "The existing component satisfies the requested behavior.",
            "negativeSearchEvidence": [],
            "exceptionReason": None,
            "terminalStatus": "COMPLETED",
            "issuedAt": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "expiresAt": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "sideEffects": [],
        }
        if decision == "NEW":
            value["candidates"][0]["fit"] = "incompatible"
            value["representativeEvidence"] = []
            value["negativeSearchEvidence"] = ["No compatible component exists in the approved package."]
            value["chosenBoundary"] = "src/components/new-empty-state.tsx"
        if decision == "DOCUMENTED_EXCEPTION":
            value["candidates"][0]["fit"] = "incompatible"
            value["exceptionReason"] = "The platform requires a native control with unavailable semantics."
        return value

    def assert_bound_valid(self, assessment: dict) -> None:
        result = validate_reuse_assessment(
            assessment,
            expected_task_id=self.intake["taskId"],
            expected_repository_id=self.intake["repositoryId"],
            expected_source_fingerprint=self.intake["sourceFingerprint"],
            expected_task_scope_fingerprint=self.intake["taskScopeFingerprint"],
        )
        self.assertTrue(result["valid"], result["errors"])

    def test_valid_outcomes_are_task_bound_and_terminal(self):
        for decision in ("REUSE", "NEW", "DOCUMENTED_EXCEPTION"):
            with self.subTest(decision=decision):
                self.assert_bound_valid(self.assessment(decision=decision))

    def test_new_requires_negative_search_and_exception_requires_reason(self):
        missing_negative = self.assessment(decision="NEW")
        missing_negative["negativeSearchEvidence"] = []
        result = validate_reuse_assessment(missing_negative)
        self.assertFalse(result["valid"])
        self.assertTrue(any("NEW requires" in error for error in result["errors"]))

        missing_reason = self.assessment(decision="DOCUMENTED_EXCEPTION")
        missing_reason["exceptionReason"] = None
        result = validate_reuse_assessment(missing_reason)
        self.assertFalse(result["valid"])
        self.assertTrue(any("DOCUMENTED_EXCEPTION requires" in error for error in result["errors"]))

    def test_stale_malformed_and_wrong_task_assessments_fail(self):
        stale = self.assessment()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stale["issuedAt"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        stale["expiresAt"] = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        self.assertFalse(validate_reuse_assessment(stale)["valid"])

        malformed = self.assessment()
        malformed["unexpected"] = True
        self.assertFalse(validate_reuse_assessment(malformed)["valid"])

        wrong_task = self.assessment()
        wrong_task["taskId"] = "another-task"
        result = validate_reuse_assessment(wrong_task, expected_task_id=self.intake["taskId"])
        self.assertFalse(result["valid"])
        self.assertTrue(any("current task binding" in error for error in result["errors"]))

    def test_mutation_gate_blocks_missing_invalid_or_stale_assessment(self):
        for assessment in (None, {"schemaVersion": "wrong"}, self._stale_assessment()):
            with self.subTest(assessment=assessment):
                gate = enforce_project_context_gate(
                    self.intake,
                    self.context,
                    self.receipt,
                    self.policy,
                    action="mutation",
                    reuse_assessment=assessment,
                )
                self.assertEqual("BLOCKED", gate["decision"])
                self.assertIn(gate["reuseAssessment"]["status"], {"MISSING", "INVALID"})
                self.assertTrue(validate_enforced_gate(gate)["valid"], validate_enforced_gate(gate)["errors"])

    def _stale_assessment(self) -> dict:
        value = self.assessment()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        value["issuedAt"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        value["expiresAt"] = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        return value

    def test_mutation_gate_allows_only_a_valid_current_assessment(self):
        gate = enforce_project_context_gate(
            self.intake,
            self.context,
            self.receipt,
            self.policy,
            action="mutation",
            reuse_assessment=self.assessment(),
        )
        self.assertEqual("ALLOW", gate["decision"])
        self.assertEqual("VALID", gate["reuseAssessment"]["status"])
        self.assertEqual("REUSE", gate["reuseAssessment"]["outcome"])
        self.assertTrue(validate_enforced_gate(gate)["valid"], validate_enforced_gate(gate)["errors"])

    def test_read_only_routing_does_not_require_a_reuse_assessment(self):
        read_only = copy.deepcopy(self.intake)
        read_only["mutationRequested"] = False
        gate = enforce_project_context_gate(
            read_only,
            self.context,
            self.receipt,
            self.policy,
            action="routing",
        )
        self.assertEqual("ALLOW", gate["decision"])
        self.assertEqual("NOT_REQUIRED", gate["reuseAssessment"]["status"])
        self.assertTrue(validate_enforced_gate(gate)["valid"], validate_enforced_gate(gate)["errors"])


if __name__ == "__main__":
    unittest.main()
