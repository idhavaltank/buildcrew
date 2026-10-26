import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from lifecycle_state import (  # noqa: E402
    StateTransitionError,
    _mint_validated_closure_evidence,
    can_transition,
    closure_eligible,
    transition,
)


class LifecycleStateTests(unittest.TestCase):
    def test_normal_work_reaches_ready_for_closure(self):
        evidence = _mint_validated_closure_evidence(
            task_id="task-1",
            source_fingerprint="source-1",
            evidence_digest="sha256:" + "a" * 64,
            gate_names=("review", "static"),
        )
        self.assertEqual(transition("RECEIVED", "IN_PROGRESS"), "IN_PROGRESS")
        self.assertTrue(can_transition("IN_PROGRESS", "READY_FOR_CLOSURE"))
        self.assertEqual(
            transition(
                "READY_FOR_CLOSURE",
                "CLOSED",
                closure_evidence=evidence,
            ),
            "CLOSED",
        )

    def test_failure_state_cannot_jump_to_closed(self):
        with self.assertRaises(StateTransitionError):
            transition("MISSING_COVERAGE", "CLOSED", closure_evidence=True)
        with self.assertRaises(StateTransitionError):
            transition("BLOCKED", "CLOSED", closure_evidence=True)

    def test_ready_for_closure_requires_complete_evidence(self):
        self.assertFalse(can_transition("READY_FOR_CLOSURE", "CLOSED"))
        with self.assertRaises(StateTransitionError):
            transition("READY_FOR_CLOSURE", "CLOSED")

    def test_recovery_must_return_to_in_progress(self):
        self.assertEqual(transition("TIMED_OUT", "IN_PROGRESS"), "IN_PROGRESS")
        self.assertEqual(transition("INVALIDATED", "IN_PROGRESS"), "IN_PROGRESS")

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(StateTransitionError):
            transition("UNKNOWN", "IN_PROGRESS")

    def test_closure_gates_are_explicit(self):
        self.assertFalse(closure_eligible({"static": "PASS", "review": "NOT_REQUIRED"}))
        self.assertFalse(closure_eligible({"static": "PASS", "review": "MISSING_COVERAGE"}))
        self.assertFalse(closure_eligible({}))
        self.assertFalse(closure_eligible(True))


if __name__ == "__main__":
    unittest.main()
