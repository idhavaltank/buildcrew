from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_receipts import validate_context_map, validate_manifest, validate_policy_bundle, validate_receipt  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402
from engine.project_context import discover_project_context  # noqa: E402


class ContextContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")
        result = discover_project_context(self.root)
        self.context = result["contextMap"]
        self.receipt = result["receipt"]
        self.policy = resolve_policy({"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []}, self.context)

    def tearDown(self):
        self.temporary.cleanup()

    def test_generated_contracts_validate(self):
        self.assertTrue(validate_context_map(self.context)["valid"])
        self.assertTrue(validate_receipt(self.receipt)["valid"])
        self.assertTrue(validate_policy_bundle(self.policy)["valid"])

    def test_unknown_fields_are_rejected(self):
        for value, validator in (
            (self.context, validate_context_map),
            (self.receipt, validate_receipt),
            (self.policy, validate_policy_bundle),
        ):
            invalid = copy.deepcopy(value)
            invalid["unexpected"] = True
            result = validator(invalid)
            self.assertFalse(result["valid"])
            self.assertTrue(any("unknown field unexpected" in error for error in result["errors"]))

    def test_manifest_schema_rejects_unknown_fields(self):
        manifest = {"schemaVersion": "project-context-manifest-v1", "projectId": "x", "profiles": [], "unexpected": True}
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown field unexpected" in error for error in result["errors"]))

    def test_generated_paths_must_remain_repository_relative(self):
        invalid_context = copy.deepcopy(self.context)
        invalid_context["sources"][0]["path"] = "/tmp/external/AGENTS.md"
        invalid_context["commandSources"] = ["../outside.json"]
        invalid_context["conflicts"] = [{
            "conflictId": "path-test",
            "sourcePaths": ["C:\\outside\\AGENTS.md"],
            "subject": "test",
            "blocking": True,
            "reason": "test",
        }]
        result = validate_context_map(invalid_context)
        self.assertFalse(result["valid"])
        self.assertTrue(any("repository-relative path" in error for error in result["errors"]))

        invalid_receipt = copy.deepcopy(self.receipt)
        invalid_receipt["inspectedSources"][0]["path"] = "/tmp/external/package.json"
        invalid_receipt["selectedSources"] = ["../outside.json"]
        result = validate_receipt(invalid_receipt)
        self.assertFalse(result["valid"])
        self.assertTrue(any("repository-relative path" in error for error in result["errors"]))

        invalid_manifest = {
            "schemaVersion": "project-context-manifest-v1",
            "projectId": "x",
            "profiles": [],
            "instructionSources": [{"path": "/tmp/AGENTS.md", "authority": "authoritative"}],
        }
        result = validate_manifest(invalid_manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("repository-relative path" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
