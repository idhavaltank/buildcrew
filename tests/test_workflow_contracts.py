import json
import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from workflow_contracts import (  # noqa: E402
    _closure_digest,
    validate_closure_bundle,
    validate_diagnosis_packet,
    validate_gate_set,
    validate_issue_record,
    validate_parallel_assignments,
    validate_receipt_binding,
    validate_review_artifacts,
    validate_transition_for_closure,
)
from engine.named_reviewer import artifact_digest  # noqa: E402
from engine.review_contracts import compute_shard_fingerprint  # noqa: E402
from engine.review_invalidation import compute_prior_receipt_wrapper_digest  # noqa: E402


def issue_record(root_cause=None):
    return {
        "issueVersion": "issue-record-v1",
        "taskId": "task-001",
        "symptom": "sync remains pending after resume",
        "reproduction": "resume app with queued mutation",
        "affectedLayers": ["mobile", "queue", "api"],
        "hypotheses": ["replay worker loses lease"],
        "evidence": ["queue log shows retry without ack"],
        "rootCause": root_cause,
        "regressionRisk": "replay ordering",
        "acceptanceMapping": ["sync completes after resume"],
    }


def diagnosis_packet():
    return {
        "packetVersion": "diagnosis-packet-v1",
        "taskId": "task-001",
        "scope": {"repository": "demo", "lane": "lane-1", "files": ["src/sync.py"]},
        "sourceFingerprint": "sha-001",
        "checkpoint": {"phase": "diagnosis", "lastCommand": "pytest", "nextAction": "inspect replay ack"},
        "expectedOutput": ["confirmed root cause or bounded hypothesis"],
        "timeoutSeconds": 60,
        "stopConditions": ["scope expands beyond sync worker"],
    }


def closure_review_artifacts():
    shard = {
        "shardId": "work-main",
        "lens": "work",
        "required": True,
        "namedReviewRequired": False,
        "criteriaIds": ["criterion-1"],
        "paths": ["src/sync.py"],
        "symbols": ["sync"],
        "dependencies": [],
        "evidenceRefs": [],
        "status": "PENDING",
    }
    shard["shardFingerprint"] = compute_shard_fingerprint(shard)
    manifest = {
        "schemaVersion": "review-shard-manifest-v1",
        "manifestId": "manifest-1",
        "taskId": "task-001",
        "projectId": "project-1",
        "repositoryId": "demo",
        "tenantId": None,
        "acceptanceFingerprint": "sha256:" + "a" * 64,
        "sourceFingerprint": "sha-001",
        "overallDiffFingerprint": "sha256:" + "d" * 64,
        "requiredLenses": ["work"],
        "shards": [shard],
        "generatedAt": "2026-09-16T08:30:00Z",
    }
    receipt = {
        "receiptVersion": "review-receipt-v2",
        "receiptId": "receipt-work-main",
        "taskId": "task-001",
        "packetVersion": "packet-v1",
        "planVersion": "plan-v1",
        "waveId": "wave-v1",
        "attempt": 1,
        "role": "reviewer",
        "host": "host-1",
        "writerOwner": "lead-writer",
        "scope": {"repository": "demo", "sourceFingerprint": "sha-001", "lane": "work-main"},
        "startedAt": "2026-09-16T08:00:00Z",
        "finishedAt": "2026-09-16T08:10:00Z",
        "status": "completed",
        "confidence": "high",
        "coverage": {"requirements": ["criterion-1"], "files": ["src/sync.py"], "flows": ["sync"]},
        "findings": [],
        "commands": [],
        "artifacts": [],
        "blockers": [],
        "residualRisk": [],
        "terminal": True,
    }
    wrapper = {
        "receiptId": receipt["receiptId"],
        "taskId": "task-001",
        "shardId": "work-main",
        "shardFingerprint": shard["shardFingerprint"],
        "overallDiffFingerprint": manifest["overallDiffFingerprint"],
        "receiptDigest": artifact_digest("review-receipt-v2", receipt),
        "terminal": True,
        "reviewReceipt": receipt,
        "wrapperDigest": "pending",
    }
    wrapper["wrapperDigest"] = compute_prior_receipt_wrapper_digest(wrapper)
    ledger = {
        "schemaVersion": "review-finding-ledger-v1",
        "ledgerId": "ledger-1",
        "taskId": "task-001",
        "projectId": "project-1",
        "repositoryId": "demo",
        "tenantId": None,
        "sourceFingerprint": "sha-001",
        "overallDiffFingerprint": manifest["overallDiffFingerprint"],
        "findings": [],
        "updatedAt": "2026-09-16T08:30:00Z",
    }
    return {
        "manifest": manifest,
        "findingLedger": ledger,
        "shardResults": {"work-main": {"evidenceType": "CURRENT_RECEIPT", "receipt": wrapper}},
    }


def closure_gate_evidence(name, status, artifact, reason=None):
    digest = _closure_digest("closure-gate-artifact-v1", artifact)
    seed = {
        "gate": name,
        "status": status,
        "artifactDigest": digest,
        "terminal": True,
        "notRequiredReason": reason,
    }
    return {
        "status": status,
        "evidenceId": "gate-evidence-" + _closure_digest(
            "closure-gate-evidence-v1", seed
        ).split(":", 1)[1][:40],
        "artifact": artifact,
        "artifactDigest": digest,
        "terminal": True,
        "notRequiredReason": reason,
    }


class WorkflowContractTests(unittest.TestCase):
    def test_symptom_record_is_valid_before_root_cause_confirmation(self):
        self.assertTrue(validate_issue_record(issue_record())["valid"])

    def test_on_disk_root_cause_and_diagnosis_fixtures_are_valid(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures"
        for name in ("symptom-only.json", "confirmed.json"):
            result = validate_issue_record(
                json.loads((fixture_root / "root-cause" / name).read_text())
            )
            self.assertTrue(result["valid"], result["errors"])
        diagnosis = json.loads(
            (fixture_root / "dispatch" / "valid-diagnosis.json").read_text()
        )
        self.assertTrue(validate_diagnosis_packet(diagnosis)["valid"])

    def test_confirmed_root_cause_requires_evidence(self):
        record = issue_record({"status": "confirmed", "claim": "lease is lost", "evidence": []})
        result = validate_issue_record(record)
        self.assertFalse(result["valid"])

    def test_diagnosis_packet_requires_bounded_stop_conditions(self):
        packet = diagnosis_packet()
        self.assertTrue(validate_diagnosis_packet(packet)["valid"])
        packet["timeoutSeconds"] = 0
        self.assertFalse(validate_diagnosis_packet(packet)["valid"])

    def test_parallel_assignments_reject_overlapping_files(self):
        assignments = [
            {"assignmentId": "explore", "role": "explorer", "files": ["src/sync.py"], "writer": False},
            {"assignmentId": "review", "role": "reviewer", "files": ["src/sync.py"], "writer": False},
        ]
        self.assertFalse(validate_parallel_assignments(assignments)["valid"])

    def test_parallel_assignments_allow_disjoint_roles_and_one_writer(self):
        assignments = [
            {"assignmentId": "explore", "role": "explorer", "files": ["src/sync.py"], "writer": False},
            {"assignmentId": "writer", "role": "writer", "files": ["src/replay.py"], "writer": True},
        ]
        result = validate_parallel_assignments(assignments)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["writerOwners"], ["writer"])

    def test_gate_set_does_not_close_on_missing_review(self):
        gates = {
            "static": "PASS",
            "review": "MISSING_COVERAGE",
            "runtime": "NOT_REQUIRED",
            "device": "NOT_REQUIRED",
            "publication": "NOT_REQUIRED",
            "activation": "NOT_REQUIRED",
            "learning": "PASS",
        }
        result = validate_gate_set(gates)
        self.assertTrue(result["valid"])
        self.assertFalse(result["closureEligible"])

    def test_raw_pass_statuses_and_boolean_cannot_close_lifecycle(self):
        gates = {name: "PASS" for name in (
            "static", "review", "runtime", "device", "publication", "activation", "learning"
        )}
        result = validate_gate_set(gates)
        self.assertTrue(result["valid"])
        self.assertFalse(result["closureEligible"])
        self.assertFalse(
            validate_transition_for_closure("READY_FOR_CLOSURE", "CLOSED", True)["valid"]
        )

    def test_validated_artifact_bundle_is_required_for_closure(self):
        review_artifacts = closure_review_artifacts()
        gates = {
            "static": "PASS",
            "review": "PASS",
            "runtime": "NOT_REQUIRED",
            "device": "NOT_REQUIRED",
            "publication": "NOT_REQUIRED",
            "activation": "NOT_REQUIRED",
            "learning": "PASS",
        }
        evidence = {}
        for name, status in gates.items():
            if name == "review":
                artifact = review_artifacts
                reason = None
            else:
                artifact = {"status": status, "terminal": True, "evidenceRef": f"test:{name}"}
                reason = None if status == "PASS" else f"{name} is outside this source-only task"
            evidence[name] = closure_gate_evidence(name, status, artifact, reason)
        bundle = {
            "taskId": "task-001",
            "sourceFingerprint": "sha-001",
            "gates": gates,
            "gateEvidence": evidence,
            "reviewArtifacts": review_artifacts,
        }
        validated = validate_closure_bundle(bundle, now="2026-09-16T08:30:00Z")
        self.assertTrue(validated["valid"], validated["errors"])
        transition_result = validate_transition_for_closure(
            "READY_FOR_CLOSURE",
            "CLOSED",
            bundle,
            now="2026-09-16T08:30:00Z",
        )
        self.assertTrue(transition_result["valid"], transition_result["errors"])

        forged = dict(bundle)
        forged["gateEvidence"] = dict(evidence)
        forged["gateEvidence"]["static"] = dict(evidence["static"])
        forged["gateEvidence"]["static"]["artifact"] = {"status": "PASS", "terminal": True}
        self.assertFalse(validate_closure_bundle(forged)["valid"])

    def test_review_bundle_cannot_supply_its_own_trusted_authority_stores(self):
        for field in (
            "riskAuthorizations",
            "resolutionReceipts",
            "invalidationPlans",
        ):
            with self.subTest(field=field):
                artifacts = closure_review_artifacts()
                artifacts[field] = {}
                result = validate_review_artifacts(artifacts)
                self.assertFalse(result["valid"])
                self.assertTrue(
                    any(f"unknown field {field}" in error for error in result["errors"]),
                    result["errors"],
                )

    def test_failure_state_cannot_transition_directly_to_closed(self):
        result = validate_transition_for_closure("TIMED_OUT", "CLOSED", True)
        self.assertFalse(result["valid"])

    def test_receipt_binding_rejects_wrong_wave_and_role(self):
        receipt = {
            "receiptVersion": "review-receipt-v2",
            "receiptId": "receipt-001",
            "taskId": "task-001",
            "packetVersion": "packet-1",
            "planVersion": "plan-1",
            "waveId": "wave-2",
            "attempt": 1,
            "role": "reviewer",
            "host": "host-1",
            "writerOwner": None,
            "scope": {"repository": "demo", "sourceFingerprint": "sha-001", "lane": "lane-1"},
            "startedAt": "2026-08-18T00:00:00Z",
            "finishedAt": "2026-08-18T00:01:00Z",
            "status": "completed",
            "confidence": "high",
            "coverage": {"requirements": ["REQ"], "files": ["src/sync.py"], "flows": ["sync"]},
            "findings": [],
            "commands": ["pytest"],
            "artifacts": [],
            "blockers": [],
            "residualRisk": [],
            "terminal": True,
        }
        dispatch = {
            "taskId": "task-001",
            "packetVersion": "packet-1",
            "planVersion": "plan-1",
            "waveId": "wave-1",
            "attempt": 1,
            "role": "reviewer",
            "host": "host-1",
            "writerOwner": None,
            "lane": "lane-1",
            "sourceFingerprint": "sha-001",
        }
        result = validate_receipt_binding(receipt, dispatch)
        self.assertFalse(result["valid"])
        self.assertTrue(any("waveId" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
