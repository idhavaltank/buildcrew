import json
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from promotion_rehearsal import APPROVAL_TOKEN, _redact, run_rehearsal, sandbox_available


class PromotionRehearsalTests(unittest.TestCase):
    def test_receipt_redaction_handles_structured_secrets(self):
        rendered = _redact('{"token":"do-not-leak", "email":"user@example.com"}')
        self.assertNotIn("do-not-leak", rendered)
        self.assertNotIn("user@example.com", rendered)
        self.assertIn("[REDACTED]", rendered)

    def build_archive(self, directory: Path, *, unsafe_name: str | None = None) -> Path:
        archive = directory / "candidate.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as package:
            package.writestr(unsafe_name or "package/tests/test_package.py", "import unittest\n\nclass PackageTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            if unsafe_name is None:
                package.writestr("package/README.md", "isolated package")
        return archive

    def statuses(self, report):
        return {receipt["stage"]: receipt["status"] for receipt in report["receipts"]}

    def test_path_traversal_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.build_archive(Path(temporary), unsafe_name="../outside.txt")
            report = run_rehearsal(archive, "python3 -m unittest discover")

        self.assertEqual(report["terminalStatus"], "FAILED")
        self.assertEqual(self.statuses(report)["candidate-validation"], "FAIL")
        self.assertEqual(self.statuses(report)["installation"], "NOT_STARTED")

    @unittest.skipUnless(sandbox_available(), "bubblewrap namespace sandbox is unavailable on this host")
    def test_extracts_with_exact_file_parity_and_runs_package_tests(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.build_archive(Path(temporary))
            report = run_rehearsal(
                archive,
                "python3 -B -m unittest discover -s package/tests -p test_*.py",
            )

        receipts = {receipt["stage"]: receipt for receipt in report["receipts"]}
        self.assertEqual(report["terminalStatus"], "REHEARSAL_READY")
        self.assertEqual(receipts["installation"]["status"], "PASS")
        self.assertEqual(receipts["installation"]["evidence"]["facts"]["parity"], "exact-sha256")
        self.assertEqual(receipts["installation"]["evidence"]["commands"][0]["result"], "passed")
        self.assertNotIn(temporary, json.dumps(report))

    @unittest.skipUnless(sandbox_available(), "bubblewrap namespace sandbox is unavailable on this host")
    def test_activation_is_not_implicit_and_incomplete_gate_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.build_archive(Path(temporary))
            command = "python3 -B -m unittest discover -s package/tests -p test_*.py"
            default_report = run_rehearsal(archive, command)
            blocked_report = run_rehearsal(archive, command, activate=True, rehearsal=True)

        self.assertEqual(self.statuses(default_report)["activation"], "NOT_STARTED")
        self.assertEqual(default_report["terminalStatus"], "REHEARSAL_READY")
        self.assertEqual(self.statuses(blocked_report)["activation"], "BLOCKED")
        self.assertEqual(blocked_report["terminalStatus"], "ACTIVATION_BLOCKED")

    @unittest.skipUnless(sandbox_available(), "bubblewrap namespace sandbox is unavailable on this host")
    def test_approved_activation_rehearsal_rolls_back_prior_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.build_archive(Path(temporary))
            report = run_rehearsal(
                archive,
                "python3 -B -m unittest discover -s package/tests -p test_*.py",
                activate=True,
                rehearsal=True,
                approval_token=APPROVAL_TOKEN,
                reviewer="Recovery Engineer",
                host_check="PASS",
                canary_seconds=30,
                rollback_trigger="host-mismatch-or-test-failure",
            )

        receipts = {receipt["stage"]: receipt for receipt in report["receipts"]}
        self.assertEqual(report["terminalStatus"], "REHEARSED_AND_ROLLED_BACK")
        self.assertEqual(receipts["activation"]["status"], "PASS")
        self.assertEqual(receipts["activation"]["evidence"]["facts"]["canarySeconds"], 30)
        self.assertEqual(
            receipts["activation"]["evidence"]["facts"]["rollbackTrigger"],
            "host-mismatch-or-test-failure",
        )
        self.assertEqual(receipts["rollback"]["status"], "PASS")
        self.assertEqual(receipts["rollback"]["evidence"]["facts"]["priorSelection"], "buildcrew")
        self.assertEqual(receipts["rollback"]["evidence"]["facts"]["restoredSelection"], "buildcrew")

    def test_rehearsal_never_changes_an_unrelated_live_configuration_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.build_archive(root)
            live_config = root / "live-host-config.json"
            live_config.write_text('{"selectedSkill":"live-skill"}', encoding="utf-8")
            before = live_config.read_text(encoding="utf-8")
            report = run_rehearsal(
                archive,
                "python3 -B -m unittest discover -s package/tests -p test_*.py",
                activate=True,
                rehearsal=True,
                approval_token=APPROVAL_TOKEN,
                reviewer="Recovery Engineer",
                host_check="PASS",
            )

            self.assertEqual(live_config.read_text(encoding="utf-8"), before)
            self.assertEqual(report["mode"], "temporary-rehearsal")
            rendered = json.dumps(report)
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("live-host-config.json", rendered)


if __name__ == "__main__":
    unittest.main()
