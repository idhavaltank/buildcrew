from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_state import load_context, persist_context, validate_state_root  # noqa: E402
from engine.context_receipts import validate_manifest  # noqa: E402
from engine.project_context import ContextDiscoveryError, discover_project_context  # noqa: E402


class ProjectContextDiscoveryTests(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "apps" / "web" / "src").mkdir(parents=True)
        (root / "apps" / "web" / "AGENTS.md").write_text(
            "Use the approved client boundary for this feature.\n", encoding="utf-8"
        )
        (root / "AGENTS.md").write_text("One controller owns lifecycle.\n", encoding="utf-8")
        (root / "README.md").write_text("A web application.\n", encoding="utf-8")
        (root / "package.json").write_text(
            json.dumps({"dependencies": {"react": "18.0.0", "vite": "5.0.0"}}),
            encoding="utf-8",
        )
        (root / ".env").write_text("API_KEY=do-not-read-this-value\n", encoding="utf-8")
        (root / ".codex").mkdir()
        (root / ".codex" / "runtime.md").write_text("private runtime state", encoding="utf-8")
        return temporary, root

    def test_discovery_is_bounded_scoped_and_platform_aware(self):
        temporary, root = self._repo()
        with temporary:
            result = discover_project_context(root, targets=["apps/web/src"], task_type="NEW_TASK")
        context = result["contextMap"]
        paths = {item["path"] for item in context["sources"]}
        self.assertIn("AGENTS.md", paths)
        self.assertIn("apps/web/AGENTS.md", paths)
        self.assertIn("package.json", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn(".codex/runtime.md", paths)
        self.assertEqual(context["status"], "CURRENT")
        self.assertIn("web", {item["platform"] for item in context["platformSignals"]})
        self.assertIn("apps/web/AGENTS.md", result["receipt"]["selectedSources"])

    def test_fast_path_reads_target_and_nearest_context_without_scanning_siblings(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        target = root / "apps" / "mobile" / "src" / "profile"
        target.mkdir(parents=True)
        (root / "AGENTS.md").write_text("Keep mobile changes minimal.\n", encoding="utf-8")
        (root / "package.json").write_text("{}", encoding="utf-8")
        (root / "apps" / "mobile" / "AGENTS.md").write_text("Use the mobile state boundary.\n", encoding="utf-8")
        (target / "screen.tsx").write_text("export const Screen = () => null;\n", encoding="utf-8")
        (root / "unrelated" / "docs").mkdir(parents=True)
        (root / "unrelated" / "AGENTS.md").write_text("Unrelated instructions.\n", encoding="utf-8")
        (root / "unrelated" / "docs" / "large-plan.md").write_text("Unrelated plan.\n", encoding="utf-8")
        try:
            result = discover_project_context(root, targets=["apps/mobile/src/profile"], fast_path=True)
            context = result["contextMap"]
            paths = {item["path"] for item in context["sources"]}
            self.assertIn("AGENTS.md", paths)
            self.assertIn("apps/mobile/AGENTS.md", paths)
            self.assertIn("package.json", paths)
            self.assertNotIn("unrelated/AGENTS.md", paths)
            self.assertNotIn("unrelated/docs/large-plan.md", paths)
            self.assertIn("target-scoped fast", result["receipt"]["evidence"][1]["summary"])
            before = context["fingerprints"]["sourceFingerprint"]
            (root / "unrelated" / "docs" / "large-plan.md").write_text("Changed unrelated plan.\n", encoding="utf-8")
            after = discover_project_context(root, targets=["apps/mobile/src/profile"], fast_path=True)["contextMap"]["fingerprints"]["sourceFingerprint"]
            self.assertEqual(before, after)
        finally:
            temporary.cleanup()

    def test_product_identity_is_host_declared_and_not_hardcoded(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        manifest_dir = root / "docs" / "agent-context"
        manifest_dir.mkdir(parents=True)
        manifest = {
            "schemaVersion": "project-context-manifest-v1",
            "projectId": "sample",
            "profiles": ["web"],
            "products": ["Billing Portal"],
        }
        (manifest_dir / "project-context.json").write_text(json.dumps(manifest), encoding="utf-8")
        try:
            self.assertTrue(validate_manifest(manifest)["valid"])
            discovered = discover_project_context(root)
        finally:
            temporary.cleanup()
        self.assertEqual([item["product"] for item in discovered["contextMap"]["productSignals"]], ["billing_portal"])

    def test_secret_like_instruction_is_ignored_without_echoing_content(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        credential_text = "api_key=" + "super-" + "secret-value" + "-123456"
        (root / "AGENTS.md").write_text(credential_text, encoding="utf-8")
        try:
            result = discover_project_context(root, targets=["."])
            source = next(item for item in result["contextMap"]["sources"] if item["path"] == "AGENTS.md")
            self.assertEqual(source["usability"], "ignored")
            serialized = json.dumps(result)
            self.assertNotIn(credential_text, serialized)
            self.assertNotIn("super-secret-value", serialized)
        finally:
            temporary.cleanup()

    def test_lifecycle_conflict_is_visible_and_blocking(self):
        temporary, root = self._repo()
        with temporary:
            workflow = root / "docs" / "workflows"
            workflow.mkdir(parents=True)
            (workflow / "local.md").write_text("Start md-execute automatically.\n", encoding="utf-8")
            context = discover_project_context(root, targets=["apps/web/src"])["contextMap"]
        self.assertEqual(context["status"], "CONFLICT")
        self.assertTrue(any(item["conflictId"] == "lifecycle-authority-001" for item in context["conflicts"]))

    def test_fingerprint_changes_when_relevant_source_changes(self):
        temporary, root = self._repo()
        with temporary:
            before = discover_project_context(root, targets=["apps/web/src"])["contextMap"]["fingerprints"]["sourceFingerprint"]
            (root / "apps" / "web" / "src" / "feature.ts").write_text("export const value = 1;\n", encoding="utf-8")
            after = discover_project_context(root, targets=["apps/web/src"])["contextMap"]["fingerprints"]["sourceFingerprint"]
        self.assertNotEqual(before, after)

    def test_protected_root_and_target_escape_fail_closed(self):
        temporary, root = self._repo()
        outside = tempfile.TemporaryDirectory()
        with temporary, outside:
            with self.assertRaises(ContextDiscoveryError):
                discover_project_context(root, protected_roots=[root])
            with self.assertRaises(ContextDiscoveryError):
                discover_project_context(root, targets=[outside.name])

    def test_active_skill_root_is_protected_by_default(self):
        with self.assertRaises(ContextDiscoveryError):
            discover_project_context(PACKAGE_ROOT)

    def test_tool_neutral_protected_root_is_respected(self):
        temporary, root = self._repo()
        with temporary, patch.dict(os.environ, {"BUILDCREW_PROTECTED_ROOTS": str(root)}, clear=True):
            with self.assertRaises(ContextDiscoveryError):
                discover_project_context(root)

    def test_host_private_directory_is_excluded_from_full_and_target_scans(self):
        temporary, root = self._repo()
        with temporary:
            private = root / ".my-tool-state"
            private.mkdir()
            (private / "AGENTS.md").write_text("private host instructions", encoding="utf-8")
            with patch.dict(os.environ, {"BUILDCREW_CONTEXT_EXCLUDED_DIRS": private.name}, clear=True):
                full = discover_project_context(root)["contextMap"]
                scoped = discover_project_context(root, targets=[private.name], fast_path=True)["contextMap"]
            self.assertNotIn(".my-tool-state/AGENTS.md", {item["path"] for item in full["sources"]})
            self.assertNotIn(".my-tool-state/AGENTS.md", {item["path"] for item in scoped["sources"]})

    def test_invalid_host_private_directory_setting_fails_closed(self):
        temporary, root = self._repo()
        with temporary, patch.dict(os.environ, {"BUILDCREW_CONTEXT_EXCLUDED_DIRS": "../outside"}, clear=True):
            with self.assertRaisesRegex(ContextDiscoveryError, "directory names"):
                discover_project_context(root)

    def test_bounded_enumeration_reports_missing_coverage(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        for name in ("a.txt", "b.txt", "c.txt"):
            (root / name).write_text(name, encoding="utf-8")
        try:
            with patch("engine.project_context.MAX_CANDIDATES", 2):
                result = discover_project_context(root)
        finally:
            temporary.cleanup()
        self.assertEqual(result["contextMap"]["status"], "INCOMPLETE")
        self.assertTrue(any(item["gapId"] == "candidate-enumeration-truncated" for item in result["contextMap"]["gaps"]))
        self.assertEqual(result["receipt"]["terminalStatus"], "MISSING_COVERAGE")
        self.assertTrue(any(item["result"] == "partial" for item in result["receipt"]["evidence"]))

    def test_explicit_full_repository_bound_is_validated_and_reported(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        for name in ("a.txt", "b.txt", "c.txt"):
            (root / name).write_text(name, encoding="utf-8")
        try:
            with patch("engine.project_context.MAX_CANDIDATES", 2):
                default = discover_project_context(root)
                explicit = discover_project_context(root, max_candidates=3)
                self.assertEqual(default["receipt"]["terminalStatus"], "MISSING_COVERAGE")
                self.assertEqual(explicit["receipt"]["terminalStatus"], "COMPLETED")
                enumeration = next(
                    item for item in explicit["receipt"]["evidence"]
                    if item["operation"] == "bounded_candidate_enumeration"
                )
                self.assertIn("enumerated 3 files within a 3-file bound", enumeration["summary"])
        finally:
            temporary.cleanup()

    def test_invalid_explicit_bounds_and_fast_path_combination_fail_before_walk(self):
        temporary, root = self._repo()
        with temporary, patch("engine.project_context._collect_files") as collect:
            for limit in (0, -1, True, False, 50001, 1.5, "100"):
                with self.subTest(limit=limit), self.assertRaises(ContextDiscoveryError):
                    discover_project_context(root, max_candidates=limit)
            with self.assertRaises(ContextDiscoveryError):
                discover_project_context(root, fast_path=True, max_candidates=100)
            collect.assert_not_called()

    def test_explicit_boundaries_are_accepted(self):
        temporary, root = self._repo()
        with temporary:
            minimum = discover_project_context(root, max_candidates=1)
            maximum = discover_project_context(root, max_candidates=50000)
        self.assertEqual(minimum["receipt"]["terminalStatus"], "MISSING_COVERAGE")
        self.assertEqual(maximum["receipt"]["terminalStatus"], "COMPLETED")

    def test_cli_explicit_bound_reaches_engine_and_rejects_invalid_or_fast_combination(self):
        temporary, root = self._repo()
        script = PACKAGE_ROOT / "scripts" / "discover_project_context.py"
        with temporary:
            complete = subprocess.run(
                [sys.executable, "-B", str(script), str(root), "--max-files", "100"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            partial = subprocess.run(
                [sys.executable, "-B", str(script), str(root), "--max-files", "2"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            invalid = subprocess.run(
                [sys.executable, "-B", str(script), str(root), "--max-files", "50001"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            fast = subprocess.run(
                [sys.executable, "-B", str(script), str(root), "--max-files", "100", "--fast-mobile"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        self.assertEqual(complete.returncode, 0, complete.stderr)
        complete_output = json.loads(complete.stdout)
        self.assertEqual(complete_output["receipt"]["terminalStatus"], "COMPLETED")
        self.assertIn(
            "within a 100-file bound",
            next(item["summary"] for item in complete_output["receipt"]["evidence"]
                 if item["operation"] == "bounded_candidate_enumeration"),
        )
        self.assertEqual(partial.returncode, 0, partial.stderr)
        self.assertEqual(json.loads(partial.stdout)["receipt"]["terminalStatus"], "MISSING_COVERAGE")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(fast.returncode, 2)

    def test_external_state_requires_non_overlapping_host_root(self):
        temporary, root = self._repo()
        state = tempfile.TemporaryDirectory()
        with temporary, state:
            result = discover_project_context(root, targets=["apps/web/src"])
            persisted = persist_context(
                result["contextMap"],
                result["receipt"],
                state_root=state.name,
                repository_root=root,
            )
            self.assertEqual(persisted["persistence"], "external")
            loaded = load_context(
                state_root=state.name,
                repository_root=root,
                repository=result["contextMap"]["repository"],
            )
            self.assertIsNotNone(loaded)
            self.assertFalse(Path(persisted["directory"]).is_relative_to(root))
            with self.assertRaises(ValueError):
                validate_state_root(root / ".context", root)
            map_path = Path(persisted["mapPath"])
            tampered = json.loads(map_path.read_text(encoding="utf-8"))
            tampered["status"] = "STALE"
            map_path.write_text(json.dumps(tampered), encoding="utf-8")
            self.assertIsNone(load_context(
                state_root=state.name,
                repository_root=root,
                repository=result["contextMap"]["repository"],
            ))

    def test_symlink_outside_repository_is_not_ingested(self):
        temporary, root = self._repo()
        outside = tempfile.TemporaryDirectory()
        with temporary, outside:
            external = Path(outside.name) / "external.md"
            external.write_text("outside content", encoding="utf-8")
            link = root / "docs"
            link.mkdir()
            try:
                (link / "escape.md").symlink_to(external)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            result = discover_project_context(root, targets=["docs"])
            self.assertNotIn("docs/escape.md", {item["path"] for item in result["contextMap"]["sources"]})

    def test_git_remote_credentials_are_removed_from_identity(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        git = root / ".git"
        git.mkdir()
        (git / "config").write_text(
            "[remote \"origin\"]\n\turl = https://user:password@example.com/team/project.git\n",
            encoding="utf-8",
        )
        (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        try:
            context = discover_project_context(root)["contextMap"]
        finally:
            temporary.cleanup()
        repository_id = context["repository"]["repositoryId"]
        self.assertEqual(repository_id, "remote:https://example.com/team/project.git")
        self.assertNotIn("password", json.dumps(context))


if __name__ == "__main__":
    unittest.main()
