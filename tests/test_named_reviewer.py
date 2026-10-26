from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.model_selection import select_model_assignments  # noqa: E402
from engine.named_reviewer import (  # noqa: E402
    NamedReviewerContractError,
    artifact_digest,
    compute_profile_fingerprint,
    resolve_named_reviewer_assignment,
    validate_authorization_evidence_envelope,
    validate_named_reviewer_assignment,
    validate_task_review_binding,
)


NOW = "2026-08-24T08:30:00Z"
DIFF = "sha256:" + "d" * 64
SHARD = "sha256:" + "a" * 64


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class NamedReviewerContractTests(unittest.TestCase):
    def setUp(self):
        fixture = load_json(PACKAGE_ROOT / "tests/fixtures/model-selection/normal-feature.json")
        policy = load_json(PACKAGE_ROOT / "config/model-selection-v1.json")
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], policy)
        self.generic = next(item for item in selection["assignments"] if item["role"] == "worker")
        self.profile = {
            "name": "flutter-work-reviewer",
            "model": "model-fast",
            "reasoningEffort": "high",
            "capability": "flutter-work-review",
        }
        self.profile["profileFingerprint"] = compute_profile_fingerprint(self.profile)
        self.binding = {
            "schemaVersion": "ae-task-review-binding-v1",
            "bindingId": "binding-1",
            "taskId": self.generic["taskId"],
            "projectId": self.generic["projectId"],
            "repositoryId": self.generic["repositoryId"],
            "tenantId": self.generic["tenantId"],
            "acceptanceFingerprint": "sha256:" + "b" * 64,
            "criterionMapFingerprint": "sha256:" + "c" * 64,
            "sourceFingerprint": self.generic["sourceFingerprint"],
            "overallDiffFingerprint": DIFF,
            "writerOwner": "lead-writer",
            "requiredLenses": ["work"],
            "shardFingerprints": [SHARD],
            "hostId": self.generic["hostId"],
            "authorizationEpoch": 1,
            "issuedAt": self.generic["issuedAt"],
            "expiresAt": self.generic["expiresAt"],
        }
        self.envelope = {
            "schemaVersion": "ae-authorization-evidence-envelope-v1",
            "envelopeId": "envelope-1",
            "approvalId": "approval-1",
            "taskReviewBindingDigest": artifact_digest("ae-task-review-binding-v1", self.binding),
            "wrappedArtifactType": "model-assignment-v1",
            "wrappedArtifactDigest": artifact_digest("model-assignment-v1", self.generic),
            "taskId": self.generic["taskId"],
            "projectId": self.generic["projectId"],
            "repositoryId": self.generic["repositoryId"],
            "tenantId": self.generic["tenantId"],
            "hostId": self.generic["hostId"],
            "authorizationEpoch": 1,
            "producerId": "controller-1",
            "issuedAt": self.generic["issuedAt"],
            "expiresAt": self.generic["expiresAt"],
            "terminal": True,
        }

    def resolve(self, **overrides):
        arguments = {
            "generic_assignment": self.generic,
            "task_review_binding": self.binding,
            "authorization_envelope": self.envelope,
            "role_profile": self.profile,
            "overall_diff_fingerprint": DIFF,
            "shard_fingerprint": SHARD,
            "route": "DIRECT_WORK",
            "expected_profile_fingerprint": self.profile["profileFingerprint"],
            "supported_models": {"model-fast", "model-balanced", "model-advanced"},
            "supported_efforts": {"none", "minimal", "low", "medium", "high", "xhigh"},
            "now": NOW,
        }
        arguments.update(overrides)
        return resolve_named_reviewer_assignment(**arguments)

    def test_assignment_preserves_generic_bindings_but_uses_named_profile_model(self):
        named = self.resolve()

        self.assertEqual("SELECTED", named["status"])
        self.assertEqual(self.generic["assignmentId"], named["genericAssignmentId"])
        self.assertEqual(self.generic["scope"], named["scope"])
        self.assertEqual(self.generic["policyFingerprint"], named["policyFingerprint"])
        self.assertEqual("model-fast", named["configuredModel"])
        self.assertEqual("high", named["configuredReasoningEffort"])
        self.assertNotEqual(self.generic["resolvedModel"], named["configuredModel"])
        result = validate_named_reviewer_assignment(
            named,
            expected_generic_assignment=self.generic,
            expected_task_review_binding=self.binding,
            expected_authorization_envelope=self.envelope,
            expected_role_profile=self.profile,
            now=NOW,
        )
        self.assertTrue(result["valid"], result["errors"])

    def test_unsupported_exact_effort_is_unavailable_without_translation(self):
        profile = dict(self.profile)
        profile["reasoningEffort"] = "max"
        profile["profileFingerprint"] = compute_profile_fingerprint(profile)

        named = self.resolve(
            role_profile=profile,
            expected_profile_fingerprint=profile["profileFingerprint"],
        )

        self.assertEqual("CAPABILITY_UNAVAILABLE", named["status"])
        self.assertEqual("max", named["configuredReasoningEffort"])
        self.assertIsNone(named["resolvedReasoningEffort"])
        self.assertIsNone(named["resolvedModel"])

    def test_direct_fast_route_cannot_create_named_assignment(self):
        with self.assertRaises(NamedReviewerContractError):
            self.resolve(route="FAST_DAILY_DIRECT")

    def test_profile_fingerprint_and_payload_tampering_fail_closed(self):
        with self.assertRaises(NamedReviewerContractError):
            self.resolve(expected_profile_fingerprint="sha256:" + "0" * 64)

        named = self.resolve()
        tampered = copy.deepcopy(named)
        tampered["scope"]["paths"] = ["unrelated/**"]
        result = validate_named_reviewer_assignment(
            tampered,
            expected_generic_assignment=self.generic,
            expected_task_review_binding=self.binding,
            expected_authorization_envelope=self.envelope,
            expected_role_profile=self.profile,
            now=NOW,
        )
        self.assertFalse(result["valid"])

    def test_authority_inputs_are_strict_and_digest_bound(self):
        binding_result = validate_task_review_binding(self.binding, now=NOW)
        self.assertTrue(binding_result["valid"], binding_result["errors"])
        envelope_result = validate_authorization_evidence_envelope(
            self.envelope,
            wrapped_artifact=self.generic,
            task_review_binding=self.binding,
            now=NOW,
        )
        self.assertTrue(envelope_result["valid"], envelope_result["errors"])

        forged = dict(self.envelope)
        forged["wrappedArtifactDigest"] = "sha256:" + "0" * 64
        self.assertFalse(
            validate_authorization_evidence_envelope(
                forged,
                wrapped_artifact=self.generic,
                task_review_binding=self.binding,
                now=NOW,
            )["valid"]
        )

        wrong_type = dict(self.envelope)
        wrong_type["wrappedArtifactType"] = "untrusted-assignment-v1"
        wrong_type["wrappedArtifactDigest"] = artifact_digest(
            wrong_type["wrappedArtifactType"], self.generic
        )
        with self.assertRaises(NamedReviewerContractError):
            self.resolve(authorization_envelope=wrong_type)


if __name__ == "__main__":
    unittest.main()
