from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.host_dispatch import InMemoryAssignmentClaimStore  # noqa: E402
from engine.model_selection import select_model_assignments  # noqa: E402
from engine.named_reviewer import (  # noqa: E402
    artifact_digest,
    compute_profile_fingerprint,
    resolve_named_reviewer_assignment,
    validate_named_reviewer_receipt,
)
from engine.reviewer_host_bridge import (  # noqa: E402
    NamedReviewerDispatchError,
    dispatch_named_review_wave,
)
from engine.review_contracts import (  # noqa: E402
    compute_shard_fingerprint,
    validate_source_contract_closure,
)
from engine.review_invalidation import compute_prior_receipt_wrapper_digest  # noqa: E402


NOW = "2026-08-24T08:30:00Z"
DIFF = "sha256:" + "d" * 64
SHARD = "sha256:" + "a" * 64


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TrustedHost:
    # Deliberately attacker-controlled.  The source bridge must ignore this
    # attribute instead of treating it as authentication authority.
    authenticated_observations = True

    def __init__(self):
        self.calls = []
        self.observations = []

    def preflight(self, assignment):
        if assignment["configuredReasoningEffort"] == "max":
            raise NamedReviewerDispatchError("exact effort is unavailable")

    def execute(self, assignment):
        self.calls.append(assignment["assignmentId"])
        observation = {
            "observationId": "observation-1",
            "hostId": assignment["hostId"],
            "processIdentity": "process-1",
            "nonce": "nonce-1",
            "promptStarted": True,
            "observedModel": assignment["configuredModel"],
            "observedReasoningEffort": assignment["configuredReasoningEffort"],
            "executionStatus": "completed",
            "coverageStatus": "PASS",
            "verdict": "PASS",
            "evidence": ["review:evidence-1"],
            "blockers": [],
            "startedAt": "2026-08-24T08:31:00Z",
            "finishedAt": "2026-08-24T08:32:00Z",
            "terminal": True,
        }
        self.observations.append(copy.deepcopy(observation))
        return observation


class MissingVerdictHost(TrustedHost):
    def execute(self, assignment):
        observation = super().execute(assignment)
        observation["verdict"] = None
        return observation


class FailsOnSecondHost(TrustedHost):
    def execute(self, assignment):
        if self.calls:
            self.calls.append(assignment["assignmentId"])
            raise RuntimeError("simulated host failure")
        return super().execute(assignment)


class SelfAuthenticatingHost(TrustedHost):
    def execute(self, assignment):
        observation = super().execute(assignment)
        observation["authenticated"] = True
        return observation


class NamedReviewerHostBridgeTests(unittest.TestCase):
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
        self.named = resolve_named_reviewer_assignment(
            self.generic,
            self.binding,
            self.envelope,
            self.profile,
            overall_diff_fingerprint=DIFF,
            shard_fingerprint=SHARD,
            route="DIRECT_WORK",
            expected_profile_fingerprint=self.profile["profileFingerprint"],
            supported_models={"model-fast"},
            supported_efforts={"high"},
            now=NOW,
        )
        self.base_receipt = {
            "receiptVersion": "review-receipt-v2",
            "receiptId": "base-receipt-1",
            "taskId": self.generic["taskId"],
            "packetVersion": self.generic["packetVersion"],
            "planVersion": self.generic["planVersion"],
            "waveId": self.generic["waveId"],
            "attempt": self.generic["attempt"],
            "role": self.generic["role"],
            "host": self.generic["hostId"],
            "writerOwner": "lead-writer",
            "scope": {
                "repository": self.generic["repositoryId"],
                "sourceFingerprint": self.generic["sourceFingerprint"],
                "lane": self.generic["scope"]["scopeId"],
            },
            "startedAt": "2026-08-24T08:31:00Z",
            "finishedAt": "2026-08-24T08:32:00Z",
            "status": "completed",
            "confidence": "high",
            "coverage": {"requirements": ["criterion-1"], "files": ["src/feature.py"], "flows": ["flow-1"]},
            "findings": [],
            "commands": [],
            "artifacts": [],
            "blockers": [],
            "residualRisk": [],
            "terminal": True,
        }

    def test_source_bridge_never_mints_live_authenticated_evidence(self):
        host = TrustedHost()
        result = dispatch_named_review_wave(
            [self.named],
            base_review_receipts={self.named["assignmentId"]: self.base_receipt},
            host=host,
            claim_store=InMemoryAssignmentClaimStore(),
            trusted_assignments={self.named["assignmentId"]: self.named},
            now=NOW,
        )

        receipt = result["receipts"][0]
        self.assertFalse(result["completionEligible"])
        self.assertTrue(result["sourceContractComplete"])
        self.assertEqual("CAPABILITY_UNAVAILABLE", result["status"])
        self.assertEqual([], result["failureRecords"])
        self.assertFalse(receipt["live"])
        self.assertFalse(receipt["authenticatedObservation"])
        self.assertTrue(receipt["terminal"])
        self.assertEqual("model-fast", receipt["observedModel"])
        validation = validate_named_reviewer_receipt(
            receipt,
            expected_assignment=self.named,
            expected_host_observation=host.observations[-1],
            now=NOW,
            require_live=False,
        )
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertFalse(validation["liveReady"])

        live_validation = validate_named_reviewer_receipt(
            receipt,
            expected_assignment=self.named,
            expected_host_observation=host.observations[-1],
            now=NOW,
            require_live=True,
        )
        self.assertFalse(live_validation["valid"])
        self.assertFalse(live_validation["liveReady"])

        forged = copy.deepcopy(receipt)
        for key in ("hostObservationId", "processIdentity", "nonce"):
            forged[key] = None
        forged_validation = validate_named_reviewer_receipt(
            forged,
            expected_assignment=self.named,
            expected_host_observation=host.observations[-1],
            now=NOW,
            require_live=True,
        )
        self.assertFalse(forged_validation["valid"])
        self.assertFalse(forged_validation["liveReady"])

    def test_caller_host_cannot_self_assert_authenticated_observations(self):
        result = dispatch_named_review_wave(
            [self.named],
            base_review_receipts={self.named["assignmentId"]: self.base_receipt},
            host=SelfAuthenticatingHost(),
            claim_store=InMemoryAssignmentClaimStore(),
            trusted_assignments={self.named["assignmentId"]: self.named},
            now=NOW,
        )
        self.assertEqual("INCOMPLETE", result["status"])
        self.assertFalse(result["completionEligible"])
        self.assertEqual([], result["receipts"])
        self.assertTrue(
            any(
                "unknown field authenticated" in blocker
                for blocker in result["failureRecords"][0]["blockers"]
            )
        )

    def test_named_required_closure_needs_both_generic_and_live_named_receipts(self):
        shard_id = self.generic["scope"]["scopeId"]
        shard = {
            "shardId": shard_id,
            "lens": "work",
            "required": True,
            "namedReviewRequired": True,
            "criteriaIds": ["criterion-1"],
            "paths": ["src/feature.py"],
            "symbols": ["feature"],
            "dependencies": [],
            "evidenceRefs": [],
            "status": "PENDING",
        }
        shard["shardFingerprint"] = compute_shard_fingerprint(shard)
        binding = copy.deepcopy(self.binding)
        binding["bindingId"] = "binding-named-closure"
        binding["shardFingerprints"] = [shard["shardFingerprint"]]
        envelope = copy.deepcopy(self.envelope)
        envelope["envelopeId"] = "envelope-named-closure"
        envelope["taskReviewBindingDigest"] = artifact_digest(
            "ae-task-review-binding-v1", binding
        )
        named = resolve_named_reviewer_assignment(
            self.generic,
            binding,
            envelope,
            self.profile,
            overall_diff_fingerprint=DIFF,
            shard_fingerprint=shard["shardFingerprint"],
            route="DIRECT_WORK",
            expected_profile_fingerprint=self.profile["profileFingerprint"],
            supported_models={"model-fast"},
            supported_efforts={"high"},
            now=NOW,
        )
        host = TrustedHost()
        dispatched = dispatch_named_review_wave(
            [named],
            base_review_receipts={named["assignmentId"]: self.base_receipt},
            host=host,
            claim_store=InMemoryAssignmentClaimStore(),
            trusted_assignments={named["assignmentId"]: named},
            now=NOW,
        )
        manifest = {
            "schemaVersion": "review-shard-manifest-v1",
            "manifestId": "manifest-named-closure",
            "taskId": self.generic["taskId"],
            "projectId": self.generic["projectId"],
            "repositoryId": self.generic["repositoryId"],
            "tenantId": self.generic["tenantId"],
            "acceptanceFingerprint": binding["acceptanceFingerprint"],
            "sourceFingerprint": self.generic["sourceFingerprint"],
            "overallDiffFingerprint": DIFF,
            "requiredLenses": ["work"],
            "shards": [shard],
            "generatedAt": NOW,
        }
        ledger = {
            "schemaVersion": "review-finding-ledger-v1",
            "ledgerId": "ledger-named-closure",
            "taskId": self.generic["taskId"],
            "projectId": self.generic["projectId"],
            "repositoryId": self.generic["repositoryId"],
            "tenantId": self.generic["tenantId"],
            "sourceFingerprint": self.generic["sourceFingerprint"],
            "overallDiffFingerprint": DIFF,
            "findings": [],
            "updatedAt": NOW,
        }
        wrapper = {
            "receiptId": self.base_receipt["receiptId"],
            "taskId": self.generic["taskId"],
            "shardId": shard_id,
            "shardFingerprint": shard["shardFingerprint"],
            "overallDiffFingerprint": DIFF,
            "receiptDigest": artifact_digest("review-receipt-v2", self.base_receipt),
            "terminal": True,
            "reviewReceipt": self.base_receipt,
            "wrapperDigest": "pending",
        }
        wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
        bundle = {
            "genericAssignment": self.generic,
            "taskReviewBinding": binding,
            "authorizationEnvelope": envelope,
            "roleProfile": self.profile,
            "namedAssignment": named,
            "namedReceipt": dispatched["receipts"][0],
            "hostObservation": host.observations[0],
        }
        result = validate_source_contract_closure(
            manifest,
            {
                shard_id: {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": wrapper,
                    "namedReviewBundle": bundle,
                }
            },
            ledger,
            trusted_risk_authorizations={},
            now=NOW,
        )
        self.assertFalse(result["closureEligible"])
        self.assertTrue(
            any("authenticated live terminal evidence is required" in error for error in result["errors"]),
            result["errors"],
        )

        generic_only = {
            shard_id: {"evidenceType": "CURRENT_RECEIPT", "receipt": wrapper}
        }
        self.assertFalse(
            validate_source_contract_closure(
                manifest,
                generic_only,
                ledger,
                trusted_risk_authorizations={},
                now=NOW,
            )["closureEligible"]
        )

    def test_complete_wave_preflight_happens_before_any_claim_or_prompt(self):
        host = TrustedHost()
        invalid = copy.deepcopy(self.named)
        invalid["configuredModel"] = "invented-model"
        store = InMemoryAssignmentClaimStore()

        with self.assertRaises(NamedReviewerDispatchError):
            dispatch_named_review_wave(
                [self.named, invalid],
                base_review_receipts={
                    self.named["assignmentId"]: self.base_receipt,
                    invalid["assignmentId"]: self.base_receipt,
                },
                host=host,
                claim_store=store,
                trusted_assignments={self.named["assignmentId"]: self.named},
                now=NOW,
            )
        self.assertEqual([], host.calls)

    def test_shared_claim_store_rejects_replay(self):
        host = TrustedHost()
        store = InMemoryAssignmentClaimStore()
        kwargs = {
            "base_review_receipts": {self.named["assignmentId"]: self.base_receipt},
            "host": host,
            "claim_store": store,
            "trusted_assignments": {self.named["assignmentId"]: self.named},
            "now": NOW,
        }
        dispatch_named_review_wave([self.named], **kwargs)
        with self.assertRaises(NamedReviewerDispatchError):
            dispatch_named_review_wave([self.named], **kwargs)

    def test_completed_coverage_requires_a_terminal_verdict(self):
        host = MissingVerdictHost()
        result = dispatch_named_review_wave(
            [self.named],
            base_review_receipts={self.named["assignmentId"]: self.base_receipt},
            host=host,
            claim_store=InMemoryAssignmentClaimStore(),
            trusted_assignments={self.named["assignmentId"]: self.named},
            now=NOW,
        )
        self.assertEqual("INCOMPLETE", result["status"])
        self.assertFalse(result["completionEligible"])
        self.assertEqual([], result["receipts"])
        self.assertEqual("MISSING_COVERAGE", result["failureRecords"][0]["coverageStatus"])
        self.assertEqual("observation_validation", result["failureRecords"][0]["failureStage"])
        self.assertEqual(
            ["review:evidence-1"], result["failureRecords"][0]["evidence"]
        )

    def test_runtime_failure_returns_one_terminal_result_per_claimed_assignment(self):
        assignments = [self.named]
        for suffix in ("second", "third"):
            profile = {
                **self.profile,
                "name": f"flutter-work-reviewer-{suffix}",
                "capability": f"flutter-work-review-{suffix}",
            }
            profile["profileFingerprint"] = compute_profile_fingerprint(profile)
            assignments.append(
                resolve_named_reviewer_assignment(
                    self.generic,
                    self.binding,
                    self.envelope,
                    profile,
                    overall_diff_fingerprint=DIFF,
                    shard_fingerprint=SHARD,
                    route="DIRECT_WORK",
                    expected_profile_fingerprint=profile["profileFingerprint"],
                    supported_models={"model-fast"},
                    supported_efforts={"high"},
                    now=NOW,
                )
            )
        receipts = {item["assignmentId"]: self.base_receipt for item in assignments}
        trusted = {item["assignmentId"]: item for item in assignments}
        store = InMemoryAssignmentClaimStore()
        result = dispatch_named_review_wave(
            assignments,
            base_review_receipts=receipts,
            host=FailsOnSecondHost(),
            claim_store=store,
            trusted_assignments=trusted,
            now=NOW,
        )

        self.assertEqual("INCOMPLETE", result["status"])
        self.assertFalse(result["completionEligible"])
        self.assertEqual(3, result["terminalResultCount"])
        self.assertEqual(1, len(result["receipts"]))
        self.assertEqual(2, len(result["failureRecords"]))
        self.assertEqual("host_execute", result["failureRecords"][0]["failureStage"])
        self.assertEqual("UNKNOWN", result["failureRecords"][0]["promptStartStatus"])
        self.assertEqual("pre_prompt_failure", result["failureRecords"][1]["executionStatus"])
        self.assertEqual("NOT_STARTED", result["failureRecords"][1]["promptStartStatus"])
        terminal_ids = {item["assignmentId"] for item in result["receipts"] + result["failureRecords"]}
        self.assertEqual({item["assignmentId"] for item in assignments}, terminal_ids)
        with self.assertRaises(NamedReviewerDispatchError):
            dispatch_named_review_wave(
                assignments,
                base_review_receipts=receipts,
                host=TrustedHost(),
                claim_store=store,
                trusted_assignments=trusted,
                now=NOW,
            )

    def test_failed_generic_receipts_cannot_satisfy_named_review(self):
        for status in ("failed", "timed_out", "interrupted", "crashed", "shutdown"):
            with self.subTest(status=status):
                base = copy.deepcopy(self.base_receipt)
                base["status"] = status
                host = TrustedHost()
                with self.assertRaises(NamedReviewerDispatchError):
                    dispatch_named_review_wave(
                        [self.named],
                        base_review_receipts={self.named["assignmentId"]: base},
                        host=host,
                        claim_store=InMemoryAssignmentClaimStore(),
                        trusted_assignments={self.named["assignmentId"]: self.named},
                        now=NOW,
                    )
                self.assertEqual([], host.calls)


if __name__ == "__main__":
    unittest.main()
