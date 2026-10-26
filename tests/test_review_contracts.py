from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.review_contracts import (  # noqa: E402
    ReviewContractError,
    compute_resolution_evidence_digest,
    compute_resolution_receipt_digest,
    compute_resolution_receipt_id,
    compute_risk_authorization_digest,
    compute_risk_authorization_id,
    compute_shard_fingerprint,
    transition_finding,
    validate_finding_ledger,
    validate_review_shard_manifest,
    validate_source_contract_closure,
)
from engine.named_reviewer import artifact_digest  # noqa: E402
from engine.review_invalidation import (  # noqa: E402
    compute_prior_receipt_wrapper_digest,
    plan_review_invalidation,
)


NOW = "2026-09-16T08:30:00Z"
DIFF = "sha256:" + "d" * 64


def shard(shard_id="work-main", lens="work", required=True):
    value = {
        "shardId": shard_id,
        "lens": lens,
        "required": required,
        "namedReviewRequired": False,
        "criteriaIds": ["criterion-1"],
        "paths": ["engine/example.py"],
        "symbols": ["example"],
        "dependencies": [],
        "evidenceRefs": [],
        "status": "PENDING" if required else "NOT_REQUIRED",
    }
    value["shardFingerprint"] = compute_shard_fingerprint(value)
    return value


def finding(state="OPEN", severity="MEDIUM"):
    return {
        "findingId": "finding-1",
        "ownerLens": "work",
        "dependentLenses": ["architecture"],
        "shardId": "work-main",
        "severity": severity,
        "state": state,
        "title": "Example finding",
        "evidenceRefs": ["evidence-1"],
        "sourceReceiptIds": ["receipt-1"],
        "repairScope": [],
        "resolutionKind": None,
        "duplicateOf": None,
        "riskAcceptance": None,
        "resolutionReceiptId": None,
        "resolutionReceiptDigest": None,
    }


def bind_resolution(item):
    bound = copy.deepcopy(item)
    receipt = {
        "schemaVersion": "review-finding-resolution-v1",
        "receiptId": "pending",
        "receiptDigest": "sha256:" + "0" * 64,
        "taskId": "task-1",
        "projectId": "project-1",
        "repositoryId": "repo-1",
        "tenantId": None,
        "sourceFingerprint": "source-1",
        "overallDiffFingerprint": DIFF,
        "findingId": bound["findingId"],
        "shardId": bound["shardId"],
        "severity": bound["severity"],
        "resolutionKind": bound["resolutionKind"],
        "repairScope": list(bound["repairScope"]),
        "duplicateOf": bound["duplicateOf"],
        "reviewerIdentity": "review-host:reviewer-1",
        "reviewerAuthority": "review-host",
        "evidenceRefs": list(bound["evidenceRefs"]),
        "evidenceDigest": compute_resolution_evidence_digest(bound["evidenceRefs"]),
        "policyFingerprint": "sha256:" + "9" * 64,
        "issuedAt": "2026-09-16T08:00:00Z",
        "expiresAt": "2026-09-17T08:00:00Z",
        "status": "PASS",
        "terminal": True,
    }
    receipt["receiptId"] = compute_resolution_receipt_id(receipt)
    receipt["receiptDigest"] = compute_resolution_receipt_digest(receipt)
    bound["resolutionReceiptId"] = receipt["receiptId"]
    bound["resolutionReceiptDigest"] = receipt["receiptDigest"]
    bound["sourceReceiptIds"] = sorted(
        set(bound["sourceReceiptIds"] + [receipt["receiptId"]])
    )
    return bound, receipt


def current_receipt_wrapper(manifest, status="completed"):
    receipt = {
        "receiptVersion": "review-receipt-v2",
        "receiptId": "receipt-work-main",
        "taskId": manifest["taskId"],
        "packetVersion": "packet-v1",
        "planVersion": "plan-v1",
        "waveId": "wave-v1",
        "attempt": 1,
        "role": "reviewer",
        "host": "host-1",
        "writerOwner": "lead-writer",
        "scope": {
            "repository": manifest["repositoryId"],
            "sourceFingerprint": manifest["sourceFingerprint"],
            "lane": "work-main",
        },
        "startedAt": "2026-09-16T08:00:00Z",
        "finishedAt": "2026-09-16T08:10:00Z",
        "status": status,
        "confidence": "high",
        "coverage": {
            "requirements": ["criterion-1"],
            "files": ["engine/example.py"],
            "flows": ["review"],
        },
        "findings": [],
        "commands": [],
        "artifacts": [],
        "blockers": [],
        "residualRisk": [],
        "terminal": True,
    }
    wrapper = {
        "receiptId": receipt["receiptId"],
        "taskId": manifest["taskId"],
        "shardId": "work-main",
        "shardFingerprint": manifest["shards"][0]["shardFingerprint"],
        "overallDiffFingerprint": manifest["overallDiffFingerprint"],
        "receiptDigest": artifact_digest("review-receipt-v2", receipt),
        "terminal": True,
        "reviewReceipt": receipt,
        "wrapperDigest": "pending",
    }
    wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
    return wrapper


class ReviewContractTests(unittest.TestCase):
    def manifest(self):
        return {
            "schemaVersion": "review-shard-manifest-v1",
            "manifestId": "manifest-1",
            "taskId": "task-1",
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "tenantId": None,
            "acceptanceFingerprint": "sha256:" + "a" * 64,
            "sourceFingerprint": "source-1",
            "overallDiffFingerprint": DIFF,
            "requiredLenses": ["work"],
            "shards": [shard()],
            "generatedAt": NOW,
        }

    def ledger(self, item=None):
        return {
            "schemaVersion": "review-finding-ledger-v1",
            "ledgerId": "ledger-1",
            "taskId": "task-1",
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "tenantId": None,
            "sourceFingerprint": "source-1",
            "overallDiffFingerprint": DIFF,
            "findings": [item or finding()],
            "updatedAt": NOW,
        }

    def test_shard_manifest_is_strict_and_fingerprint_bound(self):
        result = validate_review_shard_manifest(self.manifest())
        self.assertTrue(result["valid"], result["errors"])

        changed = self.manifest()
        changed["shards"][0]["paths"] = ["engine/other.py"]
        self.assertFalse(validate_review_shard_manifest(changed)["valid"])

        unknown = self.manifest()
        unknown["unexpected"] = True
        self.assertFalse(validate_review_shard_manifest(unknown)["valid"])

    def test_finding_state_machine_accepts_only_declared_transitions(self):
        current = finding()
        current = transition_finding(current, "ACCEPTED_FOR_REPAIR", {"repairScope": ["engine/example.py"]})
        current = transition_finding(current, "REPAIR_IN_PROGRESS")
        current = transition_finding(current, "DELTA_REVIEW_PENDING")
        current = transition_finding(
            current,
            "RESOLVED",
            {
                "resolutionKind": "fixed",
                "evidenceRefs": ["receipt-2"],
                "resolutionReceiptId": "finding-resolution-test",
                "resolutionReceiptDigest": "sha256:" + "1" * 64,
            },
        )
        self.assertEqual("RESOLVED", current["state"])
        self.assertEqual("fixed", current["resolutionKind"])

        with self.assertRaises(ReviewContractError):
            transition_finding(finding(), "RESOLVED")

    def test_accepted_risk_is_bounded_and_not_allowed_for_high_severity(self):
        item = finding()
        item = transition_finding(item, "ACCEPTED_FOR_REPAIR", {"repairScope": ["engine/example.py"]})
        item = transition_finding(item, "REPAIR_IN_PROGRESS")
        item = transition_finding(item, "DELTA_REVIEW_PENDING")
        authorization = {
            "schemaVersion": "review-risk-authorization-v1",
            "authorizationId": "pending",
            "approvalId": "approval-1",
            "approverIdentity": "user:engineering-owner-1",
            "approverAuthority": "engineering-owner",
            "taskId": "task-1",
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "tenantId": None,
            "sourceFingerprint": "source-1",
            "overallDiffFingerprint": DIFF,
            "findingId": "finding-1",
            "scope": ["engine/example.py"],
            "policyFingerprint": "sha256:" + "9" * 64,
            "issuerId": "controller-1",
            "issuedAt": "2026-09-16T08:00:00Z",
            "expiresAt": "2026-09-17T08:00:00Z",
            "terminal": True,
        }
        authorization["authorizationId"] = compute_risk_authorization_id(authorization)
        item = transition_finding(
            item,
            "ACCEPTED_RISK",
            {
                "riskAcceptance": {
                    "authorizationId": authorization["authorizationId"],
                    "authorizationDigest": compute_risk_authorization_digest(authorization),
                    "approvalId": "approval-1",
                    "approverIdentity": "user:engineering-owner-1",
                    "approverAuthority": "engineering-owner",
                    "scope": ["engine/example.py"],
                    "rationale": "Bounded source-only residual",
                    "decidedAt": "2026-09-16T08:00:00Z",
                    "expiresAt": "2026-09-17T08:00:00Z",
                    "nextReviewAt": "2026-09-17T07:00:00Z",
                    "residualRisk": ["Runtime not verified"],
                }
            },
            now=NOW,
        )
        trusted = {authorization["authorizationId"]: authorization}
        result = validate_finding_ledger(
            self.ledger(item), trusted_risk_authorizations=trusted, now=NOW
        )
        self.assertTrue(result["valid"], result["errors"])

        self.assertFalse(validate_finding_ledger(self.ledger(item), now=NOW)["valid"])

        high = copy.deepcopy(item)
        high["severity"] = "HIGH"
        self.assertFalse(
            validate_finding_ledger(
                self.ledger(high), trusted_risk_authorizations=trusted, now=NOW
            )["valid"]
        )

        missing_identity = copy.deepcopy(item)
        del missing_identity["riskAcceptance"]["approverIdentity"]
        self.assertFalse(
            validate_finding_ledger(
                self.ledger(missing_identity),
                trusted_risk_authorizations=trusted,
                now=NOW,
            )["valid"]
        )

        wrong_scope = copy.deepcopy(item)
        wrong_scope["riskAcceptance"]["scope"] = ["engine/unrelated.py"]
        self.assertFalse(
            validate_finding_ledger(
                self.ledger(wrong_scope), trusted_risk_authorizations=trusted, now=NOW
            )["valid"]
        )

        forged_authority = copy.deepcopy(authorization)
        forged_authority["approverIdentity"] = "user:attacker"
        forged_authority["authorizationId"] = compute_risk_authorization_id(forged_authority)
        self.assertFalse(
            validate_finding_ledger(
                self.ledger(item),
                trusted_risk_authorizations={
                    item["riskAcceptance"]["authorizationId"]: forged_authority
                },
                now=NOW,
            )["valid"]
        )

    def test_duplicate_chain_must_end_at_a_non_duplicate_resolution(self):
        resolved = finding()
        resolved.update(
            {
                "findingId": "resolved-root",
                "state": "RESOLVED",
                "repairScope": ["engine/example.py"],
                "resolutionKind": "fixed",
            }
        )
        duplicate_one = finding()
        duplicate_one.update(
            {
                "findingId": "duplicate-one",
                "state": "DUPLICATE",
                "resolutionKind": "duplicate",
                "duplicateOf": "resolved-root",
            }
        )
        duplicate_two = copy.deepcopy(duplicate_one)
        duplicate_two.update({"findingId": "duplicate-two", "duplicateOf": "duplicate-one"})
        resolved, resolved_receipt = bind_resolution(resolved)
        duplicate_one, duplicate_one_receipt = bind_resolution(duplicate_one)
        duplicate_two, duplicate_two_receipt = bind_resolution(duplicate_two)
        trusted = {
            receipt["receiptId"]: receipt
            for receipt in (
                resolved_receipt,
                duplicate_one_receipt,
                duplicate_two_receipt,
            )
        }
        ledger = self.ledger()
        ledger["findings"] = [resolved, duplicate_one, duplicate_two]
        self.assertTrue(
            validate_finding_ledger(
                ledger, trusted_resolution_receipts=trusted, now=NOW
            )["valid"]
        )

        duplicate_one["duplicateOf"] = "duplicate-two"
        cycle = self.ledger()
        cycle["findings"] = [resolved, duplicate_one, duplicate_two]
        result = validate_finding_ledger(
            cycle, trusted_resolution_receipts=trusted, now=NOW
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("cycle" in error for error in result["errors"]))

    def test_closure_requires_every_required_lens_and_no_open_finding(self):
        manifest = self.manifest()
        evidence = {
            "work-main": {
                "evidenceType": "CURRENT_RECEIPT",
                "receipt": current_receipt_wrapper(manifest),
            }
        }
        closed = validate_source_contract_closure(
            manifest,
            evidence,
            self.ledger(finding()),
            now=NOW,
        )
        self.assertFalse(closed["closureEligible"])

        resolved = finding()
        resolved["state"] = "RESOLVED"
        resolved["resolutionKind"] = "fixed"
        resolved["repairScope"] = ["engine/example.py"]
        resolved, resolution_receipt = bind_resolution(resolved)
        untrusted = validate_source_contract_closure(
            manifest,
            evidence,
            self.ledger(resolved),
            now=NOW,
        )
        self.assertFalse(untrusted["closureEligible"])
        self.assertTrue(
            any("trusted resolution receipt" in error for error in untrusted["errors"])
        )
        eligible = validate_source_contract_closure(
            manifest,
            evidence,
            self.ledger(resolved),
            trusted_resolution_receipts={
                resolution_receipt["receiptId"]: resolution_receipt
            },
            now=NOW,
        )
        self.assertTrue(eligible["closureEligible"], eligible["errors"])

    def test_closure_rejects_cross_source_ledger_and_unknown_finding_shard(self):
        manifest = self.manifest()
        resolved = finding()
        resolved["state"] = "RESOLVED"
        resolved["resolutionKind"] = "fixed"
        resolved["repairScope"] = ["engine/example.py"]
        resolved, resolution_receipt = bind_resolution(resolved)
        trusted = {resolution_receipt["receiptId"]: resolution_receipt}

        wrong_source = self.ledger(resolved)
        wrong_source["sourceFingerprint"] = "source-other"
        source_result = validate_source_contract_closure(
            manifest,
            {
                "work-main": {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": current_receipt_wrapper(manifest),
                }
            },
            wrong_source,
            trusted_resolution_receipts=trusted,
            now=NOW,
        )
        self.assertFalse(source_result["closureEligible"])

        unknown_shard = self.ledger(resolved)
        unknown_shard["findings"][0]["shardId"] = "missing-shard"
        shard_result = validate_source_contract_closure(
            manifest,
            {
                "work-main": {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": current_receipt_wrapper(manifest),
                }
            },
            unknown_shard,
            trusted_resolution_receipts=trusted,
            now=NOW,
        )
        self.assertFalse(shard_result["closureEligible"])

    def test_closure_rejects_raw_statuses_failed_receipts_and_unapproved_risk(self):
        manifest = self.manifest()
        empty_ledger = self.ledger(finding())
        empty_ledger["findings"] = []

        raw = validate_source_contract_closure(
            manifest,
            {"work-main": "ACCEPTED_RISK"},
            empty_ledger,
            now=NOW,
        )
        self.assertFalse(raw["closureEligible"])

        failed = validate_source_contract_closure(
            manifest,
            {
                "work-main": {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": current_receipt_wrapper(manifest, status="failed"),
                }
            },
            empty_ledger,
            now=NOW,
        )
        self.assertFalse(failed["closureEligible"])

        risk_without_finding = validate_source_contract_closure(
            manifest,
            {"work-main": {"evidenceType": "ACCEPTED_RISK", "findingId": "missing"}},
            empty_ledger,
            now=NOW,
        )
        self.assertFalse(risk_without_finding["closureEligible"])

    def test_named_required_shard_cannot_close_with_generic_receipt_alone(self):
        manifest = self.manifest()
        manifest["shards"][0]["namedReviewRequired"] = True
        manifest["shards"][0]["shardFingerprint"] = compute_shard_fingerprint(
            manifest["shards"][0]
        )
        empty_ledger = self.ledger(finding())
        empty_ledger["findings"] = []

        generic_only = validate_source_contract_closure(
            manifest,
            {
                "work-main": {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": current_receipt_wrapper(manifest),
                }
            },
            empty_ledger,
            now=NOW,
        )
        self.assertFalse(generic_only["closureEligible"])

        forged_named = validate_source_contract_closure(
            manifest,
            {
                "work-main": {
                    "evidenceType": "CURRENT_RECEIPT",
                    "receipt": current_receipt_wrapper(manifest),
                    "namedReviewBundle": {},
                }
            },
            empty_ledger,
            now=NOW,
        )
        self.assertFalse(forged_named["closureEligible"])
        self.assertTrue(
            any("namedReviewBundle" in error for error in forged_named["errors"])
        )

    def test_reuse_requires_the_exact_trusted_invalidation_plan(self):
        manifest = self.manifest()
        prior = current_receipt_wrapper(manifest)
        prior["overallDiffFingerprint"] = "sha256:" + "1" * 64
        prior["wrapperDigest"] = compute_prior_receipt_wrapper_digest(prior)
        plan = plan_review_invalidation(
            manifest,
            [prior],
            {
                "paths": [],
                "symbols": [],
                "criteriaIds": [],
                "boundaries": [],
                "findingIds": [],
                "acceptanceChanged": False,
                "criterionMap": {},
            },
            current_overall_diff_fingerprint=manifest["overallDiffFingerprint"],
            producer_identity="review-host:controller-1",
            producer_authority="review-host",
            issued_at="2026-09-16T08:00:00Z",
            expires_at="2026-09-17T08:00:00Z",
        )
        evidence = {
            "work-main": {
                "evidenceType": "REUSE_DECISION",
                "decision": plan["reuseDecisions"][0],
                "priorReceipt": prior,
            }
        }
        empty_ledger = self.ledger(finding())
        empty_ledger["findings"] = []
        untrusted = validate_source_contract_closure(
            manifest, evidence, empty_ledger, now=NOW
        )
        self.assertFalse(untrusted["closureEligible"])
        trusted = validate_source_contract_closure(
            manifest,
            evidence,
            empty_ledger,
            trusted_invalidation_plans={plan["planId"]: plan},
            now=NOW,
        )
        self.assertTrue(trusted["closureEligible"], trusted["errors"])


if __name__ == "__main__":
    unittest.main()
