from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

from engine.context_continuity import close_continuity, start_or_resume  # noqa: E402
from memory_context_runtime import lookup_memory_context  # noqa: E402
from project_memory import ingest_reviewed_candidate, project_memory_path  # noqa: E402


def _packet() -> dict:
    return {
        "taskId": "project-module-1",
        "projectId": "sample-project",
        "repository": "sample-repo",
        "objective": "Document the tested module behavior",
        "acceptanceCriteria": ["The module behavior is verified"],
        "requestType": "IMPLEMENTATION",
        "actionable": True,
        "module": "module",
        "feature": "behavior",
    }


class AutomaticContinuityTests(unittest.TestCase):
    def test_two_same_day_closures_keep_distinct_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            state = root / "state"
            start_or_resume(_packet(), action="auto", state_root=state, memory_root=memory)
            closure = {
                "status": "paused", "summary": "First verified session summary.",
                "branchOrTree": "tree-reviewed",
                "evidence": [{
                    "repository": "sample-repo", "commitOrTree": "tree-reviewed",
                    "path": "src/module.py", "symbol": "behavior",
                    "check": "focused test", "sourceType": "test",
                }],
            }
            first = close_continuity(
                {"taskId": _packet()["taskId"]}, closure,
                state_root=state, memory_root=memory, now="2026-10-26T10:00:00Z",
            )
            closure["summary"] = "Second verified session summary."
            second = close_continuity(
                {"taskId": _packet()["taskId"]}, closure,
                state_root=state, memory_root=memory, now="2026-10-26T16:00:00Z",
            )
            self.assertEqual("CLOSED", first["outcome"])
            self.assertEqual("CLOSED", second["outcome"])
            self.assertNotEqual(first["dailyReference"]["path"], second["dailyReference"]["path"])
            self.assertTrue(Path(first["dailyReference"]["path"]).is_file())
            self.assertTrue(Path(second["dailyReference"]["path"]).is_file())
            resumed = start_or_resume(_packet(), action="auto", state_root=state, memory_root=memory)
            self.assertIn("Second verified session summary", resumed["context"])

    def test_auto_chooses_start_resume_and_lookup_only_without_a_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            state = root / "state"
            input_path = root / "input.json"
            input_path.write_text(json.dumps(_packet()), encoding="utf-8")
            script = PACKAGE_ROOT / "scripts/context_loop.py"

            def run() -> dict:
                completed = subprocess.run(
                    [sys.executable, "-B", str(script), "auto", "--input-json", str(input_path),
                     "--memory-root", str(memory), "--state-root", str(state)],
                    cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                return json.loads(completed.stdout)

            self.assertEqual("STARTED", run()["outcome"])
            self.assertEqual("RESUMED", run()["outcome"])
            session_count = len(list((state / "sessions").glob("*.json")))
            input_path.write_text(json.dumps({
                "requestType": "QUESTION", "actionable": False,
                "projectId": "sample-project", "repository": "sample-repo",
                "inputText": "What does this module do?",
            }), encoding="utf-8")
            info = run()
            self.assertEqual("LOOKUP_ONLY", info["outcome"])
            self.assertEqual([], info["sideEffects"])
            self.assertEqual(session_count, len(list((state / "sessions").glob("*.json"))))

    def test_reviewed_candidate_becomes_scoped_retrievable_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            source = root / "source"
            source_file = source / "src/module.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("def behavior():\n    return 'verified'\n", encoding="utf-8")
            state = root / "state"
            start_or_resume(_packet(), action="auto", state_root=state, memory_root=memory)
            closed = close_continuity(
                {"taskId": _packet()["taskId"]},
                {
                    "status": "completed", "summary": "Verified module behavior with a focused check.",
                    "branchOrTree": "tree-reviewed", "filesInvolved": ["src/module.py"],
                    "evidence": [{
                        "repository": "sample-repo", "commitOrTree": "tree-reviewed",
                        "path": "src/module.py", "symbol": "behavior",
                        "check": "focused test", "sourceType": "test",
                    }],
                },
                state_root=state, memory_root=memory,
            )
            self.assertEqual("CLOSED", closed["outcome"])
            canonical = project_memory_path(memory, "sample-project", "sample-repo")
            self.assertFalse(canonical.exists())
            candidate = Path(closed["dailyReference"]["path"])
            review = {
                "schemaVersion": "canonical-ingestion-review-v1",
                "decision": "APPROVED", "approvalId": "review-1", "reviewer": "Project Reviewer",
                "taskId": _packet()["taskId"], "recordId": closed["learning"]["recordId"],
                "projectId": "sample-project", "repository": "sample-repo",
                "branchOrTree": "tree-reviewed",
                "candidateRelativePath": candidate.relative_to(memory).as_posix(),
                "candidateSha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "sourceHashes": {"src/module.py": hashlib.sha256(source_file.read_bytes()).hexdigest()},
            }
            invalid_review = dict(
                review,
                candidateSha256=hashlib.sha256(b"different candidate note").hexdigest(),
            )
            with self.assertRaisesRegex(ValueError, "candidate note digest"):
                ingest_reviewed_candidate(invalid_review, memory_root=memory, source_root=source)
            self.assertFalse(canonical.exists())
            review_path = root / "approved-review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            command = subprocess.run(
                [sys.executable, "-B", str(PACKAGE_ROOT / "scripts/ingest_memory_candidate.py"),
                 "--review-json", str(review_path), "--memory-root", str(memory),
                 "--source-root", str(source)],
                cwd=PACKAGE_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, command.returncode, command.stdout + command.stderr)
            first = json.loads(command.stdout)
            self.assertEqual("INGESTED", first["outcome"])
            self.assertIn("Verified module behavior", canonical.read_text(encoding="utf-8"))
            repeated = ingest_reviewed_candidate(review, memory_root=memory, source_root=source)
            self.assertEqual("ALREADY_INGESTED", repeated["outcome"])
            lookup = lookup_memory_context(
                {"projectId": "sample-project", "repository": "sample-repo", "inputText": "module behavior"},
                memory_root=memory,
            )
            self.assertEqual("matched", lookup["outcome"])
            self.assertIn(canonical.relative_to(memory).as_posix(), lookup["selectedPaths"])
            index_root = memory / "autonomous-engineering"
            (index_root / "route-map.json").write_text(json.dumps({"routes": []}), encoding="utf-8")
            (index_root / "module-registry.json").write_text(
                json.dumps({"repositories": []}), encoding="utf-8",
            )
            lookup = lookup_memory_context(
                {"projectId": "sample-project", "repository": "sample-repo", "inputText": "module behavior"},
                memory_root=memory,
            )
            self.assertEqual("matched", lookup["outcome"])
            self.assertEqual("project_memory_only", lookup["registryStatus"])
            next_task = _packet()
            next_task.update({
                "taskId": "project-module-2",
                "objective": "Extend the verified module behavior",
            })
            continued = start_or_resume(
                next_task, action="auto", state_root=state, memory_root=memory,
            )
            self.assertEqual("STARTED", continued["outcome"])
            self.assertIn("Verified module behavior", continued["context"])
            try:
                from jsonschema import Draft202012Validator
            except ImportError:
                pass
            else:
                schema = json.loads(
                    (PACKAGE_ROOT / "schemas/memory-context-lookup-v1.schema.json").read_text(encoding="utf-8")
                )
                Draft202012Validator(schema).validate(lookup)
            other = lookup_memory_context(
                {"projectId": "other-project", "repository": "sample-repo", "inputText": "module behavior"},
                memory_root=memory,
            )
            self.assertNotIn(canonical.relative_to(memory).as_posix(), other["selectedPaths"])

            source_file.write_text("def behavior():\n    return 'changed'\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current source"):
                ingest_reviewed_candidate(review, memory_root=memory, source_root=source)


if __name__ == "__main__":
    unittest.main()
