from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_continuity import (  # noqa: E402
    build_context_budget,
    close_continuity,
    measure_input_context,
    start_or_resume,
)


class ContextContinuityTests(unittest.TestCase):
    def packet(self) -> dict:
        return {
            "taskId": "continuity-module-1",
            "projectId": "demo",
            "repository": "demo-repository",
            "objective": "Implement a resumable module workflow across working days",
            "acceptanceCriteria": [
                "Resume the exact task without repeating the original request",
                "Store sanitized terminal context and next action",
                "Report bounded context reduction honestly",
            ],
            "requestType": "IMPLEMENTATION",
            "complexity": "NORMAL",
            "write": True,
            "module": "continuity",
            "feature": "resume",
            "path": "engine/context_continuity.py",
            "rawRequest": "do not persist this raw prompt",
        }

    def evidence(self) -> list[dict[str, str]]:
        return [
            {
                "repository": "demo-repository",
                "commitOrTree": "tree-demo",
                "path": "engine/context_continuity.py",
                "symbol": "start_or_resume",
                "check": "focused continuity test",
                "sourceType": "test",
            }
        ]

    def test_measurement_is_bounded_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            (memory / "MEMORY.md").write_text("# Continuity\n\nmodule resume\n", encoding="utf-8")
            result = measure_input_context(
                self.packet(), memory_root=memory, now="2026-10-23T09:00:00Z"
            )
            self.assertEqual(result["outcome"], "MEASURED")
            budget = result["contextBudget"]
            self.assertEqual(budget["schemaVersion"], "context-budget-report-v1")
            self.assertGreaterEqual(budget["reduction"]["estimatedPercent"], 0)
            self.assertLessEqual(budget["reduction"]["estimatedPercent"], 100)
            self.assertFalse((root / "state").exists())

    def test_start_close_and_next_day_resume_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            canonical = memory / "MEMORY.md"
            canonical.write_text("# Canonical memory\n\nKeep source current.\n", encoding="utf-8")
            state_root = root / "state"
            store = root / "learning-store.json"

            started = start_or_resume(
                self.packet(),
                action="start",
                state_root=state_root,
                memory_root=memory,
                now="2026-10-23T09:00:00Z",
            )
            self.assertEqual(started["outcome"], "STARTED")
            serialized = json.dumps(started["state"], sort_keys=True)
            self.assertNotIn("raw prompt", serialized)

            closed = close_continuity(
                {"taskId": self.packet()["taskId"]},
                {
                    "status": "completed",
                    "summary": "Implemented the resumable workflow and verified its focused tests.",
                    "nextAction": "Resume with current-source revalidation on the next working day.",
                    "branchOrTree": "tree-demo",
                    "filesInvolved": ["engine/context_continuity.py"],
                    "evidence": self.evidence(),
                },
                state_root=state_root,
                memory_root=memory,
                store_path=store,
                now="2026-10-23T17:00:00Z",
            )
            self.assertEqual(closed["outcome"], "CLOSED")
            self.assertTrue(closed["dailyReference"]["path"])
            self.assertTrue(closed["dailyReference"]["ingestionRequired"])
            self.assertEqual(canonical.read_text(encoding="utf-8"), "# Canonical memory\n\nKeep source current.\n")
            self.assertTrue(store.is_file())

            resumed = start_or_resume(
                self.packet(),
                action="resume",
                state_root=state_root,
                memory_root=memory,
                now="2026-10-24T09:00:00Z",
            )
            self.assertEqual(resumed["outcome"], "RESUMED")
            self.assertEqual(resumed["state"]["status"], "IN_PROGRESS")
            self.assertIn("Implemented the resumable workflow", resumed["context"])
            self.assertTrue(
                any(
                    source["path"].startswith("extensions/ad_hoc/notes/ae-continuity-")
                    for source in resumed["state"]["contextReferences"]["sources"]
                )
            )

    def test_resume_requires_exact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            start_or_resume(
                self.packet(), action="start", state_root=root / "state", memory_root=memory
            )
            changed = self.packet()
            changed["acceptanceCriteria"] = ["A materially different task"]
            result = start_or_resume(
                changed, action="resume", state_root=root / "state", memory_root=memory
            )
            self.assertEqual(result["outcome"], "CONFLICT")

    def test_budget_reports_target_without_fabricating_provider_tokens(self):
        budget = build_context_budget(
            task_id="budget-test",
            packet={"taskId": "budget-test", "objective": "x", "acceptanceCriteria": [], "complexity": "NORMAL"},
            meta={"projectId": "p", "repository": "r"},
            sources=[{"fullCharacters": 12000}],
            context_text="compact context",
        )
        self.assertEqual(budget["reduction"]["targetStatus"], "MET")
        self.assertIn("provider tokenizer not used", budget["method"])

    def test_cli_persists_worklog_as_part_of_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            state = root / "state"
            store = root / "store.json"
            worklog = root / "worklog"
            packet_path = root / "packet.json"
            closure_path = root / "closure.json"
            packet_path.write_text(json.dumps(self.packet()), encoding="utf-8")
            closure_path.write_text(
                json.dumps({"status": "completed", "summary": "CLI continuity close", "branchOrTree": "tree", "evidence": [{**self.evidence()[0], "commitOrTree": "tree"}]}),
                encoding="utf-8",
            )
            script = PACKAGE_ROOT / "scripts" / "context_loop.py"
            start = subprocess.run(
                [sys.executable, "-B", str(script), "start", "--input-json", str(packet_path), "--state-root", str(state), "--memory-root", str(memory), "--now", "2026-10-23T09:00:00Z"],
                cwd=PACKAGE_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
            close = subprocess.run(
                [sys.executable, "-B", str(script), "close", "--task-id", self.packet()["taskId"], "--closure-json", str(closure_path), "--state-root", str(state), "--memory-root", str(memory), "--store", str(store), "--worklog-root", str(worklog), "--now", "2026-10-23T17:00:00Z"],
                cwd=PACKAGE_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(close.returncode, 0, close.stdout + close.stderr)
            report = json.loads(close.stdout)
            self.assertEqual(report["outcome"], "CLOSED")
            self.assertEqual(report["worklog"]["status"], "WRITTEN")
            self.assertTrue(list(worklog.rglob("*.md")))

    def test_continuity_schemas_are_versioned_and_strict(self):
        for name, version in (
            ("continuity-session-v1.schema.json", "continuity-session-v1.schema.json"),
            ("continuity-report-v1.schema.json", "continuity-report-v1.schema.json"),
            ("context-budget-report-v1.schema.json", "context-budget-report-v1.schema.json"),
        ):
            with self.subTest(schema=name):
                schema = json.loads((PACKAGE_ROOT / "schemas" / name).read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertEqual(schema["$id"], version)
                self.assertFalse(schema["additionalProperties"])
                self.assertTrue(schema["required"])

    def test_cli_start_close_resume_uses_only_shared_home_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared-storage"
            legacy = root / "unused-legacy"
            environment = {
                **os.environ,
                "BUILDCREW_HOME": str(shared),
                "CODEX_MEMORY_ROOT": str(legacy / "memory"),
                "AUTONOMOUS_ENGINEERING_LEARNING_STORE": str(legacy / "store.json"),
                "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT": str(legacy / "continuity"),
                "CODEX_DAILY_WORKLOG_ROOT": str(legacy / "worklogs"),
                "BUILDCREW_WORKLOG_ALLOWED_ROOTS": str(shared),
            }
            packet_path = root / "packet.json"
            closure_path = root / "closure.json"
            packet_path.write_text(json.dumps(self.packet()), encoding="utf-8")
            closure_path.write_text(json.dumps({
                "status": "completed",
                "summary": "Shared storage keeps task continuity consistent between tools.",
                "branchOrTree": "tree-demo",
                "evidence": self.evidence(),
            }), encoding="utf-8")
            script = PACKAGE_ROOT / "scripts/context_loop.py"

            def run(command: str, *arguments: str) -> dict:
                completed = subprocess.run(
                    [sys.executable, "-B", str(script), command, *arguments],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                return json.loads(completed.stdout)

            started = run("start", "--input-json", str(packet_path))
            self.assertEqual("STARTED", started["outcome"])
            self.assertTrue(Path(started["sessionPath"]).is_relative_to(shared / "continuity"))
            closed = run(
                "close", "--task-id", self.packet()["taskId"], "--closure-json", str(closure_path),
            )
            self.assertEqual("CLOSED", closed["outcome"])
            store = shared / "memory/autonomous-engineering/learning-store.json"
            self.assertEqual(str(store), closed["learning"]["storePath"])
            self.assertTrue(store.is_file())
            self.assertTrue(Path(closed["worklog"]["path"]).is_relative_to(shared / "worklogs"))
            resumed = run("resume", "--input-json", str(packet_path))
            self.assertEqual("RESUMED", resumed["outcome"])
            self.assertIn("Shared storage keeps task continuity", resumed["context"])
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
