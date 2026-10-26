from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_receipts import validate_policy_bundle  # noqa: E402
from engine.policy_catalog import explain_rule  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402
from engine.project_context import discover_project_context  # noqa: E402


class PolicyResolutionTests(unittest.TestCase):
    def test_flutter_application_policy_selects_shared_and_mobile_rules(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "lib" / "data").mkdir(parents=True)
        (root / "pubspec.yaml").write_text("name: catalog\n", encoding="utf-8")
        (root / "android").mkdir()
        manifest_dir = root / "docs" / "agent-context"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "project-context.json").write_text(json.dumps({
            "schemaVersion": "project-context-manifest-v1",
            "projectId": "sample-mobile",
            "profiles": ["shared_application", "flutter", "mobile"],
        }), encoding="utf-8")
        try:
            discovered = discover_project_context(root, targets=["lib/data"], task_type="NEW_TASK")
            policy = resolve_policy(
                {"requestType": "NEW_TASK", "targetPaths": ["lib/data"], "acceptanceCriteria": ["Add offline favorites"]},
                discovered["contextMap"],
                manifest=discovered["manifest"],
                manifest_path=discovered["manifestPath"],
            )
        finally:
            temporary.cleanup()
        self.assertTrue({"shared_application", "mobile", "flutter"}.issubset(policy["selectedProfiles"]))
        rule_ids = {rule["ruleId"] for rule in policy["rules"]}
        self.assertIn("MOB-LOCAL-001", rule_ids)
        self.assertIn("APP-AUTH-001", rule_ids)
        self.assertEqual(policy["riskTier"], "coupled")
        self.assertEqual(policy["mutationStatus"], "READY")
        self.assertTrue(validate_policy_bundle(policy)["valid"])

    def test_media_rules_are_not_selected_without_media_profile(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")
        try:
            discovered = discover_project_context(root)
            policy = resolve_policy({"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []}, discovered["contextMap"])
        finally:
            temporary.cleanup()
        self.assertNotIn("media", policy["selectedProfiles"])
        self.assertFalse(any(rule["ruleId"].startswith("MEDIA-") for rule in policy["rules"]))

    def test_blocking_context_conflict_blocks_mutation(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        try:
            discovered = discover_project_context(root)
            discovered["contextMap"]["conflicts"] = [{
                "conflictId": "test-conflict",
                "sourcePaths": ["AGENTS.md", "docs/workflow.md"],
                "subject": "lifecycle controller",
                "blocking": True,
                "reason": "conflicting authority",
            }]
            policy = resolve_policy({"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []}, discovered["contextMap"])
        finally:
            temporary.cleanup()
        self.assertEqual(policy["mutationStatus"], "BLOCKED")
        self.assertTrue(any(item["conflictId"] == "test-conflict" for item in policy["conflicts"]))

    def test_low_confidence_product_signal_does_not_enable_shared_application(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "README.md").write_text("This mentions a product as historical context.\n", encoding="utf-8")
        try:
            discovered = discover_project_context(root)
            policy = resolve_policy({"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []}, discovered["contextMap"])
        finally:
            temporary.cleanup()
        self.assertNotIn("shared_application", policy["selectedProfiles"])

    def test_rule_explainer_returns_source_and_evidence_contract(self):
        rule = explain_rule("V3-CONTEXT-001")
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(rule["source"], "v3_builtin")
        self.assertIn("current context-discovery receipt", rule["requiredEvidence"])
        self.assertIsNone(explain_rule("not-a-real-rule"))

    def test_bounded_reuse_gate_is_common_to_every_actionable_profile(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        try:
            discovered = discover_project_context(root)
            for profiles in ((), ("web",), ("backend",), ("mobile", "flutter")):
                with self.subTest(profiles=profiles):
                    policy = resolve_policy(
                        {"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []},
                        discovered["contextMap"],
                        explicit_profiles=profiles,
                    )
                    rule = next(item for item in policy["rules"] if item["ruleId"] == "V3-REUSE-001")
                    self.assertEqual(rule["scope"], "COMMON")
                    self.assertEqual(rule["enforcement"], "BLOCK")
                    self.assertIn("repository-first reuse assessment", rule["requiredEvidence"])
                    self.assertIn("actionable code or source mutations", rule["condition"])
                    self.assertIn("bounded reuse discovery", policy["requiredCapabilities"])
        finally:
            temporary.cleanup()

    def test_explicit_normative_agents_line_becomes_scoped_project_rule(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "apps").mkdir()
        (root / "apps" / "AGENTS.md").write_text(
            "All schema changes MUST use an additive migration and migration test.\n"
            "The team usually runs the full suite for this.\n",
            encoding="utf-8",
        )
        try:
            discovered = discover_project_context(root, targets=["apps"])
            policy = resolve_policy(
                {"requestType": "NEW_TASK", "targetPaths": ["apps"], "acceptanceCriteria": []},
                discovered["contextMap"],
                repository_root=discovered["root"],
            )
        finally:
            temporary.cleanup()
        project_rules = [rule for rule in policy["rules"] if rule["source"] == "repository_instruction"]
        self.assertEqual(len(project_rules), 1)
        self.assertIn("additive migration", project_rules[0]["requirement"])
        self.assertEqual(project_rules[0]["sourceEvidence"], "apps/AGENTS.md:1")

    def test_non_current_context_status_never_allows_ready_mutation(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        try:
            discovered = discover_project_context(root)
            intake = {"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []}
            for status in ("UNSEEN", "DISCOVERING", "STALE", "CONFLICT", "CORRUPT"):
                context = deepcopy(discovered["contextMap"])
                context["status"] = status
                self.assertEqual(resolve_policy(intake, context)["mutationStatus"], "BLOCKED", status)
            for status in ("INCOMPLETE", "UNAVAILABLE"):
                context = deepcopy(discovered["contextMap"])
                context["status"] = status
                self.assertEqual(resolve_policy(intake, context)["mutationStatus"], "REVIEW_REQUIRED", status)
        finally:
            temporary.cleanup()

    def test_external_authoritative_source_path_cannot_inject_policy(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "AGENTS.md").write_text(
            "The repository MUST use the approved migration path.\n",
            encoding="utf-8",
        )
        try:
            discovered = discover_project_context(root)
            context = deepcopy(discovered["contextMap"])
            source = next(item for item in context["sources"] if item["path"] == "AGENTS.md")
            source["path"] = "/tmp/attacker/AGENTS.md"
            policy = resolve_policy(
                {"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": []},
                context,
                repository_root=root.as_posix(),
            )
        finally:
            temporary.cleanup()
        self.assertFalse(any(rule["source"] == "repository_instruction" for rule in policy["rules"]))


if __name__ == "__main__":
    unittest.main()
