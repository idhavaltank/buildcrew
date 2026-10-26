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

from engine.context_shadow import bind_project_context_shadow, validate_shadow_binding  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402
from engine.project_context import discover_project_context  # noqa: E402


class ContextShadowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")
        result = discover_project_context(self.root, targets=["."], task_type="NEW_TASK", acceptance_criteria=["Search works"])
        self.context = result["contextMap"]
        self.receipt = result["receipt"]
        self.policy = resolve_policy(
            {"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": ["Search works"]},
            self.context,
        )
        self.intake = {
            "taskId": "task-shadow-1",
            "repositoryId": self.context["repository"]["repositoryId"],
            "sourceFingerprint": self.context["fingerprints"]["sourceFingerprint"],
            "contextFingerprint": self.context["fingerprints"]["contextFingerprint"],
            "taskScopeFingerprint": self.context["fingerprints"]["taskScopeFingerprint"],
            "requestType": "NEW_TASK",
            "normalizedObjective": "Validate search",
            "targetPaths": ["."],
            "acceptanceCriteria": ["Search works"],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_sidecars_bind_in_shadow_only_mode(self):
        result = bind_project_context_shadow(self.intake, self.context, self.receipt, self.policy)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["mode"], "SHADOW")
        self.assertEqual(result["controllerGate"]["enforcement"], "SHADOW_ONLY")
        self.assertEqual(result["controllerGate"]["routeMutation"], "NOT_APPLIED")
        self.assertEqual(result["sideEffects"], [])
        self.assertTrue(validate_shadow_binding(result)["valid"])

    def test_changed_identity_is_visible_and_does_not_enforce(self):
        invalid = copy.deepcopy(self.intake)
        invalid["sourceFingerprint"] = "b" * 64
        result = bind_project_context_shadow(invalid, self.context, self.receipt, self.policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("sourceFingerprint" in blocker for blocker in result["blockers"]))
        self.assertEqual(result["controllerGate"]["agentSelection"], "NOT_APPLIED")

    def test_incomplete_context_is_reported_as_missing_coverage(self):
        incomplete = copy.deepcopy(self.context)
        incomplete["status"] = "INCOMPLETE"
        incomplete["gaps"] = [{"gapId": "missing-runtime", "category": "runtime", "expected": "runtime", "blocking": True, "reason": "runtime unavailable"}]
        result = bind_project_context_shadow(self.intake, incomplete, self.receipt, self.policy)
        self.assertEqual(result["status"], "MISSING_COVERAGE")
        self.assertTrue(any("context status is INCOMPLETE" in blocker for blocker in result["blockers"]))

    def test_conflict_is_blocking_but_side_effect_free(self):
        conflict = copy.deepcopy(self.context)
        conflict["status"] = "CONFLICT"
        conflict["conflicts"] = [{"conflictId": "controller", "sourcePaths": ["AGENTS.md"], "subject": "authority", "blocking": True, "reason": "conflict"}]
        result = bind_project_context_shadow(self.intake, conflict, self.receipt, self.policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["sideEffects"], [])

    def test_cli_produces_valid_shadow_report(self):
        result = discover_project_context(self.root, targets=["."], task_type="NEW_TASK", acceptance_criteria=["Search works"])
        paths = {}
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            for name, value in {
                "intake.json": self.intake, "context.json": result["contextMap"], "receipt.json": result["receipt"], "policy.json": self.policy,
            }.items():
                path = directory_path / name
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[name] = path
            completed = subprocess.run(
                [sys.executable, "-B", "scripts/bind_project_context_shadow.py", "--intake", str(paths["intake.json"]),
                 "--context-map", str(paths["context.json"]), "--receipt", str(paths["receipt.json"]), "--policy", str(paths["policy.json"])],
                cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertTrue(output["validation"]["valid"], output["validation"]["errors"])


if __name__ == "__main__":
    unittest.main()
