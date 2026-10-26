import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.git_source_context import check_branch_availability, inspect_source_context
from engine.source_provenance_gate import decide_source_provenance
from engine.worktree_host_adapter import provision_worktree


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, text=True, capture_output=True)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = Path(self.temp_dir.name) / "repository"
        self.repository.mkdir()
        git(self.repository, "init", "-b", "main")
        git(self.repository, "config", "user.email", "x@y.z")
        git(self.repository, "config", "user.name", "x")
        (self.repository / "a").write_text("a")
        git(self.repository, "add", "a")
        git(self.repository, "commit", "-m", "initial")

        self.origin = Path(self.temp_dir.name) / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", str(self.origin)],
            check=True,
            capture_output=True,
        )
        git(self.repository, "remote", "add", "origin", str(self.origin))
        git(self.repository, "push", "-u", "origin", "main")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_exact_lane_continues(self):
        context = inspect_source_context(self.repository)
        decision = decide_source_provenance(
            context,
            {
                "source": context,
                "writerOwner": "lead",
                "writeAllowlist": ["a"],
            },
            read_only=False,
            requested_paths=["a"],
        )
        self.assertEqual("CONTINUE_CURRENT", decision["decision"])

    def test_unbound_or_changed_source_fails_closed(self):
        context = inspect_source_context(self.repository)
        self.assertEqual(
            "CONFIRM_CURRENT",
            decide_source_provenance(context, None, read_only=False)["decision"],
        )
        changed = dict(context, headSha="changed")
        decision = decide_source_provenance(
            changed,
            {"source": context, "writerOwner": "lead", "writeAllowlist": ["a"]},
            read_only=False,
            requested_paths=["a"],
        )
        self.assertEqual("PROVISION_OR_REUSE", decision["decision"])

    def test_writer_scope_is_required(self):
        context = inspect_source_context(self.repository)
        decision = decide_source_provenance(
            context,
            {"source": context, "writeAllowlist": ["a"]},
            read_only=False,
            requested_paths=["a"],
        )
        self.assertEqual("BLOCKED", decision["decision"])

    def test_source_context_normalizes_paths_and_redacts_remote_credentials(self):
        git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://user:secret@example.com/org/repository.git?token=hidden",
        )
        (self.repository / "a").write_text("changed")
        (self.repository / "new file").write_text("new")
        context = inspect_source_context(self.repository)
        self.assertEqual("https://example.com/org/repository.git", context["origin"])
        self.assertNotIn("secret", context["repositoryId"])
        self.assertIn("a", context["baselinePaths"])
        self.assertIn("new file", context["baselinePaths"])
        self.assertNotIn("? ", context["baselinePaths"])
        self.assertTrue(Path(context["worktreeId"]).exists())
        self.assertTrue(Path(context["commonDir"]).exists())

    def test_duplicate_local_branch_rejected(self):
        git(self.repository, "branch", "task/taken")
        result = check_branch_availability(self.repository, "task/taken")
        self.assertIn("LOCAL", result["conflicts"])

    def test_provision_creates_worktree(self):
        target = Path(self.temp_dir.name) / "worktree"
        receipt = provision_worktree(
            {
                "repositoryRoot": str(self.repository),
                "worktreePath": str(target),
                "branch": "task/new",
                "baseRef": "HEAD",
                "approvalId": "yes",
                "taskId": "task",
                "laneId": "lane",
            }
        )
        self.assertEqual("COMPLETED", receipt["status"])
        self.assertTrue((target / ".git").exists())
        context = inspect_source_context(target)
        self.assertEqual("task/new", context["branch"])
        self.assertEqual("LINKED", context["checkoutKind"])
