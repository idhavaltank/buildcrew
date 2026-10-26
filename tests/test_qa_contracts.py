from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.qa_contracts import SCHEMA_VERSIONS, validate_qa_contract  # noqa: E402


NOW = "2026-08-25T10:00:00Z"
LATER = "2026-08-25T11:00:00Z"
FINGERPRINT = "a" * 64


def valid_contracts() -> dict[str, dict]:
    return {
        "request": {
            "schemaVersion": SCHEMA_VERSIONS["request"], "requestId": "qa-request-1", "taskId": "task-1",
            "repository": "repo", "sourceFingerprint": FINGERPRINT, "objective": "Validate product search",
            "acceptanceCriteria": ["Search returns the expected product"], "targetPlatforms": ["WEB", "MOBILE"],
            "riskTier": "MEDIUM", "mode": "ADVISORY", "issuedAt": NOW, "expiresAt": LATER,
        },
        "workflow-map": {
            "schemaVersion": SCHEMA_VERSIONS["workflow-map"], "workflowId": "workflow-1", "requestId": "qa-request-1",
            "taskId": "task-1", "sourceFingerprint": FINGERPRINT, "objective": "Validate product search",
            "steps": [{"stepId": "step-1", "title": "Open search", "target": "SHARED", "entryPoint": "catalog",
                        "action": "Open product search", "expected": "Search field is visible", "evidenceRequired": ["screenshot"]}],
            "unresolved": [], "status": "READY", "generatedAt": NOW,
        },
        "execution-packet": {
            "schemaVersion": SCHEMA_VERSIONS["execution-packet"], "packetId": "packet-1", "requestId": "qa-request-1",
            "workflowId": "workflow-1", "taskId": "task-1", "sourceFingerprint": FINGERPRINT,
            "targetAllowlist": {"repository": "repo", "webTargets": ["dev"], "androidTargets": ["emulator-1"], "testDataScope": ["qa-user"]},
            "requiredCapabilities": ["browser runtime"], "requiredEvidence": ["screenshot"], "stepIds": ["step-1"],
            "timeoutSeconds": 60, "isolation": {"externalState": "DISPOSABLE_ONLY", "network": "ALLOWLISTED", "credentials": "SANDBOXED"},
            "mode": "ADVISORY", "status": "READY", "issuedAt": NOW, "expiresAt": LATER,
        },
        "finding": {
            "schemaVersion": SCHEMA_VERSIONS["finding"], "findingId": "finding-1", "packetId": "packet-1", "taskId": "task-1",
            "sourceFingerprint": FINGERPRINT, "severity": "MEDIUM", "status": "OPEN", "title": "Search result mismatch",
            "observed": "The result count differs", "expected": "The result count matches the acceptance criterion",
            "affectedSteps": ["step-1"], "evidenceRefs": [], "blocking": False, "createdAt": NOW, "residualRisk": ["Needs product review"],
        },
        "receipt": {
            "schemaVersion": SCHEMA_VERSIONS["receipt"], "receiptId": "receipt-1", "packetId": "packet-1", "requestId": "qa-request-1",
            "taskId": "task-1", "sourceFingerprint": FINGERPRINT, "adapter": "source_only",
            "runtimeIdentity": {"target": "source-only", "version": "n/a", "build": "source"}, "startedAt": NOW, "finishedAt": LATER,
            "status": "BLOCKED", "steps": [{"stepId": "step-1", "status": "BLOCKED", "evidenceRefs": []}], "evidence": [],
            "findings": [], "skippedOrBlocked": [{"stepId": "step-1", "reason": "Browser adapter unavailable"}], "sideEffects": [],
            "terminal": True, "residualRisk": ["Runtime proof is unavailable"],
        },
        "compatibility-matrix": {
            "schemaVersion": SCHEMA_VERSIONS["compatibility-matrix"], "matrixId": "matrix-1", "taskId": "task-1",
            "sourceFingerprint": FINGERPRINT, "criteria": [{"criterionId": "criterion-1", "description": "Search behavior",
              "webStatus": "PASS", "mobileStatus": "NOT_VERIFIED", "comparisonStatus": "NOT_VERIFIED", "evidenceRefs": [],
              "residualRisk": ["Mobile runtime unavailable"]}], "overallStatus": "NOT_VERIFIED", "generatedAt": NOW, "sideEffects": [],
        },
    }


class QaContractTests(unittest.TestCase):
    def test_all_contracts_validate(self):
        for kind, value in valid_contracts().items():
            with self.subTest(kind=kind):
                result = validate_qa_contract(kind, value)
                self.assertTrue(result["valid"], result["errors"])

    def test_unknown_fields_fail_closed_for_every_contract(self):
        for kind, value in valid_contracts().items():
            with self.subTest(kind=kind):
                invalid = copy.deepcopy(value)
                invalid["unexpected"] = True
                result = validate_qa_contract(kind, invalid)
                self.assertFalse(result["valid"])
                self.assertTrue(any("unknown field unexpected" in error for error in result["errors"]))

    def test_pass_receipt_requires_evidence_and_all_steps_to_pass(self):
        receipt = copy.deepcopy(valid_contracts()["receipt"])
        receipt["status"] = "PASS"
        result = validate_qa_contract("receipt", receipt)
        self.assertFalse(result["valid"])
        self.assertTrue(any("PASS receipt requires evidence" in error for error in result["errors"]))

    def test_confirmed_finding_requires_evidence(self):
        finding = copy.deepcopy(valid_contracts()["finding"])
        finding["status"] = "CONFIRMED"
        result = validate_qa_contract("finding", finding)
        self.assertFalse(result["valid"])
        self.assertTrue(any("confirmed finding requires evidence" in error for error in result["errors"]))

    def test_shadow_contracts_reject_side_effects(self):
        for kind in ("receipt", "compatibility-matrix"):
            with self.subTest(kind=kind):
                invalid = copy.deepcopy(valid_contracts()[kind])
                invalid["sideEffects"] = ["write live state"]
                result = validate_qa_contract(kind, invalid)
                self.assertFalse(result["valid"])
                self.assertTrue(any("sideEffects" in error for error in result["errors"]))

    def test_expired_windows_are_rejected(self):
        for kind in ("request", "execution-packet"):
            with self.subTest(kind=kind):
                invalid = copy.deepcopy(valid_contracts()[kind])
                invalid["expiresAt"] = NOW
                result = validate_qa_contract(kind, invalid)
                self.assertFalse(result["valid"])
                self.assertTrue(any("must be after issuedAt" in error for error in result["errors"]))

    def test_cli_accepts_valid_contract_and_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            valid_path = Path(directory) / "request.json"
            valid_path.write_text(json.dumps(valid_contracts()["request"]), encoding="utf-8")
            valid = subprocess.run(
                [sys.executable, "-B", "scripts/validate_qa_contracts.py", "request", str(valid_path)],
                cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            invalid_path = Path(directory) / "invalid.json"
            invalid_path.write_text("{", encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, "-B", "scripts/validate_qa_contracts.py", "request", str(invalid_path)],
                cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
