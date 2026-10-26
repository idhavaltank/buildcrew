import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import learning_runtime
from learning_runtime import classify_input, find_related_knowledge, load_learning_store, record_task_learning


NOW = "2026-09-11T12:00:00Z"


def lesson_payload(**overrides):
    payload = {
        "taskId": "task-replay-1",
        "intent": "bug",
        "status": "completed",
        "projectId": "general",
        "repository": "backend",
        "application": "api",
        "module": "sync",
        "feature": "replay-acknowledgement",
        "branchOrTree": "tree-current",
        "accessScope": ["project:general:read"],
        "claim": "Replay acknowledgements must be persisted after successful processing.",
        "kind": "failure_pattern",
        "canonicalKey": "replay-acknowledgement",
        "fingerprints": {
            "issueKey": "BUG-123",
            "path": "apps/api/sync.py",
            "symbol": "ReplayWorker",
            "taskType": "bug",
        },
        "evidence": [
            {
                "repository": "backend",
                "commitOrTree": "tree-current",
                "path": "apps/api/sync.py",
                "symbol": "ReplayWorker",
                "check": "python3 -m unittest tests.test_replay",
                "sourceType": "test",
            }
        ],
        "filesInvolved": ["apps/api/sync.py"],
        "businessLogic": "The acknowledgement is the durable replay boundary.",
        "fixStrategy": "Persist the acknowledgement after the side effect succeeds.",
    }
    payload.update(overrides)
    return payload


class LearningRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "ae-learning-store.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_simple_question_skips_learning_and_does_not_create_store(self):
        result = record_task_learning(
            {
                "taskId": "question-1",
                "intent": "simple_question",
                "status": "completed",
                "inputText": "What does this helper do?",
            },
            store_path=self.store,
            now=NOW,
        )
        self.assertEqual("SKIP", result["decision"])
        self.assertEqual("skipped_simple", result["outcome"])
        self.assertFalse(self.store.exists())

    def test_bug_is_persisted_as_durable_learning(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        memory_root.mkdir(parents=True)
        result = record_task_learning(
            lesson_payload(), store_path=self.store, memory_root=memory_root, now=NOW
        )
        self.assertEqual("candidate_pending_review", result["outcome"])
        self.assertEqual("queued", result["moduleFeatureWriteback"]["outcome"])
        self.assertTrue(Path(result["moduleFeatureWriteback"]["notePath"]).is_file())
        document = load_learning_store(self.store)["document"]
        self.assertEqual(1, len(document["records"]))
        self.assertEqual("candidate", document["records"][0]["status"])
        self.assertEqual("replay-acknowledgement", document["records"][0]["canonicalKey"])

    def test_existing_lesson_is_retrieved_and_expanded_without_duplicate(self):
        first = record_task_learning(lesson_payload(), store_path=self.store, now=NOW)
        second_payload = lesson_payload(
            taskId="task-replay-2",
            evidence=[
                lesson_payload()["evidence"][0],
                {
                    "repository": "backend",
                    "commitOrTree": "tree-current",
                    "path": "apps/api/replay.test.py",
                    "symbol": "replayAck",
                    "check": "python3 -m unittest tests.test_replay_ack",
                    "sourceType": "test",
                },
            ],
            filesInvolved=["apps/api/sync.py", "apps/api/replay.test.py"],
            businessLogic="The acknowledgement is also asserted by the replay regression test.",
        )
        second = record_task_learning(second_payload, store_path=self.store, now=NOW)
        self.assertEqual("updated", second["outcome"])
        self.assertIn(second["updateKind"], {"same_claim_merge", "evidence_expansion"})
        self.assertIn(first["recordId"], second["matchedRecordIds"])
        document = load_learning_store(self.store)["document"]
        self.assertEqual(1, len([record for record in document["records"] if record["status"] in {"candidate", "verified", "confirmed"}]))
        record = next(record for record in document["records"] if record["status"] == "candidate")
        self.assertEqual(2, len(record["evidence"]))
        self.assertIn("apps/api/replay.test.py", record["filesInvolved"])

        related = find_related_knowledge(second_payload, store_path=self.store, now=NOW)
        self.assertEqual("matched", related["outcome"])
        self.assertEqual(first["recordId"], related["records"][0]["id"])

    def test_changed_claim_replaces_old_record_and_preserves_history(self):
        first = record_task_learning(lesson_payload(), store_path=self.store, now=NOW)
        replacement = record_task_learning(
            lesson_payload(
                taskId="task-replay-3",
                claim="Replay acknowledgements must be persisted before the worker reports completion.",
            ),
            store_path=self.store,
            now=NOW,
        )
        self.assertEqual("updated", replacement["outcome"])
        self.assertEqual("superseded_and_created", replacement["updateKind"])
        self.assertNotEqual(first["recordId"], replacement["recordId"])
        records = load_learning_store(self.store)["document"]["records"]
        self.assertEqual("superseded", next(item for item in records if item["id"] == first["recordId"])["status"])
        self.assertEqual("candidate", next(item for item in records if item["id"] == replacement["recordId"])["status"])

    def test_generic_task_type_does_not_expand_an_unrelated_lesson(self):
        first = record_task_learning(lesson_payload(), store_path=self.store, now=NOW)
        unrelated = record_task_learning(
            lesson_payload(
                taskId="task-billing-1",
                claim="Invoice exports must preserve the customer timezone.",
                canonicalKey="invoice-timezone",
                fingerprints={"taskType": "bug", "path": "apps/api/invoices.py", "symbol": "exportInvoice"},
                filesInvolved=["apps/api/invoices.py"],
            ),
            store_path=self.store,
            now=NOW,
        )
        self.assertEqual("candidate_pending_review", unrelated["outcome"])
        self.assertNotIn(first["recordId"], unrelated["matchedRecordIds"])
        records = load_learning_store(self.store)["document"]["records"]
        self.assertEqual(2, len(records))

    def test_different_tree_never_updates_or_exposes_existing_lesson(self):
        evidence_a = dict(lesson_payload()["evidence"][0], commitOrTree="tree-a")
        evidence_b = dict(lesson_payload()["evidence"][0], commitOrTree="tree-b")
        first = record_task_learning(
            lesson_payload(branchOrTree="tree-a", evidence=[evidence_a]), store_path=self.store, now=NOW
        )
        second_payload = lesson_payload(
            taskId="task-replay-tree-b", branchOrTree="tree-b", evidence=[evidence_b]
        )
        related = find_related_knowledge(second_payload, store_path=self.store, now=NOW)
        second = record_task_learning(second_payload, store_path=self.store, now=NOW)

        self.assertEqual("no_relevant_memory", related["outcome"])
        self.assertEqual("candidate_pending_review", second["outcome"])
        self.assertNotIn(first["recordId"], second["matchedRecordIds"])
        records = load_learning_store(self.store)["document"]["records"]
        self.assertEqual({"tree-a", "tree-b"}, {record["branchOrTree"] for record in records})

    def test_different_access_scope_never_merges_with_existing_lesson(self):
        first = record_task_learning(
            lesson_payload(accessScope=["project:other:read"]), store_path=self.store, now=NOW
        )
        second = record_task_learning(
            lesson_payload(taskId="task-private", accessScope=["tenant:private:read"]),
            store_path=self.store,
            now=NOW,
        )

        self.assertEqual("candidate_pending_review", second["outcome"])
        self.assertNotIn(first["recordId"], second["matchedRecordIds"])
        records = load_learning_store(self.store)["document"]["records"]
        self.assertEqual(2, len(records))

    def test_different_tenant_never_merges_with_existing_lesson(self):
        first = record_task_learning(lesson_payload(tenantId="tenant-a"), store_path=self.store, now=NOW)
        second = record_task_learning(
            lesson_payload(taskId="task-tenant-b", tenantId="tenant-b"), store_path=self.store, now=NOW
        )

        self.assertEqual("candidate_pending_review", second["outcome"])
        self.assertNotIn(first["recordId"], second["matchedRecordIds"])
        records = load_learning_store(self.store)["document"]["records"]
        self.assertEqual({"tenant-a", "tenant-b"}, {record["tenantId"] for record in records})

    def test_expired_lesson_is_not_returned_or_updated(self):
        first = record_task_learning(lesson_payload(), store_path=self.store, now=NOW)
        future = "2027-04-01T12:00:00Z"
        related = find_related_knowledge(lesson_payload(taskId="future-query"), store_path=self.store, now=future)
        second = record_task_learning(
            lesson_payload(taskId="future-write"), store_path=self.store, now=future
        )

        self.assertEqual("no_relevant_memory", related["outcome"])
        self.assertEqual("candidate_pending_review", second["outcome"])
        self.assertNotIn(first["recordId"], second["matchedRecordIds"])
        self.assertEqual(2, len(load_learning_store(self.store)["document"]["records"]))

    def test_concurrent_unique_writes_preserve_every_record(self):
        def write(index: int) -> str:
            payload = lesson_payload(
                taskId=f"concurrent-{index}",
                claim=f"Worker {index} preserves its own durable boundary.",
                canonicalKey=f"concurrent-boundary-{index}",
                fingerprints={
                    "issueKey": f"BUG-{index}",
                    "path": f"apps/api/worker_{index}.py",
                    "symbol": f"Worker{index}",
                    "taskType": "bug",
                },
                evidence=[
                    {
                        "repository": "backend",
                        "commitOrTree": "tree-current",
                        "path": f"apps/api/worker_{index}.py",
                        "symbol": f"Worker{index}",
                        "check": f"python3 -m unittest tests.test_worker_{index}",
                        "sourceType": "test",
                    }
                ],
                filesInvolved=[f"apps/api/worker_{index}.py"],
            )
            return record_task_learning(payload, store_path=self.store, now=NOW)["outcome"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(write, range(16)))

        self.assertEqual(["candidate_pending_review"] * 16, outcomes)
        loaded = load_learning_store(self.store)
        self.assertTrue(loaded["valid"], loaded["errors"])
        self.assertEqual(16, len(loaded["document"]["records"]))
        self.assertEqual(16, len({record["id"] for record in loaded["document"]["records"]}))

    def test_missing_evidence_blocks_required_learning_without_writing(self):
        payload = lesson_payload(evidence=[])
        result = record_task_learning(payload, store_path=self.store, now=NOW)
        self.assertEqual("REQUIRED", result["decision"])
        self.assertEqual("blocked", result["outcome"])
        self.assertFalse(self.store.exists())

    def test_missing_terminal_status_blocks_learning_without_writing(self):
        payload = lesson_payload()
        payload.pop("status")
        result = record_task_learning(payload, store_path=self.store, now=NOW)

        self.assertEqual("REQUIRED", result["decision"])
        self.assertEqual("blocked", result["outcome"])
        self.assertTrue(any("status" in error for error in result["errors"]))
        self.assertFalse(self.store.exists())

    def test_unknown_intent_still_uses_actionable_text_detection(self):
        examples = (
            ("Fix the broken replay error", "change"),
            ("Review this module for promotion safety", "review"),
            ("Audit this implementation", "review"),
            ("Assess whether this is safe to release", "review"),
            ("Validate this behavior", "review"),
        )
        for input_text, expected_signal in examples:
            with self.subTest(input_text=input_text):
                result = classify_input(
                    {"intent": "request", "inputText": input_text},
                    input_text=input_text,
                )
                self.assertEqual("REQUIRED", result["decision"])
                self.assertIn(expected_signal, result["signals"])

    def test_evidence_must_match_record_repository_and_tree(self):
        for evidence_repository, evidence_tree in (
            ("other-repository", "tree-current"),
            ("backend", "tree-other"),
        ):
            with self.subTest(repository=evidence_repository, tree=evidence_tree):
                evidence = dict(lesson_payload()["evidence"][0])
                evidence["repository"] = evidence_repository
                evidence["commitOrTree"] = evidence_tree
                result = record_task_learning(
                    lesson_payload(taskId=f"bad-evidence-{evidence_repository}-{evidence_tree}", evidence=[evidence]),
                    store_path=self.store,
                    now=NOW,
                )
                self.assertEqual("blocked", result["outcome"])
        self.assertFalse(self.store.exists())

    def test_store_is_json_and_does_not_contain_raw_input_text(self):
        payload = lesson_payload(inputText="Do not persist this raw question.")
        record_task_learning(payload, store_path=self.store, now=NOW)
        raw = self.store.read_text(encoding="utf-8")
        self.assertNotIn("Do not persist this raw question", raw)
        json.loads(raw)

    def test_simple_question_never_creates_module_feature_note(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        result = record_task_learning(
            {
                "taskId": "question-2",
                "intent": "simple_question",
                "status": "completed",
                "inputText": "What does this helper do?",
            },
            store_path=self.store,
            memory_root=memory_root,
            now=NOW,
        )

        self.assertEqual("skipped_simple", result["outcome"])
        self.assertEqual("not_required", result["moduleFeatureWriteback"]["outcome"])
        self.assertFalse((memory_root / "extensions" / "ad_hoc" / "notes").exists())

    def test_source_backed_simple_input_can_save_explicit_reusable_learning(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        memory_root.mkdir(parents=True)
        result = record_task_learning(
            lesson_payload(
                taskId="question-with-new-source-fact",
                intent="simple_question",
                reusableLearning=True,
            ),
            store_path=self.store,
            memory_root=memory_root,
            now=NOW,
        )

        self.assertEqual("REQUIRED", result["decision"])
        self.assertEqual("candidate_pending_review", result["outcome"])
        self.assertEqual("queued", result["moduleFeatureWriteback"]["outcome"])

    def test_wrong_repository_memory_route_reports_partial_after_store_success(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        memory_root.mkdir(parents=True)
        result = record_task_learning(
            lesson_payload(memoryRoute={"repository": "web-frontend", "application": "web", "module": "sync", "feature": "replay"}),
            store_path=self.store,
            memory_root=memory_root,
            now=NOW,
        )

        self.assertEqual("partial", result["outcome"])
        self.assertEqual("candidate_pending_review", result["storeOutcome"])
        self.assertEqual("blocked", result["moduleFeatureWriteback"]["outcome"])
        self.assertTrue(self.store.is_file())
        self.assertIn("local durable learning store updated", result["sideEffects"])
        self.assertTrue(result["errors"])

    def test_tenant_scoped_learning_stays_in_scoped_store_without_shared_note(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        memory_root.mkdir(parents=True)
        result = record_task_learning(
            lesson_payload(tenantId="tenant-a", accessScope=["tenant:tenant-a:read"]),
            store_path=self.store,
            memory_root=memory_root,
            now=NOW,
        )

        self.assertEqual("candidate_pending_review", result["outcome"])
        self.assertEqual("scope_restricted", result["moduleFeatureWriteback"]["outcome"])
        self.assertFalse((memory_root / "extensions" / "ad_hoc" / "notes").exists())

    def test_store_directory_fsync_failure_reports_partial_and_real_side_effect(self):
        memory_root = Path(self.temp_dir.name) / "memories"
        memory_root.mkdir(parents=True)
        with patch.object(learning_runtime.os, "fsync", side_effect=[None, OSError("directory fsync failed")]):
            result = record_task_learning(
                lesson_payload(taskId="partial-store"),
                store_path=self.store,
                memory_root=memory_root,
                now=NOW,
            )

        self.assertEqual("partial", result["outcome"])
        self.assertTrue(self.store.is_file())
        self.assertEqual(["local durable learning store updated; durability confirmation failed"], result["sideEffects"])
        self.assertTrue(any("fsync" in error for error in result["errors"]))
        self.assertEqual("not_required", result["moduleFeatureWriteback"]["outcome"])


if __name__ == "__main__":
    unittest.main()
