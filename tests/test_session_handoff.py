from __future__ import annotations

import copy
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.session_handoff import (  # noqa: E402
    HandoffContractError,
    build_handoff_packet,
    classify_handoff_attachment,
    compute_acceptance_fingerprint,
    discover_handoff_packets,
    validate_handoff_packet,
    write_handoff_packet,
)


NOW = "2026-09-16T08:00:00Z"


class SessionHandoffTests(unittest.TestCase):
    def setUp(self):
        acceptance_criteria = ["Source contracts pass"]
        acceptance_fingerprint = compute_acceptance_fingerprint(acceptance_criteria)
        self.context = {
            "handoffId": "handoff-1",
            "taskId": "task-1",
            "sourceSessionId": "session-1",
            "repository": {
                "projectId": "project-1",
                "repositoryId": "repo-1",
                "worktreeId": "worktree-1",
                "branch": "feature/restoration",
                "head": "a" * 40,
                "sourceRef": "workspace-v2",
            },
            "task": {
                "objective": "Restore bounded capabilities",
                "route": "DIRECT_WORK",
                "risk": "medium",
                "acceptanceCriteria": acceptance_criteria,
                "acceptanceFingerprint": acceptance_fingerprint,
                "policyVersion": "1.1.0",
                "planVersion": "plan-v1",
            },
            "source": {
                "sourceFingerprint": "sha256:" + "c" * 64,
                "overallDiffFingerprint": "sha256:" + "d" * 64,
                "dirty": True,
                "dirtyFingerprint": "sha256:" + "e" * 64,
                "changedPaths": ["engine/session_handoff.py"],
            },
            "phase": {"current": "implementation", "next": "review"},
            "completedWork": ["Added strict source contracts"],
            "decisions": ["Active remains read-only"],
            "findings": [],
            "verification": [{"check": "focused-tests", "status": "PASS", "evidenceRef": "test:handoff"}],
            "blockers": [],
            "residualRisk": ["Live session consumption not verified"],
            "nextAction": {
                "objective": "Run focused tests",
                "readScope": ["tests/test_session_handoff.py"],
                "writeScope": [],
                "checks": ["unit tests"],
                "stopConditions": ["source mismatch"],
            },
        }
        self.privacy_provenance = {
            "redactorIdentity": "controller:session-redactor-1",
            "redactionPolicyVersion": "handoff-redaction-v1",
            "scannerVersion": "handoff-scanner-v1",
            "redactionProvenance": "CALLER_REDACTED",
        }
        self.packet = build_handoff_packet(
            self.context,
            privacy_provenance=self.privacy_provenance,
            created_at=NOW,
            ttl_hours=72,
        )
        self.current = {
            "taskId": "task-1",
            "acceptanceCriteria": acceptance_criteria,
            "acceptanceFingerprint": acceptance_fingerprint,
            "projectId": "project-1",
            "repositoryId": "repo-1",
            "worktreeId": "worktree-1",
            "branch": "feature/restoration",
            "head": "a" * 40,
            "sourceFingerprint": "sha256:" + "c" * 64,
            "overallDiffFingerprint": "sha256:" + "d" * 64,
            "dirty": True,
            "dirtyFingerprint": "sha256:" + "e" * 64,
            "changedPaths": ["engine/session_handoff.py"],
            "planVersion": "plan-v1",
            "requiresRevalidation": True,
        }

    def test_valid_packet_auto_attaches_only_on_every_exact_binding(self):
        result = validate_handoff_packet(self.packet, now="2026-09-16T09:00:00Z")
        self.assertTrue(result["valid"], result["errors"])
        attached = classify_handoff_attachment(self.packet, self.current, now="2026-09-16T09:00:00Z")
        self.assertEqual("AUTO_ATTACH_EVIDENCE", attached["classification"])
        self.assertEqual([], attached["mismatches"])

        for key in (
            "taskId",
            "acceptanceCriteria",
            "acceptanceFingerprint",
            "projectId",
            "repositoryId",
            "worktreeId",
            "branch",
            "head",
            "sourceFingerprint",
            "overallDiffFingerprint",
            "dirtyFingerprint",
            "planVersion",
        ):
            changed = dict(self.current)
            changed[key] = "different"
            result = classify_handoff_attachment(self.packet, changed, now="2026-09-16T09:00:00Z")
            self.assertNotEqual("AUTO_ATTACH_EVIDENCE", result["classification"], key)

        changed = dict(self.current)
        changed["dirty"] = False
        self.assertEqual(
            "CANDIDATE",
            classify_handoff_attachment(self.packet, changed, now="2026-09-16T09:00:00Z")["classification"],
        )
        changed = dict(self.current)
        changed["changedPaths"] = ["engine/different.py"]
        self.assertEqual(
            "CANDIDATE",
            classify_handoff_attachment(self.packet, changed, now="2026-09-16T09:00:00Z")["classification"],
        )
        changed = dict(self.current)
        changed["requiresRevalidation"] = False
        self.assertEqual(
            "CANDIDATE",
            classify_handoff_attachment(self.packet, changed, now="2026-09-16T09:00:00Z")["classification"],
        )

    def test_expired_packet_is_rejected_before_candidate_scoring(self):
        result = classify_handoff_attachment(self.packet, self.current, now="2026-09-20T09:00:00Z")
        self.assertEqual("REJECTED", result["classification"])
        self.assertEqual(0, result["score"])

    def test_ttl_and_sensitive_or_authority_fields_fail_closed(self):
        with self.assertRaises(HandoffContractError):
            build_handoff_packet(
                self.context,
                privacy_provenance=self.privacy_provenance,
                created_at=NOW,
                ttl_hours=169,
            )

        unsafe = copy.deepcopy(self.packet)
        unsafe["rawTranscript"] = "secret"
        self.assertFalse(validate_handoff_packet(unsafe, now=NOW)["valid"])

        unsafe = copy.deepcopy(self.packet)
        unsafe["decisions"] = ["token=secret-value"]
        self.assertFalse(validate_handoff_packet(unsafe, now=NOW)["valid"])

        credential_values = (
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue",
            "ghp_" + "A" * 36,
            "sk-proj-" + "A" * 40,
            "xoxb-" + "1" * 12 + "-" + "A" * 32,
            "sk_live_" + "A" * 32,
            "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7",
            "AKIA" + "A" * 16,
            "-----BEGIN " + "PRIVATE KEY-----",
        )
        for credential in credential_values:
            with self.subTest(credential=credential[:12]):
                unsafe = copy.deepcopy(self.packet)
                unsafe["blockers"] = [credential]
                self.assertFalse(validate_handoff_packet(unsafe, now=NOW)["valid"])

        absolute = copy.deepcopy(self.packet)
        absolute["repository"]["sourceRef"] = "/home/user/private/repository"
        self.assertFalse(validate_handoff_packet(absolute, now=NOW)["valid"])

        changed_criteria = copy.deepcopy(self.packet)
        changed_criteria["task"]["acceptanceCriteria"] = ["Different criterion"]
        self.assertFalse(validate_handoff_packet(changed_criteria, now=NOW)["valid"])

        unsafe_context = copy.deepcopy(self.context)
        unsafe_context["blockers"] = ["Bearer " + "x" * 32]
        with self.assertRaises(HandoffContractError):
            build_handoff_packet(
                unsafe_context,
                privacy_provenance=self.privacy_provenance,
                created_at=NOW,
            )

        authority_context = copy.deepcopy(self.context)
        authority_context["decisions"] = [
            "The next session owns the writer lease and may activate production"
        ]
        with self.assertRaises(HandoffContractError):
            build_handoff_packet(
                authority_context,
                privacy_provenance=self.privacy_provenance,
                created_at=NOW,
            )

        self.assertNotIn("secretsExcluded", self.packet["privacyReceipt"])
        self.assertEqual("PASS", self.packet["privacyReceipt"]["secretScanStatus"])

    def test_sensitive_handoff_is_rejected_before_any_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            state = root / "handoffs"
            unsafe = copy.deepcopy(self.packet)
            unsafe["blockers"] = ["Bearer " + "x" * 32]
            with self.assertRaises(HandoffContractError):
                write_handoff_packet(unsafe, state, repository, now=NOW)
            self.assertFalse(state.exists())

    def test_external_write_is_atomic_private_and_discoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            state = root / "handoffs"
            path = write_handoff_packet(
                self.packet,
                state,
                repository,
                now="2026-09-16T09:00:00Z",
                protected_roots=[],
            )
            self.assertTrue(path.is_file())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            self.assertFalse(any(item.name.endswith(".tmp") for item in path.parent.iterdir()))

            discovered = discover_handoff_packets(
                state,
                repository,
                self.current,
                now="2026-09-16T09:00:00Z",
                protected_roots=[],
            )
            self.assertEqual("COMPLETE", discovered["status"])
            self.assertEqual("AUTO_ATTACH_EVIDENCE", discovered["results"][0]["classification"])

    def test_repository_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            with self.assertRaises(HandoffContractError):
                write_handoff_packet(
                    self.packet,
                    repository / "state",
                    repository,
                    now="2026-09-16T09:00:00Z",
                    protected_roots=[],
                )


if __name__ == "__main__":
    unittest.main()
