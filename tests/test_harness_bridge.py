import copy
import json
import unittest
from pathlib import Path

from engine.harness_bridge import (
    HarnessBridgeError,
    execution_status,
    inspect_manifest,
    project_capability,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PACKAGE_ROOT / "tests" / "fixtures" / "capabilities" / "valid.json"
UNAVAILABLE_FIXTURE = PACKAGE_ROOT / "tests" / "fixtures" / "capabilities" / "unavailable.json"


class HarnessBridgeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(FIXTURE.read_text())

    def test_valid_import_reports_all_four_states(self):
        result = inspect_manifest(self.manifest)
        self.assertEqual(result["status"], "VALID")
        self.assertTrue(result["eligible"])
        self.assertEqual(
            {item["documented"] for item in result["capabilities"]},
            {"DOCUMENTED"},
        )
        self.assertTrue(all(item["installed"] == "INSTALLED" for item in result["capabilities"]))
        self.assertTrue(all(item["runnable"] == "RUNNABLE" for item in result["capabilities"]))
        self.assertTrue(all(item["executed"] == "NOT_EXECUTED" for item in result["capabilities"]))

    def test_unavailable_is_explicit_and_not_eligible(self):
        result = inspect_manifest(json.loads(UNAVAILABLE_FIXTURE.read_text()))
        self.assertEqual(result["status"], "VALID")
        self.assertFalse(result["eligible"])
        capability = result["capabilities"][0]
        self.assertEqual(capability["availability"], "UNAVAILABLE")
        self.assertEqual(capability["installed"], "UNAVAILABLE")
        self.assertEqual(capability["runnable"], "UNAVAILABLE")
        self.assertEqual(capability["executed"], "UNAVAILABLE")
        self.assertFalse(capability["eligible"])

    def test_authority_duplicate_and_stale_inputs_are_rejected(self):
        authority = copy.deepcopy(self.manifest)
        authority["capabilities"][0]["prompt"] = "do something"
        duplicate = copy.deepcopy(self.manifest)
        duplicate["capabilities"].append(copy.deepcopy(duplicate["capabilities"][0]))
        stale = inspect_manifest(
            self.manifest,
            expected_fingerprints={"repository-documentation": "sha256:" + "a" * 64},
        )
        self.assertEqual(inspect_manifest(authority)["status"], "INVALID")
        self.assertEqual(inspect_manifest(duplicate)["status"], "INVALID")
        self.assertEqual(stale["status"], "INVALID")

    def test_task_bound_projection_contains_no_execution_authority(self):
        projection = project_capability(
            self.manifest,
            "lint-architecture",
            task_id="task-123",
            repository="repo-a",
            source_fingerprint="sha256:" + "b" * 64,
            ae_grant=True,
            task_category="verification",
        )
        self.assertEqual(projection["status"], "PROJECTED")
        self.assertEqual(projection["executionStatus"], "NOT_EXECUTED")
        self.assertNotIn("model", projection)
        self.assertNotIn("route", projection)
        self.assertNotIn("command", projection)
        self.assertNotIn("prompt", projection)

    def test_projection_requires_ae_binding_and_eligible_capability(self):
        with self.assertRaises(HarnessBridgeError):
            project_capability(
                self.manifest,
                "lint-architecture",
                task_id="task-123",
                repository="repo-a",
                source_fingerprint="sha256:" + "b" * 64,
            )
        with self.assertRaises(HarnessBridgeError):
            project_capability(
                json.loads(UNAVAILABLE_FIXTURE.read_text()),
                "observability",
                task_id="task-123",
                repository="repo-a",
                source_fingerprint="sha256:" + "b" * 64,
                ae_grant=True,
            )

    def test_execution_is_explicitly_unavailable_and_has_no_runner(self):
        result = execution_status()
        self.assertEqual(result["status"], "NOT_EXECUTED")
        self.assertFalse(result["available"])
        self.assertIn("read-only", result["reason"])


if __name__ == "__main__":
    unittest.main()
