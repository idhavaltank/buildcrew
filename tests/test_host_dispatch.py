import json
import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_dispatch import (  # noqa: E402
    DispatchBoundaryError,
    InMemoryAssignmentClaimStore,
    RecordingHostAdapter,
    dispatch_selection,
)
from engine.model_selection import select_model_assignments  # noqa: E402


FIXTURES = PACKAGE_ROOT / "tests" / "fixtures" / "model-selection"
POLICY = json.loads((PACKAGE_ROOT / "config" / "model-selection-v1.json").read_text())


def load(name):
    return json.loads((FIXTURES / name).read_text())


def dispatch(selection, host, *, trusted_selection=None):
    trusted = trusted_selection or selection
    return dispatch_selection(
        selection,
        host,
        expected_binding=trusted["taskBinding"],
        expected_policy_fingerprint=trusted["policyFingerprint"],
        expected_assignment_bindings=trusted["assignmentBindings"],
    )


class HostDispatchTests(unittest.TestCase):
    def test_direct_route_never_crosses_dispatch_boundary(self):
        fixture = load("direct-question.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        result = dispatch_selection(select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY), host)
        self.assertFalse(result["dispatched"])
        self.assertEqual([], host.calls)

    def test_normal_selection_passes_balanced_model_for_coordinator_and_workers(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        result = dispatch(selection, host)
        self.assertTrue(result["dispatched"])
        self.assertEqual("model-balanced", host.calls[0]["model"])
        self.assertEqual("high", host.calls[0]["reasoningEffort"])
        self.assertTrue(all(call["model"] == "model-balanced" for call in host.calls[1:]))
        self.assertTrue(all(call["reasoningEffort"] == "high" for call in host.calls[1:]))

    def test_coupled_selection_passes_advanced_model_for_coordinator_and_worker(self):
        fixture = load("coupled-security.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        dispatch(selection, host)
        self.assertEqual("model-advanced", host.calls[0]["model"])
        self.assertEqual("model-advanced", host.calls[1]["model"])

    def test_expired_assignment_cannot_cross_dispatch_boundary(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T09:00:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        with self.assertRaises(DispatchBoundaryError):
            dispatch(selection, host)
        self.assertEqual([], host.calls)

    def test_dispatch_requires_enclosing_selection_policy_and_task_binding(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        tampered_policy = copy.deepcopy(selection)
        tampered_policy["assignments"][0]["policyFingerprint"] = "0" * 64
        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered_policy, host, trusted_selection=selection)

        tampered_binding = copy.deepcopy(selection)
        tampered_binding["taskBinding"]["sourceFingerprint"] = "f" * 64
        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered_binding, host, trusted_selection=selection)

        tampered_assignment_binding = copy.deepcopy(selection)
        tampered_assignment_binding["assignmentBindings"][0]["scope"]["scopeId"] = "other-scope"
        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered_assignment_binding, host, trusted_selection=selection)

        missing_assignment_binding = copy.deepcopy(selection)
        missing_assignment_binding.pop("assignmentBindings")
        with self.assertRaises(DispatchBoundaryError):
            dispatch(missing_assignment_binding, host, trusted_selection=selection)

    def test_assignment_identity_and_scope_cannot_be_changed_at_dispatch(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        tampered_assignment = copy.deepcopy(selection)
        tampered_assignment["assignments"][0]["assignmentId"] = "assignment-" + "0" * 20
        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered_assignment, host, trusted_selection=selection)

        tampered_scope = copy.deepcopy(selection)
        tampered_scope["assignments"][0]["scope"]["scopeId"] = "other-scope"
        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered_scope, host, trusted_selection=selection)

    def test_dispatch_requires_trusted_binding_and_rejects_rebound_envelopes(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        with self.assertRaises(DispatchBoundaryError):
            dispatch_selection(selection, host)

        rebound = copy.deepcopy(selection)
        rebound["taskBinding"]["taskId"] = "rebound-task"
        rebound["assignments"][0]["taskId"] = "rebound-task"
        rebound["assignments"][1]["taskId"] = "rebound-task"
        rebound["assignmentBindings"][0]["taskId"] = "rebound-task"
        rebound["assignmentBindings"][1]["taskId"] = "rebound-task"
        with self.assertRaises(DispatchBoundaryError):
            dispatch(rebound, host, trusted_selection=selection)

        rebound_assignment = copy.deepcopy(selection)
        rebound_assignment["assignments"][1]["scope"]["scopeId"] = "rebound-scope"
        rebound_assignment["assignmentBindings"][1]["scope"]["scopeId"] = "rebound-scope"
        rebound_assignment["assignments"][1]["assignmentId"] = "assignment-" + "1" * 20
        rebound_assignment["assignmentBindings"][1]["assignmentId"] = "assignment-" + "1" * 20
        with self.assertRaises(DispatchBoundaryError):
            dispatch(rebound_assignment, host, trusted_selection=selection)

        invalidated = copy.deepcopy(selection)
        invalidated["status"] = "INVALIDATED"
        with self.assertRaises(DispatchBoundaryError):
            dispatch(invalidated, host, trusted_selection=selection)

        duplicate = copy.deepcopy(selection)
        duplicate["assignments"][1]["assignmentId"] = duplicate["assignments"][0]["assignmentId"]
        duplicate["assignmentBindings"][1]["assignmentId"] = duplicate["assignmentBindings"][0]["assignmentId"]
        with self.assertRaises(DispatchBoundaryError):
            dispatch(duplicate, host, trusted_selection=selection)
        self.assertEqual([], host.calls)

    def test_dispatch_preflights_every_assignment_before_recording_any_call(self):
        fixture = load("normal-feature.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        selection["assignments"][1]["expiresAt"] = "2026-08-24T08:00:00Z"
        with self.assertRaises(DispatchBoundaryError):
            dispatch(selection, host)
        self.assertEqual([], host.calls)

    def test_policy_approved_fast_model_fallback_dispatches_balanced_model_worker(self):
        fixture = load("fast-model-unavailable.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        result = dispatch(selection, host)
        self.assertTrue(result["dispatched"])
        self.assertEqual(1, len(host.calls))
        self.assertEqual("model-balanced", host.calls[0]["model"])
        self.assertEqual("high", host.calls[0]["reasoningEffort"])

    def test_same_assignment_cannot_be_dispatched_twice(self):
        fixture = load("fast-model-unavailable.json")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        dispatch(selection, host)
        with self.assertRaises(DispatchBoundaryError):
            dispatch(selection, host)

        self.assertEqual(1, len(host.calls))

    def test_shared_claim_store_rejects_replay_across_host_adapters(self):
        fixture = load("fast-model-unavailable.json")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        claims = InMemoryAssignmentClaimStore()
        first_host = RecordingHostAdapter(now="2026-08-24T08:30:00Z", claim_store=claims)
        second_host = RecordingHostAdapter(now="2026-08-24T08:30:00Z", claim_store=claims)

        dispatch(selection, first_host)
        with self.assertRaises(DispatchBoundaryError):
            dispatch(selection, second_host)

        self.assertEqual(1, len(first_host.calls))
        self.assertEqual([], second_host.calls)

    def test_mixed_coordinator_and_fallback_wave_commits_only_after_full_preflight(self):
        fixture = load("normal-feature.json")
        fixture["taskFacts"]["taskClass"] = "small"
        fixture["taskFacts"]["workerScopes"] = [fixture["taskFacts"]["workerScopes"][0]]
        fixture["hostCatalog"]["availableModels"].pop("model-fast")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        self.assertEqual("FALLBACK_EVIDENCED", selection["status"])
        result = dispatch(selection, host)

        self.assertTrue(result["dispatched"])
        self.assertEqual(["model-balanced", "model-balanced"], [call["model"] for call in host.calls])

    def test_invalidated_fallback_member_prevents_any_mixed_wave_commit(self):
        fixture = load("normal-feature.json")
        fixture["taskFacts"]["taskClass"] = "small"
        fixture["taskFacts"]["workerScopes"] = [fixture["taskFacts"]["workerScopes"][0]]
        fixture["hostCatalog"]["availableModels"].pop("model-fast")
        host = RecordingHostAdapter(now="2026-08-24T08:30:00Z")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        tampered = copy.deepcopy(selection)
        tampered["assignments"][1]["expiresAt"] = "2026-08-24T08:20:00Z"

        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered, host, trusted_selection=selection)
        self.assertEqual([], host.calls)

    def test_fallback_evidence_is_bound_at_dispatch(self):
        fixture = load("fast-model-unavailable.json")
        host = RecordingHostAdapter()
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)
        tampered = copy.deepcopy(selection)
        tampered["assignments"][0]["overrideEvidence"]["approvalId"] = "tampered"

        with self.assertRaises(DispatchBoundaryError):
            dispatch(tampered, host, trusted_selection=selection)
        self.assertEqual([], host.calls)

    def test_multiple_worker_fallback_wave_fails_closed_before_dispatch(self):
        fixture = load("normal-feature.json")
        fixture["hostCatalog"]["availableModels"].pop("model-fast")
        fixture["taskFacts"]["taskClass"] = "small"
        host = RecordingHostAdapter()
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        result = dispatch(selection, host)

        self.assertFalse(result["dispatched"])
        self.assertEqual([], host.calls)

    def test_final_audit_without_fast_model_does_not_dispatch_fallback(self):
        fixture = load("final-audit.json")
        fixture["hostCatalog"]["availableModels"].pop("model-fast")
        host = RecordingHostAdapter()
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], POLICY)

        result = dispatch(selection, host)

        self.assertFalse(result["dispatched"])
        self.assertEqual([], host.calls)


if __name__ == "__main__":
    unittest.main()
