import json
import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from validate_hybrid_contracts import validate_packet, validate_receipt


def valid_packet():
    return {
        "schemaVersion": "hybrid-intake-v1",
        "taskId": "task-001",
        "request": {
            "rawText": "Fix the sync issue",
            "normalizedObjective": "Fix sync",
            "requestType": "bug",
            "constraints": [],
            "acceptanceCriteria": ["sync completes"],
        },
        "scope": {
            "project": "demo",
            "repository": "/repo",
            "repositoryRef": "main",
            "domain": "mobile",
            "sourceFingerprint": "sha-001",
        },
        "behavior": {
            "current": "stuck",
            "expected": "complete",
            "reproduction": "reopen app",
            "userImpact": "sync unavailable",
        },
        "evidence": {},
        "candidateProjection": None,
        "reconciliation": {},
        "execution": {},
        "verification": {},
        "learning": {},
        "state": {},
    }


def valid_receipt(status="completed"):
    return {
        "receiptVersion": "review-receipt-v2",
        "receiptId": "receipt-001",
        "taskId": "task-001",
        "packetVersion": "packet-1",
        "planVersion": "plan-1",
        "waveId": "wave-1",
        "attempt": 1,
        "role": "reviewer",
        "host": "test-host",
        "writerOwner": None,
        "scope": {
            "repository": "/repo",
            "sourceFingerprint": "sha-001",
            "lane": "lane-1",
        },
        "startedAt": "2026-08-18T00:00:00Z",
        "finishedAt": "2026-08-18T00:01:00Z",
        "status": status,
        "confidence": "high",
        "coverage": {
            "requirements": ["REQ-001"],
            "files": ["src/example.py"],
            "flows": ["sync"],
        },
        "findings": [],
        "commands": ["python3 -m unittest"],
        "artifacts": [],
        "blockers": [],
        "residualRisk": [],
        "terminal": True,
    }


def valid_candidate_projection():
    return {
        "schemaVersion": "candidate-projection-v1",
        "sourceFingerprint": "candidate-sha-001",
        "route": "NEW_TASK",
        "complexity": "COUPLED",
        "riskTier": "HIGH",
        "riskReasons": ["offline boundary"],
        "knowledgeScope": "TARGETED_REPOSITORY",
        "requiredCapabilities": ["regression-analysis"],
        "requiredCoverage": ["queue-replay"],
        "status": "AVAILABLE",
    }


class HybridContractTests(unittest.TestCase):
    def test_valid_packet(self):
        result = validate_packet(valid_packet())
        self.assertTrue(result["valid"], result["errors"])

    def test_valid_completed_receipt_is_eligible(self):
        result = validate_receipt(
            valid_receipt(),
            expected_task_id="task-001",
            expected_fingerprint="sha-001",
        )
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["completionEligible"])

    def test_valid_candidate_projection_is_accepted(self):
        packet = valid_packet()
        packet["candidateProjection"] = valid_candidate_projection()
        result = validate_packet(packet)
        self.assertTrue(result["valid"], result["errors"])

    def test_unknown_nested_candidate_field_is_rejected(self):
        packet = valid_packet()
        candidate = valid_candidate_projection()
        candidate["untrustedField"] = "must not be accepted"
        packet["candidateProjection"] = candidate
        result = validate_packet(packet)
        self.assertFalse(result["valid"])

    def test_legacy_unavailable_status_is_rejected(self):
        packet = valid_packet()
        candidate = valid_candidate_projection()
        candidate["status"] = "UNAVAILABLE"
        packet["candidateProjection"] = candidate
        result = validate_packet(packet)
        self.assertFalse(result["valid"])

    def test_wrong_task_is_rejected(self):
        result = validate_receipt(valid_receipt(), expected_task_id="other-task")
        self.assertFalse(result["valid"])
        self.assertFalse(result["completionEligible"])

    def test_wrong_fingerprint_is_rejected(self):
        result = validate_receipt(valid_receipt(), expected_fingerprint="sha-other")
        self.assertFalse(result["valid"])

    def test_timeout_is_not_completion(self):
        result = validate_receipt(valid_receipt("timed_out"))
        self.assertTrue(result["valid"], result["errors"])
        self.assertFalse(result["completionEligible"])

    def test_missing_required_field_is_rejected(self):
        receipt = valid_receipt()
        del receipt["terminal"]
        result = validate_receipt(receipt)
        self.assertFalse(result["valid"])

    def test_unknown_receipt_field_is_rejected(self):
        receipt = valid_receipt()
        receipt["untrustedField"] = "must not be accepted"
        result = validate_receipt(receipt)
        self.assertFalse(result["valid"])

    def test_behavior_fields_are_required_like_the_declared_schema(self):
        packet = valid_packet()
        del packet["behavior"]["expected"]
        result = validate_packet(packet)
        self.assertFalse(result["valid"])

    def test_on_disk_invalid_packet_fixtures_are_rejected(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "contracts"
        for fixture in sorted(fixture_root.glob("invalid-*.json")):
            result = validate_packet(json.loads(fixture.read_text()))
            self.assertFalse(result["valid"], fixture.name)

    def test_single_lifecycle_authority_is_explicitly_declared(self):
        skill_text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
        self.assertIn("only lifecycle authority", skill_text)
        self.assertIn("first and single engineering controller", skill_text)


if __name__ == "__main__":
    unittest.main()
