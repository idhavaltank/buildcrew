from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.review_contracts import compute_shard_fingerprint  # noqa: E402
from engine.named_reviewer import artifact_digest  # noqa: E402
from engine.review_invalidation import (  # noqa: E402
    ReviewInvalidationError,
    compute_prior_receipt_wrapper_digest,
    plan_review_invalidation,
    validate_invalidation_plan,
)


OLD_DIFF = "sha256:" + "1" * 64
NEW_DIFF = "sha256:" + "2" * 64
PLAN_AUTHORITY = {
    "producer_identity": "review-host:controller-1",
    "producer_authority": "review-host",
    "issued_at": "2026-09-16T08:00:00Z",
    "expires_at": "2026-09-17T08:00:00Z",
}


def make_shard(shard_id, lens, path, criterion):
    value = {
        "shardId": shard_id,
        "lens": lens,
        "required": True,
        "namedReviewRequired": False,
        "criteriaIds": [criterion],
        "paths": [path],
        "symbols": [],
        "dependencies": [],
        "evidenceRefs": [f"receipt:{shard_id}"],
        "status": "PASS",
    }
    value["shardFingerprint"] = compute_shard_fingerprint(value)
    return value


def make_review_receipt(receipt_id, shard_id):
    return {
        "receiptVersion": "review-receipt-v2",
        "receiptId": receipt_id,
        "taskId": "task-1",
        "packetVersion": "packet-v1",
        "planVersion": "plan-v1",
        "waveId": "wave-v1",
        "attempt": 1,
        "role": "reviewer",
        "host": "host-1",
        "writerOwner": "lead-writer",
        "scope": {"repository": "repo-1", "sourceFingerprint": "source-1", "lane": shard_id},
        "startedAt": "2026-09-16T07:00:00Z",
        "finishedAt": "2026-09-16T07:05:00Z",
        "status": "completed",
        "confidence": "high",
        "coverage": {"requirements": ["criterion"], "files": ["engine/file.py"], "flows": ["source-review"]},
        "findings": [],
        "commands": [],
        "artifacts": [],
        "blockers": [],
        "residualRisk": [],
        "terminal": True,
    }


class ReviewInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.work = make_shard("work-main", "work", "engine/work.py", "criterion-work")
        self.security = make_shard("security-storage", "security", "engine/storage.py", "criterion-storage")
        self.manifest = {
            "schemaVersion": "review-shard-manifest-v1",
            "manifestId": "manifest-1",
            "taskId": "task-1",
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "tenantId": None,
            "acceptanceFingerprint": "sha256:" + "a" * 64,
            "sourceFingerprint": "source-1",
            "overallDiffFingerprint": NEW_DIFF,
            "requiredLenses": ["work", "security"],
            "shards": [self.work, self.security],
            "generatedAt": "2026-09-16T08:00:00Z",
        }
        self.receipts = []
        for item in (self.work, self.security):
            receipt_id = f"receipt-{item['shardId']}"
            review_receipt = make_review_receipt(receipt_id, item["shardId"])
            wrapper = {
                "receiptId": f"receipt-{item['shardId']}",
                "taskId": "task-1",
                "shardId": item["shardId"],
                "shardFingerprint": item["shardFingerprint"],
                "overallDiffFingerprint": OLD_DIFF,
                "receiptDigest": artifact_digest("review-receipt-v2", review_receipt),
                "terminal": True,
                "reviewReceipt": review_receipt,
                "wrapperDigest": "pending",
            }
            wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
            self.receipts.append(wrapper)

    def test_changed_overall_diff_reuses_unchanged_unaffected_shards(self):
        plan = plan_review_invalidation(
            self.manifest,
            self.receipts,
            {
                "paths": [],
                "symbols": [],
                "criteriaIds": [],
                "boundaries": [],
                "findingIds": [],
                "acceptanceChanged": False,
                "criterionMap": {},
            },
            current_overall_diff_fingerprint=NEW_DIFF,
            **PLAN_AUTHORITY,
        )
        self.assertEqual([], plan["rerunShards"])
        self.assertEqual(["security-storage", "work-main"], plan["reusedShards"])
        self.assertTrue(all(item["priorReceiptDigest"].startswith("sha256:") for item in plan["reuseDecisions"]))
        validation = validate_invalidation_plan(plan, self.manifest, now="2026-09-16T08:30:00Z")
        self.assertTrue(validation["valid"], validation["errors"])

        forged = copy.deepcopy(plan)
        forged["normalizedChangeSet"]["paths"] = ["engine/hidden-change.py"]
        self.assertFalse(
            validate_invalidation_plan(
                forged, self.manifest, now="2026-09-16T08:30:00Z"
            )["valid"]
        )

    def test_only_affected_path_is_invalidated(self):
        plan = plan_review_invalidation(
            self.manifest,
            self.receipts,
            {
                "paths": ["engine/storage.py"],
                "symbols": [],
                "criteriaIds": [],
                "boundaries": [],
                "findingIds": [],
                "acceptanceChanged": False,
                "criterionMap": {},
            },
            current_overall_diff_fingerprint=NEW_DIFF,
            **PLAN_AUTHORITY,
        )
        self.assertEqual(["security-storage"], plan["rerunShards"])
        self.assertEqual(["work-main"], plan["reusedShards"])

    def test_acceptance_change_without_narrow_map_invalidates_every_required_shard(self):
        plan = plan_review_invalidation(
            self.manifest,
            self.receipts,
            {
                "paths": [],
                "symbols": [],
                "criteriaIds": [],
                "boundaries": [],
                "findingIds": [],
                "acceptanceChanged": True,
                "criterionMap": {},
            },
            current_overall_diff_fingerprint=NEW_DIFF,
            **PLAN_AUTHORITY,
        )
        self.assertEqual(["security-storage", "work-main"], plan["rerunShards"])

    def test_acceptance_change_with_unknown_or_incomplete_map_invalidates_every_required_shard(self):
        for criterion_map in (
            {"criterion-storage": ["unknown-shard"]},
            {
                "criterion-storage": ["security-storage"],
                "stale-extra-criterion": ["work-main"],
            },
        ):
            with self.subTest(criterion_map=criterion_map):
                plan = plan_review_invalidation(
                    self.manifest,
                    self.receipts,
                    {
                        "paths": [],
                        "symbols": [],
                        "criteriaIds": ["criterion-storage"],
                        "boundaries": [],
                        "findingIds": [],
                        "acceptanceChanged": True,
                        "criterionMap": criterion_map,
                    },
                    current_overall_diff_fingerprint=NEW_DIFF,
                    **PLAN_AUTHORITY,
                )
                self.assertEqual(["security-storage", "work-main"], plan["rerunShards"])

    def test_changed_finding_reruns_owner_and_declared_dependent_lenses(self):
        architecture = make_shard("architecture-storage", "architecture", "engine/contracts.py", "criterion-architecture")
        self.manifest["requiredLenses"].append("architecture")
        self.manifest["shards"].append(architecture)
        review_receipt = make_review_receipt("receipt-architecture-storage", "architecture-storage")
        wrapper = {
                "receiptId": "receipt-architecture-storage",
                "taskId": "task-1",
                "shardId": "architecture-storage",
                "shardFingerprint": architecture["shardFingerprint"],
                "overallDiffFingerprint": OLD_DIFF,
                "receiptDigest": artifact_digest("review-receipt-v2", review_receipt),
                "terminal": True,
                "reviewReceipt": review_receipt,
                "wrapperDigest": "pending",
            }
        wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
        self.receipts.append(wrapper)
        ledger = {
            "schemaVersion": "review-finding-ledger-v1",
            "ledgerId": "ledger-1",
            "taskId": "task-1",
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "tenantId": None,
            "sourceFingerprint": "source-1",
            "overallDiffFingerprint": NEW_DIFF,
            "findings": [
                {
                    "findingId": "finding-storage",
                    "ownerLens": "security",
                    "dependentLenses": ["architecture"],
                    "shardId": "security-storage",
                    "severity": "HIGH",
                    "state": "OPEN",
                    "title": "Storage boundary requires repair",
                    "evidenceRefs": ["evidence-1"],
                    "sourceReceiptIds": ["receipt-security-storage"],
                    "repairScope": [],
                    "resolutionKind": None,
                    "duplicateOf": None,
                    "riskAcceptance": None,
                    "resolutionReceiptId": None,
                    "resolutionReceiptDigest": None,
                }
            ],
            "updatedAt": "2026-09-16T08:00:00Z",
        }
        plan = plan_review_invalidation(
            self.manifest,
            self.receipts,
            {
                "paths": [],
                "symbols": [],
                "criteriaIds": [],
                "boundaries": [],
                "findingIds": ["finding-storage"],
                "acceptanceChanged": False,
                "criterionMap": {},
            },
            current_overall_diff_fingerprint=NEW_DIFF,
            **PLAN_AUTHORITY,
            finding_ledger=ledger,
        )
        self.assertEqual(["architecture-storage", "security-storage"], plan["rerunShards"])
        self.assertEqual(["work-main"], plan["reusedShards"])

        for key, different in (
            ("projectId", "project-other"),
            ("repositoryId", "repo-other"),
            ("tenantId", "tenant-other"),
            ("sourceFingerprint", "source-other"),
            ("overallDiffFingerprint", "sha256:" + "9" * 64),
        ):
            with self.subTest(binding=key):
                mismatched = copy.deepcopy(ledger)
                mismatched[key] = different
                with self.assertRaises(ReviewInvalidationError):
                    plan_review_invalidation(
                        self.manifest,
                        self.receipts,
                        {
                            "paths": [],
                            "symbols": [],
                            "criteriaIds": [],
                            "boundaries": [],
                            "findingIds": ["finding-storage"],
                            "acceptanceChanged": False,
                            "criterionMap": {},
                        },
                        current_overall_diff_fingerprint=NEW_DIFF,
                        **PLAN_AUTHORITY,
                        finding_ledger=mismatched,
                    )

    def test_non_boolean_terminal_and_unsafe_paths_fail_closed_without_mutating_inputs(self):
        receipts = copy.deepcopy(self.receipts)
        receipts[0]["terminal"] = 1
        original_manifest = copy.deepcopy(self.manifest)
        with self.assertRaises(ReviewInvalidationError):
            plan_review_invalidation(
                self.manifest,
                receipts,
                {
                    "paths": ["../escape.py"],
                    "symbols": [],
                    "criteriaIds": [],
                    "boundaries": [],
                    "findingIds": [],
                    "acceptanceChanged": False,
                    "criterionMap": {},
                },
                current_overall_diff_fingerprint=NEW_DIFF,
                **PLAN_AUTHORITY,
            )
        self.assertEqual(original_manifest, self.manifest)

    def test_mutated_review_receipt_cannot_reuse_an_old_immutable_digest(self):
        receipts = copy.deepcopy(self.receipts)
        receipts[0]["reviewReceipt"]["coverage"]["files"] = ["engine/tampered.py"]
        with self.assertRaises(ReviewInvalidationError):
            plan_review_invalidation(
                self.manifest,
                receipts,
                {
                    "paths": [],
                    "symbols": [],
                    "criteriaIds": [],
                    "boundaries": [],
                    "findingIds": [],
                    "acceptanceChanged": False,
                    "criterionMap": {},
                },
                current_overall_diff_fingerprint=NEW_DIFF,
                **PLAN_AUTHORITY,
            )

    def test_failed_terminal_receipts_are_never_reuse_eligible(self):
        for status in ("failed", "timed_out", "interrupted", "crashed", "shutdown"):
            with self.subTest(status=status):
                receipts = copy.deepcopy(self.receipts)
                for wrapper in receipts:
                    wrapper["reviewReceipt"]["status"] = status
                    wrapper["receiptDigest"] = artifact_digest(
                        "review-receipt-v2", wrapper["reviewReceipt"]
                    )
                    wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
                with self.assertRaises(ReviewInvalidationError):
                    plan_review_invalidation(
                        self.manifest,
                        receipts,
                        {
                            "paths": [],
                            "symbols": [],
                            "criteriaIds": [],
                            "boundaries": [],
                            "findingIds": [],
                            "acceptanceChanged": False,
                            "criterionMap": {},
                        },
                        current_overall_diff_fingerprint=NEW_DIFF,
                        **PLAN_AUTHORITY,
                    )

    def test_wrapper_metadata_cannot_be_relabelled_without_invalidating_its_digest(self):
        receipts = copy.deepcopy(self.receipts)
        receipts[0]["shardFingerprint"] = self.security["shardFingerprint"]
        with self.assertRaises(ReviewInvalidationError):
            plan_review_invalidation(
                self.manifest,
                receipts,
                {
                    "paths": [],
                    "symbols": [],
                    "criteriaIds": [],
                    "boundaries": [],
                    "findingIds": [],
                    "acceptanceChanged": False,
                    "criterionMap": {},
                },
                current_overall_diff_fingerprint=NEW_DIFF,
                **PLAN_AUTHORITY,
            )

    def test_receipt_from_another_review_lane_cannot_be_rebound_to_a_shard(self):
        receipts = copy.deepcopy(self.receipts)
        receipts[0]["reviewReceipt"]["scope"]["lane"] = "security-storage"
        receipts[0]["receiptDigest"] = artifact_digest(
            "review-receipt-v2", receipts[0]["reviewReceipt"]
        )
        receipts[0]["wrapperDigest"] = compute_prior_receipt_wrapper_digest(receipts[0])
        with self.assertRaises(ReviewInvalidationError):
            plan_review_invalidation(
                self.manifest,
                receipts,
                {
                    "paths": [],
                    "symbols": [],
                    "criteriaIds": [],
                    "boundaries": [],
                    "findingIds": [],
                    "acceptanceChanged": False,
                    "criterionMap": {},
                },
                current_overall_diff_fingerprint=NEW_DIFF,
                **PLAN_AUTHORITY,
            )


if __name__ == "__main__":
    unittest.main()
