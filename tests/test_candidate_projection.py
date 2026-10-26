import os
import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from candidate_projection import (  # noqa: E402
    CANDIDATE_UNAVAILABLE,
    CandidateProjector,
    CandidateProjectionError,
    INVALID,
    infer_risk_signals,
    project_optional,
    resolve_candidate_root,
)


class CandidateProjectionTests(unittest.TestCase):
    def test_missing_candidate_is_explicit_and_side_effect_free(self):
        result = project_optional(None, {"rawText": "Explain this"})
        self.assertEqual(result["status"], CANDIDATE_UNAVAILABLE)
        self.assertEqual(result["sourceFingerprint"], "unavailable")
        self.assertEqual(result["riskReasons"], ["candidate source was not supplied"])

    def test_invalid_candidate_path_is_not_treated_as_unavailable(self):
        result = project_optional(
            "/definitely/missing/candidate",
            {"rawText": "Explain this"},
        )
        self.assertEqual(result["status"], INVALID)

    def test_skill_directory_resolves_to_candidate_project_root_when_present(self):
        candidate_root = os.environ.get("general_CANDIDATE_ROOT")
        if not candidate_root:
            self.skipTest("general_CANDIDATE_ROOT is not configured")
        resolved = resolve_candidate_root(candidate_root)
        self.assertIsNotNone(resolved)
        self.assertTrue((resolved / "src" / "autonomous_engineering").is_dir())

    def test_supplied_candidate_produces_source_only_projection(self):
        candidate_root = os.environ.get("general_CANDIDATE_ROOT")
        if not candidate_root:
            self.skipTest("general_CANDIDATE_ROOT is not configured")
        result = project_optional(
            candidate_root,
            {
                "rawText": "Make this mutation work offline with queue replay",
                "requestType": "change",
            },
        )
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["complexity"], "COUPLED")
        self.assertEqual(result["riskTier"], "HIGH")
        self.assertEqual(len(result["sourceFingerprint"]), 64)

    def test_credential_material_is_rejected_before_candidate_invocation(self):
        candidate_root = os.environ.get("general_CANDIDATE_ROOT")
        if not candidate_root:
            self.skipTest("general_CANDIDATE_ROOT is not configured")
        resolved = resolve_candidate_root(candidate_root)
        self.assertIsNotNone(resolved)
        with self.assertRaises(CandidateProjectionError):
            CandidateProjector(resolved).project({
                "rawText": "Review token=do-not-forward",
                "requestType": "review",
            })

    def test_non_source_only_diagnosis_output_is_rejected(self):
        candidate_root = os.environ.get("general_CANDIDATE_ROOT")
        if not candidate_root:
            self.skipTest("general_CANDIDATE_ROOT is not configured")
        resolved = resolve_candidate_root(candidate_root)
        self.assertIsNotNone(resolved)

        def runner(arguments):
            command = arguments[0]
            if command == "status":
                return {
                    "mode": "SOURCE_ONLY",
                    "active": False,
                    "installed": False,
                    "legacyRuntime": "UNTOUCHED",
                }
            if command == "classify":
                return {
                    "route": "NEW_TASK",
                    "knowledgeScope": "TARGETED_REPOSITORY",
                    "invokesLegacyRuntime": False,
                }
            if command == "diagnosis-dispatch-plan":
                return {
                    "mode": "SOURCE_ONLY",
                    "executed": True,
                    "selection": {"complexity": "SMALL", "reasons": []},
                    "dispatchPlan": {"waves": []},
                }
            if command == "explain-tier":
                return {"tier": "T0", "reasons": []}
            raise AssertionError(command)

        with self.assertRaises(CandidateProjectionError):
            CandidateProjector(resolved, runner=runner).project({
                "rawText": "Implement a safe change",
                "requestType": "change",
            })

    def test_risk_signal_inference_is_sorted_and_deduplicated(self):
        result = infer_risk_signals({
            "rawText": "Replay the offline queue with auth token handling",
        })
        self.assertEqual(result, ("auth_session", "offline_sync", "queue_replay"))


if __name__ == "__main__":
    unittest.main()
