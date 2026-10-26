from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.mobile_workflow import (  # noqa: E402
    classify_mobile_workflow,
    resolve_mobile_model_selection,
    validate_mobile_workflow_decision,
)


class MobileWorkflowTests(unittest.TestCase):
    def assert_valid(self, intake):
        decision = classify_mobile_workflow(intake)
        validation = validate_mobile_workflow_decision(decision)
        self.assertTrue(validation["valid"], validation["errors"])
        return decision

    def test_routine_react_native_screen_uses_v1_fast_path_and_fast_model(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "targetPaths": ["apps/mobile/src/profile"],
            "objective": "Add an empty state to the React Native profile screen",
        })
        self.assertEqual("FAST_DAILY", decision["decision"])
        self.assertEqual("small", decision["taskClass"])
        self.assertEqual("model-fast", decision["model"])
        self.assertEqual("medium", decision["reasoningEffort"])
        self.assertIsNone(decision["coordinator"])
        self.assertIn("repo_wide_reusable_scan", decision["skippedGates"])
        self.assertIn("bounded_reuse_assessment", decision["requiredGates"])
        self.assertNotIn("bounded_reuse_assessment", decision["skippedGates"])

    def test_ui_background_word_does_not_trigger_lifecycle_safety_route(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "objective": "Update the profile screen background color",
        })
        self.assertEqual("FAST_DAILY", decision["decision"])

    def test_bare_retry_button_and_sync_icon_are_fast_ui_work(self):
        retry = self.assert_valid({
            "platform": "React Native",
            "objective": "Add a retry button to the empty state",
        })
        sync = self.assert_valid({
            "platform": "React Native",
            "objective": "Show a sync status icon in the toolbar",
        })
        self.assertEqual("FAST_DAILY", retry["decision"])
        self.assertEqual("FAST_DAILY", sync["decision"])

    def test_retry_logic_for_network_requests_keeps_full_safety(self):
        decision = self.assert_valid({
            "platform": "Android",
            "objective": "Implement retry logic for failed network requests",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])
        self.assertIn("retry", decision["matchedRiskSignals"])

    def test_authentication_and_token_work_cannot_use_fast_path(self):
        decision = self.assert_valid({
            "platform": "Expo",
            "objective": "Fix authentication token refresh handling",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])
        self.assertIn("authentication", decision["matchedRiskSignals"])
        self.assertIn("token", decision["matchedRiskSignals"])

    def test_highest_declared_class_wins_over_small_task_class(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "taskClass": "small",
            "riskTier": "cross repository",
            "objective": "Update the profile screen",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])
        self.assertEqual("cross-repository", decision["taskClass"])

    def test_normal_fast_lane_uses_balanced_model_like_v1_complexity_rule(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "taskClass": "normal",
            "objective": "Refactor the profile screen state mapping",
        })
        self.assertEqual("FAST_DAILY", decision["decision"])
        self.assertEqual("model-balanced", decision["model"])
        self.assertEqual("high", decision["reasoningEffort"])

    def test_flutter_is_supported_by_source_routing_without_claiming_runtime(self):
        decision = self.assert_valid({
            "platform": "Flutter",
            "objective": "Adjust the settings widget spacing",
        })
        self.assertEqual("FAST_DAILY", decision["decision"])
        self.assertEqual(["flutter"], decision["platforms"])
        self.assertFalse(decision["runtimeRequested"])

    def test_offline_sync_keeps_full_safety_and_sol(self):
        decision = self.assert_valid({
            "platform": "Android",
            "objective": "Replay the offline queue and resolve sync conflicts",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])
        self.assertEqual("coupled", decision["taskClass"])
        self.assertEqual("model-advanced", decision["model"])
        self.assertEqual("max", decision["reasoningEffort"])
        self.assertIn("offline", decision["matchedRiskSignals"])
        self.assertIn("sync", decision["matchedRiskSignals"])

    def test_explicit_emulator_validation_uses_runtime_route(self):
        decision = self.assert_valid({
            "platform": "iOS",
            "objective": "Run the smoke test on the simulator",
        })
        self.assertEqual("RUNTIME_VALIDATION", decision["decision"])
        self.assertEqual("model-balanced", decision["model"])
        self.assertTrue(decision["runtimeRequested"])
        self.assertFalse(decision["mutationRequested"])
        self.assertNotIn("bounded_reuse_assessment", decision["requiredGates"])

    def test_security_signal_cannot_be_downgraded_to_fast_path(self):
        decision = self.assert_valid({
            "platform": "Expo",
            "objective": "Fix permission handling for production release",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])
        self.assertEqual("model-advanced", decision["model"])

    def test_explicit_native_boundary_keeps_full_safety(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "objective": "Update the native module bridge lifecycle",
        })
        self.assertEqual("FULL_SAFETY", decision["decision"])

    def test_every_actionable_mobile_route_requires_bounded_reuse_assessment(self):
        intakes = (
            {"platform": "React Native", "objective": "Add an empty state"},
            {"platform": "Android", "objective": "Implement offline queue persistence"},
            {"platform": "iOS", "objective": "Update the empty state and run it on the simulator"},
        )
        for intake in intakes:
            with self.subTest(intake=intake):
                decision = self.assert_valid(intake)
                self.assertTrue(decision["mutationRequested"])
                self.assertIn("bounded_reuse_assessment", decision["requiredGates"])

    def test_validator_rejects_a_mutation_route_without_the_bounded_reuse_gate(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "objective": "Add an empty state",
        })
        tampered = copy.deepcopy(decision)
        tampered["requiredGates"].remove("bounded_reuse_assessment")
        validation = validate_mobile_workflow_decision(tampered)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("requires bounded_reuse_assessment" in error for error in validation["errors"]))

    def test_validator_rejects_skipping_the_bounded_reuse_gate_for_mutation(self):
        decision = self.assert_valid({
            "platform": "React Native",
            "objective": "Add an empty state",
        })
        tampered = copy.deepcopy(decision)
        tampered["skippedGates"].append("bounded_reuse_assessment")
        validation = validate_mobile_workflow_decision(tampered)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("cannot skip bounded_reuse_assessment" in error for error in validation["errors"]))

    def test_fast_daily_resolves_direct_non_dispatchable_selection(self):
        routing = resolve_mobile_model_selection({
            "platform": "React Native",
            "objective": "Add an empty state to the profile screen",
        })
        self.assertTrue(routing["valid"], routing["errors"])
        selection = routing["selection"]
        self.assertEqual("FAST_DAILY_DIRECT", selection["status"])
        self.assertEqual("model-fast", selection["model"])
        self.assertTrue(selection["strictModelSelectionBypass"])
        self.assertFalse(selection["dispatchable"])
        self.assertEqual([], selection["assignments"])

    def test_fast_daily_with_delegation_requires_strict_selection(self):
        routing = resolve_mobile_model_selection({
            "platform": "React Native",
            "objective": "Add an empty state to the profile screen",
            "requestedRoles": ["worker"],
        })
        self.assertTrue(routing["valid"], routing["errors"])
        self.assertEqual("FAST_DAILY", routing["decision"]["decision"])
        self.assertEqual("STRICT_REQUIRED", routing["selection"]["status"])
        self.assertFalse(routing["selection"]["strictModelSelectionBypass"])

    def test_full_safety_and_runtime_routes_require_strict_selection(self):
        full = resolve_mobile_model_selection({
            "platform": "Android",
            "objective": "Implement offline queue persistence",
        })
        runtime = resolve_mobile_model_selection({
            "platform": "iOS",
            "objective": "Run the smoke test on the simulator",
        })
        self.assertEqual("STRICT_REQUIRED", full["selection"]["status"])
        self.assertEqual("STRICT_REQUIRED", runtime["selection"]["status"])
        self.assertFalse(full["selection"]["strictModelSelectionBypass"])
        self.assertFalse(runtime["selection"]["strictModelSelectionBypass"])

    def test_non_mobile_intake_does_not_select_a_mobile_model(self):
        decision = self.assert_valid({"platform": "Web", "objective": "Update a dashboard card"})
        self.assertEqual("NOT_MOBILE", decision["decision"])
        self.assertIsNone(decision["model"])


if __name__ == "__main__":
    unittest.main()
