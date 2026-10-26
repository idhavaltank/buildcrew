from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.mobile_workflow import classify_mobile_workflow  # noqa: E402
from engine.model_selection import (  # noqa: E402
    select_fast_daily_model,
    validate_fast_daily_model_selection,
)


class FastModelSelectionTests(unittest.TestCase):
    def test_direct_selection_needs_no_strict_task_or_host_envelope(self):
        decision = classify_mobile_workflow({
            "platform": "React Native",
            "objective": "Add an empty state to the profile screen",
        })
        selection = select_fast_daily_model(decision)
        self.assertEqual("FAST_DAILY_DIRECT", selection["status"])
        self.assertEqual("model-fast", selection["model"])
        self.assertEqual("medium", selection["reasoningEffort"])
        self.assertTrue(selection["strictModelSelectionBypass"])
        self.assertEqual("SESSION_ONLY", selection["selectionBinding"])
        self.assertFalse(selection["dispatchable"])
        self.assertEqual([], selection["assignments"])
        for field in (
            "taskFacts",
            "sourceFingerprint",
            "policyFingerprint",
            "hostCatalog",
            "expiresAt",
            "coordinatorScope",
        ):
            self.assertNotIn(field, selection)
        self.assertEqual([], validate_fast_daily_model_selection(selection)["errors"])

    def test_normal_fast_daily_uses_balanced_model_high(self):
        decision = classify_mobile_workflow({
            "platform": "Flutter",
            "taskClass": "normal",
            "objective": "Refactor local screen state mapping",
        })
        selection = select_fast_daily_model(decision)
        self.assertEqual("FAST_DAILY_DIRECT", selection["status"])
        self.assertEqual("model-balanced", selection["model"])
        self.assertEqual("high", selection["reasoningEffort"])

    def test_full_safety_decision_is_rejected_by_direct_selector(self):
        decision = classify_mobile_workflow({
            "platform": "Android",
            "objective": "Implement offline queue persistence",
        })
        selection = select_fast_daily_model(decision)
        self.assertEqual("REJECTED", selection["status"])
        self.assertFalse(selection["strictModelSelectionBypass"])
        self.assertFalse(validate_fast_daily_model_selection(selection)["valid"])

    def test_tampered_fast_decision_cannot_change_model_or_enable_dispatch(self):
        decision = classify_mobile_workflow({
            "platform": "React Native",
            "objective": "Add a loading state to the profile screen",
        })
        tampered = copy.deepcopy(decision)
        tampered["model"] = "model-advanced"
        tampered["dispatchable"] = True
        selection = select_fast_daily_model(tampered)
        self.assertEqual("REJECTED", selection["status"])
        self.assertFalse(selection["dispatchable"])
        self.assertTrue(any("model" in error for error in selection["errors"]))

    def test_classifier_decision_with_worker_strategy_cannot_bypass(self):
        decision = classify_mobile_workflow({
            "platform": "React Native",
            "objective": "Add a loading state to the profile screen",
        })
        tampered = copy.deepcopy(decision)
        tampered["workerStrategy"] = "bounded_named_agents"
        selection = select_fast_daily_model(tampered)
        self.assertEqual("REJECTED", selection["status"])
        self.assertTrue(any("workerStrategy" in error for error in selection["errors"]))


if __name__ == "__main__":
    unittest.main()
