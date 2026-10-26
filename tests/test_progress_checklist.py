import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from progress_checklist import (
    PHASES,
    complete_progress,
    main,
    render_progress,
    show_progress,
    start_progress,
    update_lane,
    update_progress,
)


DISPLAY_PHASES = [
    ("understand", "Understand the requested progress experience"),
    ("inspect", "Inspect the existing progress engine"),
    ("implement", "Implement the friendly progress flow"),
    ("verify", "Verify behavior and compatibility"),
]


class ProgressChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "progress.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def start_display_task(self, task_id="task-display", **kwargs):
        return start_progress(
            task_id,
            state_file=self.state_file,
            display_phases=DISPLAY_PHASES,
            now="2026-09-18T12:00:00Z",
            emit=False,
            **kwargs,
        )

    def test_start_renders_task_derived_phases_with_friendly_markers(self):
        state = self.start_display_task()
        output = io.StringIO()
        with redirect_stdout(output):
            render_progress(state, color=True)
        rendered = output.getvalue()
        self.assertIn("AE Progress — 0/4 complete", rendered)
        self.assertIn("Current: Understand the requested progress experience", rendered)
        self.assertIn("🟨 Understand the requested progress experience", rendered)
        self.assertIn("⬜ Inspect the existing progress engine", rendered)
        self.assertNotIn("Intake", rendered)
        self.assertNotIn("NOT_STARTED", rendered)
        self.assertNotIn("\x1b[9", rendered)

    def test_plain_mode_uses_semantic_ascii_fallback_without_ansi(self):
        state = self.start_display_task("task-plain")
        output = io.StringIO()
        with redirect_stdout(output):
            render_progress(state, color=False)
        rendered = output.getvalue()
        self.assertIn("[NOW] Understand the requested progress experience", rendered)
        self.assertIn("[ ] Inspect the existing progress engine", rendered)
        self.assertNotIn("🟨", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_meaningful_transition_prints_concise_diff(self):
        self.start_display_task("task-diff")
        output = io.StringIO()
        with redirect_stdout(output):
            update_progress(
                "task-diff",
                "intake",
                "PASS",
                detail="Requirement packet accepted",
                display_phase_id="understand",
                next_display_phase_id="inspect",
                evidence="request and examples confirmed",
                evidence_tier="source",
                state_file=self.state_file,
                now="2026-09-18T12:01:00Z",
                color=True,
            )
        rendered = output.getvalue()
        self.assertIn("- 🟨 Understand the requested progress experience", rendered)
        self.assertIn("+ 🟩 Understand the requested progress experience — request and examples confirmed", rendered)
        self.assertIn("+ 🟨 Inspect the existing progress engine", rendered)
        self.assertNotIn("AE Progress —", rendered)

    def test_periodic_snapshot_keeps_transition_diff_and_restores_full_state(self):
        self.start_display_task("task-periodic", full_every=2)
        update_progress(
            "task-periodic",
            "intake",
            "PASS",
            detail="Request confirmed",
            display_phase_id="understand",
            next_display_phase_id="inspect",
            evidence="request confirmed",
            evidence_tier="source",
            state_file=self.state_file,
            emit=False,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            update_progress(
                "task-periodic",
                "context",
                "PASS",
                detail="Source inspected",
                display_phase_id="inspect",
                next_display_phase_id="implement",
                evidence="source and tests inspected",
                evidence_tier="test",
                state_file=self.state_file,
                color=True,
            )
        rendered = output.getvalue()
        self.assertIn("- 🟨 Inspect the existing progress engine", rendered)
        self.assertIn("AE Progress — 2/4 complete", rendered)
        self.assertIn("🟨 Implement the friendly progress flow", rendered)

    def test_display_completion_requires_evidence(self):
        self.start_display_task("task-evidence")
        with self.assertRaisesRegex(ValueError, "PASS requires evidence"):
            update_progress(
                "task-evidence",
                "intake",
                "PASS",
                display_phase_id="understand",
                state_file=self.state_file,
                emit=False,
            )

    def test_display_completion_requires_an_explicit_evidence_tier(self):
        self.start_display_task("task-evidence-tier")
        with self.assertRaisesRegex(ValueError, "evidence tier"):
            update_progress(
                "task-evidence-tier",
                "intake",
                "PASS",
                display_phase_id="understand",
                evidence="request confirmed",
                state_file=self.state_file,
                emit=False,
            )

    def test_blocked_transition_requires_and_renders_complete_blocker_context(self):
        self.start_display_task("task-blocked")
        with self.assertRaisesRegex(ValueError, "checked"):
            update_progress(
                "task-blocked",
                "context",
                "BLOCKED",
                display_phase_id="understand",
                blocker="Required host contract is unavailable",
                next_action="Restore the contract, then retry",
                state_file=self.state_file,
                emit=False,
            )

        output = io.StringIO()
        with redirect_stdout(output):
            result = update_progress(
                "task-blocked",
                "context",
                "BLOCKED",
                display_phase_id="understand",
                blocker="Required host contract is unavailable",
                checked=["Active skill path", "Workspace loader"],
                next_action="Restore the contract, then retry",
                state_file=self.state_file,
                color=True,
            )
        rendered = output.getvalue()
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("🟥 Understand the requested progress experience", rendered)
        self.assertIn("Blocker: Required host contract is unavailable", rendered)
        self.assertIn("Checked: Active skill path; Workspace loader", rendered)
        self.assertIn("Next: Restore the contract, then retry", rendered)
        self.assertIn("AE Progress —", rendered)

    def test_show_restores_the_full_durable_projection(self):
        self.start_display_task("task-recovery")
        update_progress(
            "task-recovery",
            "intake",
            "PASS",
            detail="Request confirmed",
            display_phase_id="understand",
            next_display_phase_id="inspect",
            evidence="request confirmed",
            evidence_tier="source",
            state_file=self.state_file,
            emit=False,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            recovered = show_progress("task-recovery", state_file=self.state_file, color=True)
        self.assertEqual("progress-checklist-v2", recovered["schemaVersion"])
        self.assertIn("AE Progress — 1/4 complete", output.getvalue())
        self.assertIn("🟩 Understand the requested progress experience — request confirmed", output.getvalue())
        self.assertIn("🟨 Inspect the existing progress engine", output.getvalue())

    def test_v1_state_is_migrated_without_losing_phase_history(self):
        phases = [
            {
                "id": phase_id,
                "label": label,
                "status": "PASS" if phase_id == "intake" else "NOT_STARTED",
                "detail": "Legacy evidence" if phase_id == "intake" else None,
                "lastCommand": None,
            }
            for phase_id, label in PHASES
        ]
        self.state_file.write_text(
            json.dumps(
                {
                    "schemaVersion": "progress-checklist-v1",
                    "taskId": "task-v1",
                    "status": "IN_PROGRESS",
                    "createdAt": "2026-09-11T12:00:00Z",
                    "updatedAt": "2026-09-11T12:01:00Z",
                    "phases": phases,
                    "events": [{"phase": "intake", "status": "PASS", "at": "2026-09-11T12:01:00Z"}],
                }
            ),
            encoding="utf-8",
        )
        migrated = start_progress("task-v1", state_file=self.state_file, emit=False)
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual("progress-checklist-v2", migrated["schemaVersion"])
        self.assertEqual("progress-checklist-v2", persisted["schemaVersion"])
        self.assertEqual("PASS", migrated["phases"][0]["status"])
        self.assertEqual("Legacy evidence", migrated["phases"][0]["detail"])
        self.assertTrue(migrated["phases"][0]["legacyEvidenceUnverified"])
        self.assertEqual([], migrated["displayPhases"])
        self.assertEqual([], migrated["lanes"])
        self.assertTrue(self.state_file.with_suffix(".json.v1-backup").is_file())
        self.assertEqual(0o600, stat.S_IMODE(self.state_file.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.state_file.with_suffix(".json.v1-backup").stat().st_mode))
        historical = complete_progress("task-v1", state_file=self.state_file, emit=False)
        self.assertEqual("BLOCKED", historical["status"])
        self.assertIn("Legacy evidence unverified: Intake", historical["blockers"])

    def test_parallel_lane_requires_assignment_contract_and_appears_in_projection(self):
        self.start_display_task("task-lane")
        with self.assertRaisesRegex(ValueError, "purpose"):
            update_lane(
                "task-lane",
                "reviewer",
                "IN_PROGRESS",
                label="Independent Reviewer — compatibility reviewer",
                state_file=self.state_file,
                emit=False,
            )

        output = io.StringIO()
        with redirect_stdout(output):
            state = update_lane(
                "task-lane",
                "reviewer",
                "IN_PROGRESS",
                label="Independent Reviewer — compatibility reviewer",
                purpose="Review migration and host compatibility",
                boundaries=["Read-only progress helper and loader"],
                deliverable="Source-backed acceptance gaps",
                verification="Report exact file references",
                state_file=self.state_file,
                color=True,
                full=True,
            )
        rendered = output.getvalue()
        self.assertEqual("Independent Reviewer — compatibility reviewer", state["lanes"][0]["label"])
        self.assertTrue(state["lanes"][0]["preserveUnrelatedChanges"])
        self.assertIn("+ 🟨 Independent Reviewer — compatibility reviewer", rendered)
        self.assertIn("Parallel work", rendered)

    def test_completion_is_blocked_by_pending_display_phase_or_active_lane(self):
        self.start_display_task("task-close")
        for phase_id, _ in PHASES:
            update_progress(
                "task-close",
                phase_id,
                "PASS",
                detail=f"{phase_id} evidence",
                evidence_tier="source",
                state_file=self.state_file,
                emit=False,
            )
        blocked = complete_progress("task-close", state_file=self.state_file, emit=False)
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertTrue(any(item.startswith("Task phase:") for item in blocked["blockers"]))

        for index, (display_id, _) in enumerate(DISPLAY_PHASES):
            update_progress(
                "task-close",
                "closure",
                "PASS",
                detail="Canonical closure evidence",
                display_phase_id=display_id,
                next_display_phase_id=DISPLAY_PHASES[index + 1][0] if index + 1 < len(DISPLAY_PHASES) else None,
                evidence=f"{display_id} verified",
                evidence_tier="test",
                state_file=self.state_file,
                emit=False,
            )
        update_lane(
            "task-close",
            "reviewer",
            "IN_PROGRESS",
            label="Independent Reviewer — reviewer",
            purpose="Independent verification",
            boundaries=["Read-only changed files"],
            deliverable="Review verdict",
            verification="Focused test evidence",
            state_file=self.state_file,
            emit=False,
        )
        blocked_by_lane = complete_progress("task-close", state_file=self.state_file, emit=False)
        self.assertEqual("BLOCKED", blocked_by_lane["status"])
        self.assertIn("Parallel lane: Independent Reviewer — reviewer", blocked_by_lane["blockers"])

        update_lane(
            "task-close",
            "reviewer",
            "PASS",
            evidence="review passed with focused tests",
            evidence_tier="review",
            state_file=self.state_file,
            emit=False,
        )
        completed = complete_progress("task-close", state_file=self.state_file, emit=False)
        self.assertEqual("CLOSED", completed["status"])

    def test_sensitive_transition_details_are_redacted_in_state_and_output(self):
        self.start_display_task("task-redaction")
        output = io.StringIO()
        authorization_header = "Author" + "ization: Bearer bearer-secret-value "
        api_key_line = "OPENAI_API_" + "KEY=environment-secret "
        json_token = '{"to' + 'ken":"json-secret"} '
        private_key = (
            "-----BEGIN PRIVATE " + "KEY----- private-key-secret "
            "-----END PRIVATE " + "KEY-----"
        )
        with redirect_stdout(output):
            result = update_progress(
                "task-redaction",
                "intake",
                "PASS",
                detail=(
                    authorization_header
                    + api_key_line
                    + json_token
                    + "https://person:url-password@example.test "
                    + private_key
                ),
                display_phase_id="understand",
                next_display_phase_id="inspect",
                evidence="api_" + "key=evidence-secret",
                evidence_tier="source",
                state_file=self.state_file,
                color=True,
            )
        persisted = self.state_file.read_text(encoding="utf-8")
        for secret in (
            "bearer-secret-value",
            "environment-secret",
            "json-secret",
            "url-password",
            "private-key-secret",
            "evidence-secret",
        ):
            self.assertNotIn(secret, persisted)
            self.assertNotIn(secret, output.getvalue())
        self.assertIn("[REDACTED]", result["displayPhases"][0]["evidence"])

    def test_task_identity_mismatch_fails_before_migrating_or_updating(self):
        self.start_display_task("task-owner")
        original = self.state_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "belongs to task task-owner"):
            start_progress("task-other", state_file=self.state_file, emit=False)
        self.assertEqual(original, self.state_file.read_bytes())

    def test_required_blocker_remains_global_after_unrelated_progress(self):
        self.start_display_task("task-global-block")
        update_progress(
            "task-global-block",
            "context",
            "BLOCKED",
            display_phase_id="understand",
            blocker="Host contract unavailable",
            checked=["Loader source"],
            next_action="Restore the loader contract",
            state_file=self.state_file,
            emit=False,
        )
        result = update_progress(
            "task-global-block",
            "knowledge",
            "PASS",
            detail="Knowledge source verified",
            evidence_tier="source",
            state_file=self.state_file,
            emit=False,
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("Understand the requested progress experience", result["progress"]["blockers"])

    def test_no_op_update_keeps_revision_and_stale_revision_fails_closed(self):
        self.start_display_task("task-revision")
        first = update_progress(
            "task-revision",
            "intake",
            "PASS",
            detail="Request confirmed",
            display_phase_id="understand",
            next_display_phase_id="inspect",
            evidence="request confirmed",
            evidence_tier="source",
            expected_revision=0,
            state_file=self.state_file,
            emit=False,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            repeated = update_progress(
                "task-revision",
                "intake",
                "PASS",
                detail="Request confirmed",
                display_phase_id="understand",
                next_display_phase_id="inspect",
                evidence="request confirmed",
                evidence_tier="source",
                expected_revision=first["revision"],
                state_file=self.state_file,
            )
        self.assertEqual(first["revision"], repeated["revision"])
        self.assertEqual("", output.getvalue())
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            update_progress(
                "task-revision",
                "context",
                "IN_PROGRESS",
                expected_revision=0,
                state_file=self.state_file,
                emit=False,
            )

    def test_cli_supports_task_phases_lanes_and_quiet_persistence(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "start",
                    "--task-id",
                    "task-cli",
                    "--state-file",
                    str(self.state_file),
                    "--display-phase",
                    "inspect=Inspect the current behavior",
                    "--display-phase",
                    "verify=Verify the result",
                    "--full-every",
                    "2",
                    "--quiet",
                ]
            )
        self.assertEqual(0, status)
        self.assertEqual("", output.getvalue())
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual("Inspect the current behavior", persisted["displayPhases"][0]["label"])
        self.assertEqual(2, persisted["settings"]["fullEvery"])

        lane_status = main(
            [
                "lane",
                "--task-id",
                "task-cli",
                "--state-file",
                str(self.state_file),
                "--lane-id",
                "reviewer",
                "--status",
                "IN_PROGRESS",
                "--label",
                "Independent Reviewer — reviewer",
                "--purpose",
                "Review compatibility",
                "--boundary",
                "Read-only progress files",
                "--deliverable",
                "Review verdict",
                "--verification",
                "Focused test output",
                "--quiet",
            ]
        )
        self.assertEqual(0, lane_status)
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual("Independent Reviewer — reviewer", persisted["lanes"][0]["label"])

    def test_versioned_schema_declares_runtime_identity_and_lane_cap(self):
        schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "progress-checklist-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "progress-checklist-v2",
            schema["properties"]["schemaVersion"]["const"],
        )
        self.assertIn("planFingerprint", schema["required"])
        self.assertIn("revision", schema["required"])
        self.assertEqual(4, schema["properties"]["lanes"]["maxItems"])

    def test_real_cli_flow_fails_closed_then_closes_with_evidence(self):
        script = SCRIPT_ROOT / "progress_checklist.py"

        def run(*arguments):
            return subprocess.run(
                [sys.executable, "-B", str(script), *arguments],
                text=True,
                capture_output=True,
                check=False,
            )

        started = run(
            "start",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--display-phase",
            "build=Build the feature",
            "--display-phase",
            "verify=Verify the feature",
            "--color",
        )
        self.assertEqual(0, started.returncode, started.stderr)
        self.assertIn("🟨 Build the feature", started.stdout)

        missing_tier = run(
            "update",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--phase",
            "intake",
            "--status",
            "PASS",
            "--display-phase",
            "build",
            "--evidence",
            "request confirmed",
            "--color",
        )
        self.assertEqual(2, missing_tier.returncode)
        self.assertIn("PASS requires an evidence tier", missing_tier.stdout)

        built = run(
            "update",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--phase",
            "intake",
            "--status",
            "PASS",
            "--display-phase",
            "build",
            "--next-display-phase",
            "verify",
            "--evidence",
            "source change completed",
            "--evidence-tier",
            "source",
            "--color",
        )
        self.assertEqual(0, built.returncode, built.stderr)
        self.assertIn("+ 🟨 Verify the feature", built.stdout)

        lane_started = run(
            "lane",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--lane-id",
            "reviewer",
            "--status",
            "IN_PROGRESS",
            "--label",
            "Independent Reviewer — reviewer",
            "--purpose",
            "Review compatibility",
            "--boundary",
            "Read-only progress files",
            "--deliverable",
            "Review verdict",
            "--verification",
            "Focused test output",
            "--quiet",
        )
        self.assertEqual(0, lane_started.returncode, lane_started.stderr)
        premature = run(
            "complete",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--quiet",
        )
        self.assertEqual(2, premature.returncode)

        for phase_id, _ in PHASES:
            if phase_id == "intake":
                continue
            completed = run(
                "update",
                "--task-id",
                "task-e2e",
                "--state-file",
                str(self.state_file),
                "--phase",
                phase_id,
                "--status",
                "PASS",
                "--detail",
                f"{phase_id} verified",
                "--evidence-tier",
                "test",
                "--quiet",
            )
            self.assertEqual(0, completed.returncode, completed.stdout)

        verified = run(
            "update",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--phase",
            "closure",
            "--status",
            "PASS",
            "--display-phase",
            "verify",
            "--evidence",
            "focused and full suites passed",
            "--evidence-tier",
            "test",
            "--quiet",
        )
        self.assertEqual(0, verified.returncode, verified.stdout)
        lane_completed = run(
            "lane",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--lane-id",
            "reviewer",
            "--status",
            "PASS",
            "--evidence",
            "independent review accepted",
            "--evidence-tier",
            "review",
            "--quiet",
        )
        self.assertEqual(0, lane_completed.returncode, lane_completed.stdout)
        closed = run(
            "complete",
            "--task-id",
            "task-e2e",
            "--state-file",
            str(self.state_file),
            "--color",
        )
        self.assertEqual(0, closed.returncode, closed.stdout)
        self.assertIn("· Closed", closed.stdout)
        self.assertIn("Parallel work", closed.stdout)
        self.assertEqual(
            "CLOSED",
            json.loads(self.state_file.read_text(encoding="utf-8"))["status"],
        )


if __name__ == "__main__":
    unittest.main()
