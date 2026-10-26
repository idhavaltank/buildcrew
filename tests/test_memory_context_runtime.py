from __future__ import annotations

import hashlib
import json
import fcntl
import os
import stat
import sys
import tempfile
from threading import Event
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

import memory_context_runtime
from memory_context_runtime import canonical_memory_lock, lookup_memory_context, queue_module_feature_update


NOW = "2026-09-11T12:00:00Z"


def _lesson_record() -> dict:
    return {
        "schemaVersion": "rag-record-v1",
        "id": "lesson-replay",
        "claim": "Replay acknowledgements are persisted after processing succeeds.",
        "kind": "failure_pattern",
        "scope": "repo",
        "memoryTier": "dynamic_durable",
        "projectId": "general",
        "repository": "backend",
        "branchOrTree": "tree-current",
        "accessScope": ["project:general:read"],
        "fingerprints": {
            "path": "apps/api/sync.py",
            "symbol": "ReplayWorker",
            "taskType": "bug",
        },
        "status": "candidate",
        "confidence": "medium",
        "canonicalKey": "replay-acknowledgement",
        "contentHash": "a" * 64,
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
        "fixStrategy": "Persist after the side effect succeeds.",
        "observedAt": NOW,
        "lastVerifiedAt": None,
        "reviewAt": "2026-10-11T12:00:00Z",
        "expiresAt": "2027-03-10T12:00:00Z",
        "contradictionGroup": None,
        "sensitivity": "none",
    }


class MemoryContextRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.memory_root = Path(self.temporary.name) / "memories"
        self.index_root = self.memory_root / "autonomous-engineering"
        feature_path = self.index_root / "backend" / "sync" / "INDEX.md"
        feature_path.parent.mkdir(parents=True)
        (self.memory_root / "MEMORY.md").write_text("# Global memory\n", encoding="utf-8")
        (self.index_root / "MEMORY.md").write_text("# Evidence-driven agentic engineering memory\n", encoding="utf-8")
        feature_path.write_text("# Replay sync\n", encoding="utf-8")
        (self.index_root / "route-map.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "autonomous-engineering-route-map-v1",
                    "generatedAt": "2026-09-10T00:00:00Z",
                    "retrievalPolicy": {"firstPass": "bounded"},
                    "routes": [
                        {
                            "id": "backend-replay-sync",
                            "repository": "backend",
                            "application": "api",
                            "module": "sync",
                            "match": ["replay acknowledgement", "ReplayWorker"],
                            "initial_memory_paths": ["backend/sync/INDEX.md"],
                            "expand_only_if": ["queue", "database"],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.index_root / "module-registry.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "autonomous-engineering-module-registry-v1",
                    "generatedAt": "2026-09-10T00:00:00Z",
                    "repositories": [
                        {
                            "name": "backend",
                            "path": "backend",
                            "memoryIndex": "backend/INDEX.md",
                            "applications": [
                                {
                                    "name": "api",
                                    "path": "backend/apps/api",
                                    "memoryIndex": "backend/api/INDEX.md",
                                    "modules": ["sync"],
                                }
                            ],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_lookup_uses_shared_canonical_generation_lock(self):
        lock_path = self.memory_root.parent / f".{self.memory_root.name}.canonical-memory.lock"
        with canonical_memory_lock(self.memory_root, exclusive=False):
            self.assertTrue(lock_path.is_file())
            self.assertEqual(0o600, stat.S_IMODE(lock_path.stat().st_mode))
            competing = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(competing)

        competing = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(competing, fcntl.LOCK_UN)
        finally:
            os.close(competing)

    def test_canonical_generation_lock_rejects_symlink(self):
        outside = Path(self.temporary.name) / "outside.lock"
        outside.write_text("", encoding="utf-8")
        lock_path = self.memory_root.parent / f".{self.memory_root.name}.canonical-memory.lock"
        try:
            lock_path.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            with canonical_memory_lock(self.memory_root, exclusive=False):
                self.fail("symlinked lock must not be acquired")

    def test_symlinked_memory_root_is_rejected_before_lock_creation(self):
        alias = self.memory_root.parent / "memory-alias"
        alias.symlink_to(self.memory_root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            with canonical_memory_lock(alias):
                self.fail("symlinked root must not be locked")
        with patch.object(memory_context_runtime, "_load_json") as read:
            result = lookup_memory_context(
                {"taskId": "symlinked-root", "inputText": "ReplayWorker"},
                memory_root=alias, now=NOW,
            )
        read.assert_not_called()
        self.assertEqual("failed", result["outcome"])
        self.assertEqual([], result["selectedPaths"])
        self.assertTrue(result["sourceFirstRequired"])
        self.assertFalse((self.memory_root.parent / ".memories.canonical-memory.lock").exists())
        self.assertFalse((self.memory_root.parent / ".memory-alias.canonical-memory.lock").exists())

    def test_lookup_holds_shared_lock_through_source_receipt_reads(self):
        original = memory_context_runtime._source_receipt
        observed = []

        def check_lock(path, root):
            lock_path = root.parent / f".{root.name}.canonical-memory.lock"
            descriptor = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed.append(path)
            finally:
                os.close(descriptor)
            return original(path, root)

        with patch.object(memory_context_runtime, "_source_receipt", side_effect=check_lock):
            result = lookup_memory_context(
                {"taskId": "locked-read", "inputText": "ReplayWorker"},
                memory_root=self.memory_root, now=NOW,
            )
        self.assertEqual("matched", result["outcome"])
        self.assertEqual(3, len(observed))

    def test_lookup_waits_for_exclusive_generation_publication(self):
        attempted = Event()
        original_flock = fcntl.flock

        def observe_shared_attempt(descriptor, operation):
            if operation == fcntl.LOCK_SH:
                attempted.set()
            return original_flock(descriptor, operation)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with canonical_memory_lock(self.memory_root, exclusive=True):
                with patch.object(memory_context_runtime.fcntl, "flock", side_effect=observe_shared_attempt):
                    future = pool.submit(
                        lookup_memory_context,
                        {"taskId": "publication-read", "inputText": "ReplayWorker"},
                        memory_root=self.memory_root, now=NOW,
                    )
                    self.assertTrue(attempted.wait(timeout=2), "reader must attempt shared lock")
                    self.assertFalse(future.done(), "reader must wait for publication")
                    (self.index_root / "backend/sync/INDEX.md").write_text(
                        "# Published generation\n", encoding="utf-8"
                    )
            result = future.result(timeout=2)
        self.assertEqual("matched", result["outcome"])
        selected = next(item for item in result["sources"] if item["path"].endswith("backend/sync/INDEX.md"))
        self.assertEqual(hashlib.sha256(b"# Published generation\n").hexdigest(), selected["sha256"])

    def test_generation_exchange_preserves_sibling_lock_identity(self):
        with canonical_memory_lock(self.memory_root, exclusive=True) as lock:
            identity = lock.stat().st_ino
            old_generation = self.memory_root / "previous-generation"
            self.index_root.rename(old_generation)
            self.index_root.mkdir()
            self.assertEqual(identity, lock.stat().st_ino)
        with canonical_memory_lock(self.memory_root) as lock:
            self.assertEqual(identity, lock.stat().st_ino)

    def test_invalid_canonical_locks_fail_closed_without_reading_memory(self):
        lock_path = self.memory_root.parent / f".{self.memory_root.name}.canonical-memory.lock"
        outside = self.memory_root.parent / "outside.lock"
        outside.write_text("outside", encoding="utf-8")
        for kind in ("symlink", "hardlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    lock_path.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, lock_path)
                elif kind == "directory":
                    lock_path.mkdir()
                else:
                    os.mkfifo(lock_path)
                try:
                    with patch.object(memory_context_runtime, "_load_json") as read:
                        result = lookup_memory_context(
                            {"taskId": "bad-lock", "inputText": "ReplayWorker"},
                            memory_root=self.memory_root, now=NOW,
                        )
                    read.assert_not_called()
                    self.assertEqual("failed", result["outcome"])
                    self.assertEqual([], result["sources"])
                    self.assertEqual([], result["selectedPaths"])
                    self.assertTrue(result["sourceFirstRequired"])
                    self.assertIn("canonical memory lock unavailable", result["errors"][0])
                    self.assertEqual("outside", outside.read_text(encoding="utf-8"))
                finally:
                    if kind == "directory":
                        lock_path.rmdir()
                    else:
                        lock_path.unlink()

    def test_simple_input_still_gets_bounded_local_memory_route(self):
        result = lookup_memory_context(
            {
                "taskId": "question-1",
                "intent": "simple_question",
                "inputText": "What does ReplayWorker do for replay acknowledgement?",
                "repository": "backend",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("matched", result["outcome"])
        self.assertEqual("backend-replay-sync", result["route"]["routeId"])
        self.assertEqual("sync", result["route"]["module"])
        self.assertEqual(
            ["MEMORY.md", "autonomous-engineering/MEMORY.md", "autonomous-engineering/backend/sync/INDEX.md"],
            result["selectedPaths"],
        )
        self.assertTrue(result["currentSourceRevalidationRequired"])
        self.assertEqual("local_first_provider_optional", result["providerPolicy"])
        self.assertEqual([], result["sideEffects"])
        self.assertNotIn("What does ReplayWorker", json.dumps(result))

    def test_no_route_requires_source_first_without_scanning_memory_tree(self):
        unrelated = self.index_root / "backend" / "unrelated" / "secret.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("must not be scanned\n", encoding="utf-8")
        result = lookup_memory_context(
            {
                "taskId": "feature-1",
                "intent": "feature",
                "inputText": "Add an unrelated export feature",
                "repository": "backend",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("no_relevant_memory", result["outcome"])
        self.assertIsNone(result["route"])
        self.assertEqual(["MEMORY.md", "autonomous-engineering/MEMORY.md"], result["selectedPaths"])
        self.assertNotIn("autonomous-engineering/backend/unrelated/secret.md", result["selectedPaths"])
        self.assertTrue(result["sourceFirstRequired"])

    def test_registry_fallback_reads_only_one_nearest_index(self):
        api_index = self.index_root / "backend" / "api" / "INDEX.md"
        api_index.parent.mkdir(parents=True)
        api_index.write_text("# API modules\n", encoding="utf-8")
        registry_path = self.index_root / "module-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["repositories"][0]["applications"][0]["modules"].append("billing")
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        result = lookup_memory_context(
            {
                "taskId": "billing-question",
                "intent": "simple_question",
                "inputText": "How does invoice rounding work?",
                "repository": "backend",
                "application": "api",
                "module": "billing",
                "feature": "invoice-rounding",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("matched", result["outcome"])
        self.assertEqual("module_registry", result["route"]["routingConfidence"])
        self.assertEqual("billing", result["route"]["module"])
        self.assertEqual(
            ["MEMORY.md", "autonomous-engineering/MEMORY.md", "autonomous-engineering/backend/api/INDEX.md"],
            result["selectedPaths"],
        )

    def test_source_area_only_registry_fallback_accepts_exact_empty_unit_name(self):
        desktop_index = self.index_root / "record" / "desktop" / "INDEX.md"
        package_index = self.index_root / "record" / "packages" / "components" / "INDEX.md"
        desktop_index.parent.mkdir(parents=True)
        package_index.parent.mkdir(parents=True)
        desktop_index.write_text("# Desktop source catalog\n", encoding="utf-8")
        package_index.write_text("# Components source catalog\n", encoding="utf-8")
        registry_path = self.index_root / "module-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["repositories"].append(
            {
                "name": "capture-suite",
                "path": "capture-suite",
                "status": "source-area-only",
                "memoryIndex": "record/INDEX.md",
                "applications": [
                    {
                        "name": "desktop",
                        "path": "capture-suite/apps/desktop",
                        "memoryIndex": "record/desktop/INDEX.md",
                        "modules": [],
                        "sourceAreas": [],
                    }
                ],
                "sharedPackages": [
                    {
                        "name": "components",
                        "path": "capture-suite/packages/components",
                        "memoryIndex": "record/packages/components/INDEX.md",
                        "modules": [],
                    }
                ],
            }
        )
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        for application, module, expected in (
            ("desktop", "desktop", "autonomous-engineering/record/desktop/INDEX.md"),
            ("components", "components", "autonomous-engineering/record/packages/components/INDEX.md"),
        ):
            with self.subTest(application=application):
                result = lookup_memory_context(
                    {
                        "taskId": f"record-{application}",
                        "intent": "simple_question",
                        "inputText": f"Explain Capture {application}",
                        "repository": "capture-suite",
                        "application": application,
                        "module": module,
                    },
                    memory_root=self.memory_root,
                    now=NOW,
                )
                self.assertEqual("matched", result["outcome"])
                self.assertEqual("matched", result["registryStatus"])
                self.assertIn(expected, result["selectedPaths"])

        unknown = lookup_memory_context(
            {
                "taskId": "record-desktop-unknown",
                "intent": "simple_question",
                "inputText": "Explain a missing Capture desktop module",
                "repository": "capture-suite",
                "application": "desktop",
                "module": "unknown-feature",
            },
            memory_root=self.memory_root,
            now=NOW,
        )
        self.assertEqual("no_relevant_memory", unknown["outcome"])

    def test_explicit_repository_and_unit_routes_validate_against_nearest_indexes(self):
        repository_index = self.index_root / "auth" / "repository-context" / "INDEX.md"
        package_index = self.index_root / "auth" / "packages" / "cache" / "INDEX.md"
        repository_index.parent.mkdir(parents=True)
        package_index.parent.mkdir(parents=True)
        repository_index.write_text("# Auth repository context\n", encoding="utf-8")
        package_index.write_text("# Auth cache package\n", encoding="utf-8")
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        route_map["routes"] = [
            {
                "id": "identity-suite-context",
                "repository": "identity-suite",
                "application": "identity-suite",
                "module": "repository-context",
                "match": ["identity-suite", "auth repository context"],
                "initial_memory_paths": ["auth/repository-context/INDEX.md"],
                "expand_only_if": [],
            },
            {
                "id": "identity-suite-cache",
                "repository": "identity-suite",
                "application": "cache",
                "module": "cache",
                "match": ["auth cache package"],
                "initial_memory_paths": ["auth/packages/cache/INDEX.md"],
                "expand_only_if": [],
            },
        ]
        route_map["routeCount"] = len(route_map["routes"])
        route_path.write_text(json.dumps(route_map), encoding="utf-8")
        registry_path = self.index_root / "module-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["repositories"] = [
            {
                "name": "identity-suite",
                "kind": "authentication",
                "memoryIndex": "auth/INDEX.md",
                "applications": [],
                "packages": [
                    {
                        "name": "cache",
                        "memoryIndex": "auth/packages/cache/INDEX.md",
                        "modules": [],
                    }
                ],
            }
        ]
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        for payload, expected in (
            (
                {
                    "taskId": "auth-root-route",
                    "intent": "simple_question",
                    "inputText": "Explain identity-suite repository context",
                    "repository": "identity-suite",
                    "application": "identity-suite",
                    "module": "repository-context",
                },
                "autonomous-engineering/auth/repository-context/INDEX.md",
            ),
            (
                {
                    "taskId": "auth-cache-route",
                    "intent": "simple_question",
                    "inputText": "Explain auth cache package",
                    "repository": "identity-suite",
                    "application": "cache",
                    "module": "cache",
                },
                "autonomous-engineering/auth/packages/cache/INDEX.md",
            ),
        ):
            with self.subTest(task=payload["taskId"]):
                result = lookup_memory_context(payload, memory_root=self.memory_root, now=NOW)
                self.assertEqual("matched", result["outcome"])
                self.assertEqual("matched", result["registryStatus"])
                self.assertIn(expected, result["selectedPaths"])

    def test_source_area_fallback_does_not_infer_unlisted_boundaries(self):
        registry_path = self.index_root / "module-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        index = self.index_root / "backend/api/INDEX.md"
        index.parent.mkdir(parents=True)
        index.write_text("# API catalog\n", encoding="utf-8")
        payload = {
            "taskId": "exact-unit-boundary", "inputText": "Explain the catalog",
            "repository": "backend", "application": "api", "module": "api",
        }
        cases = (
            ({"status": "source-area-only"}, {"modules": [], "sourceAreas": []}, {}, "matched"),
            ({"status": "active"}, {"modules": [], "sourceAreas": []}, {}, "no_relevant_memory"),
            ({"status": "source-area-only"}, {"modules": ["sync"]}, {}, "no_relevant_memory"),
            ({"status": "source-area-only"}, {"modules": [], "sourceAreas": ["sync"]}, {}, "no_relevant_memory"),
            ({"status": "source-area-only"}, {"modules": [], "memoryIndex": None}, {}, "no_relevant_memory"),
            ({"status": "source-area-only"}, {"modules": []}, {"application": "unknown"}, "no_relevant_memory"),
            ({"status": "source-area-only"}, {"modules": []}, {"repository": "unknown"}, "no_relevant_memory"),
        )
        for position, (repository_fields, unit_fields, payload_fields, outcome) in enumerate(cases):
            with self.subTest(case=position):
                current = json.loads(json.dumps(registry))
                current["repositories"][0].update(repository_fields)
                current["repositories"][0]["applications"][0].update(unit_fields)
                registry_path.write_text(json.dumps(current), encoding="utf-8")
                result = lookup_memory_context(dict(payload, **payload_fields), memory_root=self.memory_root, now=NOW)
                self.assertEqual(outcome, result["outcome"])
                if outcome == "no_relevant_memory":
                    self.assertIsNone(result["route"])
                    self.assertNotIn("autonomous-engineering/backend/api/INDEX.md", result["selectedPaths"])

    def test_explicit_nearest_index_routes_require_exact_owner_and_index(self):
        route_path = self.index_root / "route-map.json"
        registry_path = self.index_root / "module-registry.json"
        index = self.index_root / "backend/api/INDEX.md"
        index.parent.mkdir(parents=True)
        index.write_text("# API catalog\n", encoding="utf-8")
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["repositories"][0]["kind"] = "server"
        registry["repositories"][0]["applications"][0]["modules"] = []
        for application, module, has_index, expected in (
            ("api", "api", True, "matched"),
            ("api", "api", False, "failed"),
            ("api", "unknown-module", True, "failed"),
            ("backend", "repository-context", True, "matched"),
            ("backend", "repository-context", False, "failed"),
            ("server", "repository-context", True, "failed"),
        ):
            with self.subTest(application=application, module=module, has_index=has_index):
                current = json.loads(json.dumps(registry))
                owner = current["repositories"][0]["applications"][0] if application == "api" else current["repositories"][0]
                owner["memoryIndex"] = "backend/api/INDEX.md" if has_index else None
                registry_path.write_text(json.dumps(current), encoding="utf-8")
                route_map["routes"] = [{
                    "id": "nearest-owner", "repository": "backend", "application": application,
                    "module": module, "match": ["nearest owner catalog"],
                    "initial_memory_paths": ["backend/api/INDEX.md"], "expand_only_if": [],
                }]
                route_path.write_text(json.dumps(route_map), encoding="utf-8")
                result = lookup_memory_context({
                    "taskId": "nearest-owner", "inputText": "Explain nearest owner catalog",
                    "repository": "backend", "application": application, "module": module,
                }, memory_root=self.memory_root, now=NOW)
                self.assertEqual(expected, result["outcome"])
                self.assertEqual("matched" if expected == "matched" else "module_not_listed", result["registryStatus"])

    def test_conditional_expansion_is_loaded_only_for_matching_boundary_signal(self):
        queue_path = self.index_root / "backend" / "sync" / "QUEUE.md"
        queue_path.write_text("# Queue detail\n", encoding="utf-8")
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        route_map["routes"][0]["expand_only_if"] = ["queue"]
        route_map["routes"][0]["backend_expansion_paths"] = ["backend/sync/QUEUE.md"]
        route_path.write_text(json.dumps(route_map), encoding="utf-8")

        expanded = lookup_memory_context(
            {
                "taskId": "queue-detail",
                "intent": "simple_question",
                "inputText": "How does ReplayWorker use the queue?",
                "repository": "backend",
            },
            memory_root=self.memory_root,
            now=NOW,
        )
        bounded = lookup_memory_context(
            {
                "taskId": "no-queue-detail",
                "intent": "simple_question",
                "inputText": "What does ReplayWorker do?",
                "repository": "backend",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertIn("autonomous-engineering/backend/sync/QUEUE.md", expanded["selectedPaths"])
        self.assertNotIn("autonomous-engineering/backend/sync/QUEUE.md", bounded["selectedPaths"])

    def test_explicit_scope_hard_excludes_cross_repository_route_collision(self):
        wrong_path = self.index_root / "frontend" / "sync" / "INDEX.md"
        wrong_path.parent.mkdir(parents=True)
        wrong_path.write_text("# Wrong repository\n", encoding="utf-8")
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        route_map["routes"].insert(
            0,
            {
                "id": "frontend-replay-sync",
                "repository": "web-frontend",
                "application": "web",
                "module": "sync",
                "match": ["What does ReplayWorker do for replay acknowledgement?"],
                "initial_memory_paths": ["frontend/sync/INDEX.md"],
                "expand_only_if": [],
            },
        )
        route_path.write_text(json.dumps(route_map), encoding="utf-8")

        result = lookup_memory_context(
            {
                "taskId": "scoped-collision",
                "intent": "simple_question",
                "inputText": "What does ReplayWorker do for replay acknowledgement?",
                "repository": "backend",
                "application": "api",
                "module": "sync",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("matched", result["outcome"])
        self.assertEqual("backend-replay-sync", result["route"]["routeId"])
        self.assertNotIn("autonomous-engineering/frontend/sync/INDEX.md", result["selectedPaths"])

    def test_malformed_route_entry_fails_closed(self):
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        route_map["routes"][0]["match"] = "ReplayWorker"
        route_path.write_text(json.dumps(route_map), encoding="utf-8")

        result = lookup_memory_context(
            {"taskId": "bad-route", "intent": "simple_question", "inputText": "ReplayWorker"},
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("invalid", result["outcome"])
        self.assertTrue(result["sourceFirstRequired"])
        self.assertTrue(any("routes[0].match" in error for error in result["errors"]))

    def test_missing_memory_root_returns_unavailable_source_first_receipt(self):
        result = lookup_memory_context(
            {"taskId": "feature-2", "intent": "feature", "inputText": "Add replay support"},
            memory_root=self.memory_root / "missing",
            now=NOW,
        )

        self.assertEqual("unavailable", result["outcome"])
        self.assertTrue(result["sourceFirstRequired"])
        self.assertEqual([], result["selectedPaths"])
        self.assertEqual([], result["sideEffects"])

    def test_malformed_registry_fails_closed_to_source_first(self):
        (self.index_root / "module-registry.json").write_text("[]\n", encoding="utf-8")
        result = lookup_memory_context(
            {"taskId": "bad-registry", "intent": "simple_question", "inputText": "ReplayWorker"},
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("failed", result["outcome"])
        self.assertTrue(result["sourceFirstRequired"])
        self.assertTrue(any("module registry" in error for error in result["errors"]))

    def test_unsafe_route_path_is_rejected_without_reading_outside_memory_root(self):
        route_map = json.loads((self.index_root / "route-map.json").read_text(encoding="utf-8"))
        route_map["routes"][0]["initial_memory_paths"] = ["../outside.md"]
        (self.index_root / "route-map.json").write_text(json.dumps(route_map), encoding="utf-8")

        result = lookup_memory_context(
            {"taskId": "question-2", "intent": "simple_question", "inputText": "ReplayWorker"},
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("invalid", result["outcome"])
        self.assertTrue(result["sourceFirstRequired"])
        self.assertTrue(any("unsafe" in error for error in result["errors"]))
        self.assertNotIn("outside.md", result["selectedPaths"])

    def test_archive_dynamic_store_and_escaping_symlink_are_rejected(self):
        outside = Path(self.temporary.name) / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        escape = self.index_root / "backend" / "sync" / "escape.md"
        try:
            escape.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        for unsafe in ("_archive/old.md", "learning-store.json", "backend/sync/escape.md"):
            with self.subTest(path=unsafe):
                route_map["routes"][0]["initial_memory_paths"] = [unsafe]
                route_path.write_text(json.dumps(route_map), encoding="utf-8")
                result = lookup_memory_context(
                    {"taskId": "unsafe", "intent": "simple_question", "inputText": "ReplayWorker"},
                    memory_root=self.memory_root,
                    now=NOW,
                )
                self.assertEqual("invalid", result["outcome"])
                self.assertTrue(any("unsafe" in error for error in result["errors"]))

    def test_internal_symlink_into_archive_is_rejected(self):
        archive = self.index_root / "_archive" / "hidden.md"
        archive.parent.mkdir(parents=True)
        archive.write_text("archived content\n", encoding="utf-8")
        alias = self.index_root / "backend" / "sync" / "alias.md"
        try:
            alias.symlink_to(archive)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        route_path = self.index_root / "route-map.json"
        route_map = json.loads(route_path.read_text(encoding="utf-8"))
        route_map["routes"][0]["initial_memory_paths"] = ["backend/sync/alias.md"]
        route_path.write_text(json.dumps(route_map), encoding="utf-8")

        result = lookup_memory_context(
            {"taskId": "archive-alias", "intent": "simple_question", "inputText": "ReplayWorker"},
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("invalid", result["outcome"])
        self.assertNotIn("autonomous-engineering/backend/sync/alias.md", result["selectedPaths"])
        self.assertTrue(any("unsafe" in error for error in result["errors"]))

    def test_symlinked_root_index_and_registry_are_rejected(self):
        try:
            probe = Path(self.temporary.name) / "symlink-probe"
            probe.symlink_to(self.memory_root / "MEMORY.md")
            probe.unlink()
        except OSError:
            self.skipTest("symlink creation is unavailable")

        for relative in (Path("MEMORY.md"), Path("autonomous-engineering/module-registry.json")):
            with self.subTest(path=relative.as_posix()):
                target = self.memory_root / relative
                original = target.read_text(encoding="utf-8")
                replacement = target.with_name(target.name + ".real")
                target.rename(replacement)
                target.symlink_to(replacement)
                try:
                    result = lookup_memory_context(
                        {"taskId": "symlinked-index", "intent": "simple_question", "inputText": "ReplayWorker"},
                        memory_root=self.memory_root,
                        now=NOW,
                    )
                    self.assertEqual("invalid", result["outcome"])
                    self.assertTrue(any("unsafe" in error for error in result["errors"]))
                finally:
                    target.unlink()
                    replacement.unlink()
                    target.write_text(original, encoding="utf-8")

    def test_module_feature_writeback_uses_unique_no_clobber_ad_hoc_note(self):
        result = queue_module_feature_update(
            _lesson_record(),
            task_id="task-replay-1",
            route={
                "routeId": "backend-replay-sync",
                "projectId": "general",
                "repository": "backend",
                "application": "api",
                "module": "sync",
                "feature": "replay-acknowledgement",
                "routingConfidence": "explicit",
            },
            memory_root=self.memory_root,
            now=NOW,
        )

        self.assertEqual("queued", result["outcome"])
        note = Path(result["notePath"])
        self.assertTrue(note.is_file())
        self.assertEqual(self.memory_root / "extensions" / "ad_hoc" / "notes", note.parent)
        raw = note.read_text(encoding="utf-8")
        self.assertIn("repository: \"backend\"", raw)
        self.assertIn("access_scope: [\"project:general:read\"]", raw)
        self.assertIn("tenant_id: null", raw)
        self.assertIn("module: \"sync\"", raw)
        self.assertIn("feature: \"replay-acknowledgement\"", raw)
        self.assertIn("status: source_document_candidate_not_canonical", raw)
        self.assertIn("canonical_effect: \"none\"", raw)
        self.assertIn("Replay acknowledgements are persisted", raw)
        self.assertNotIn("rawPrompt", raw)
        self.assertTrue(result["noClobber"])
        self.assertFalse(result["canonicalFilesChanged"])

        repeated = queue_module_feature_update(
            _lesson_record(),
            task_id="task-replay-1",
            route={
                "routeId": "backend-replay-sync",
                "projectId": "general",
                "repository": "backend",
                "application": "api",
                "module": "sync",
                "feature": "replay-acknowledgement",
                "routingConfidence": "explicit",
            },
            memory_root=self.memory_root,
            now=NOW,
        )
        self.assertEqual("already_queued", repeated["outcome"])
        self.assertEqual(result["notePath"], repeated["notePath"])

    def test_existing_different_note_is_never_overwritten(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        first = queue_module_feature_update(
            _lesson_record(), task_id="task-replay-1", route=route, memory_root=self.memory_root, now=NOW
        )
        note = Path(first["notePath"])
        note.write_text("concurrent writer content\n", encoding="utf-8")

        second = queue_module_feature_update(
            _lesson_record(), task_id="task-replay-1", route=route, memory_root=self.memory_root, now=NOW
        )

        self.assertEqual("conflict", second["outcome"])
        self.assertEqual("concurrent writer content\n", note.read_text(encoding="utf-8"))
        self.assertFalse(second["canonicalFilesChanged"])

    def test_concurrent_unique_notes_preserve_every_writer(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }

        def queue(index: int) -> dict:
            return queue_module_feature_update(
                _lesson_record(),
                task_id=f"concurrent-note-{index}",
                route=route,
                memory_root=self.memory_root,
                now=NOW,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(queue, range(16)))

        self.assertEqual({"queued"}, {receipt["outcome"] for receipt in receipts})
        self.assertEqual(16, len({receipt["notePath"] for receipt in receipts}))
        notes = list((self.memory_root / "extensions" / "ad_hoc" / "notes").glob("*.md"))
        self.assertEqual(16, len(notes))

    def test_writeback_rejects_project_or_repository_mismatch(self):
        for key, value in (("projectId", "other-project"), ("repository", "web-frontend")):
            with self.subTest(field=key):
                route = {
                    "routeId": "backend-replay-sync",
                    "projectId": "general",
                    "repository": "backend",
                    "application": "api",
                    "module": "sync",
                    "feature": "replay-acknowledgement",
                    "routingConfidence": "explicit",
                }
                route[key] = value
                result = queue_module_feature_update(
                    _lesson_record(),
                    task_id=f"wrong-{key}",
                    route=route,
                    memory_root=self.memory_root,
                    now=NOW,
                )
                self.assertEqual("blocked", result["outcome"])
                self.assertEqual([], result["sideEffects"])
                self.assertFalse((self.memory_root / "extensions" / "ad_hoc" / "notes").exists())

    def test_tenant_or_non_project_scope_stays_out_of_shared_note_queue(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        restricted_records = (
            dict(_lesson_record(), tenantId="tenant-a"),
            dict(_lesson_record(), accessScope=["tenant:private:read"]),
        )
        for index, record in enumerate(restricted_records):
            with self.subTest(index=index):
                result = queue_module_feature_update(
                    record,
                    task_id=f"restricted-{index}",
                    route=route,
                    memory_root=self.memory_root,
                    now=NOW,
                )
                self.assertEqual("scope_restricted", result["outcome"])
                self.assertEqual(record.get("tenantId"), result["scopeBinding"]["tenantId"])
                self.assertEqual(record["accessScope"], result["scopeBinding"]["accessScope"])
                self.assertEqual([], result["sideEffects"])
        self.assertFalse((self.memory_root / "extensions" / "ad_hoc" / "notes").exists())

    def test_project_prefixed_restricted_scopes_stay_out_of_shared_note_queue(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        restricted_scopes = (
            ["project:general:restricted"],
            ["project:general:private"],
            ["project:general:user:alice"],
            ["project:general:read", "project:general:restricted"],
        )
        for index, access_scope in enumerate(restricted_scopes):
            with self.subTest(access_scope=access_scope):
                record = dict(_lesson_record(), accessScope=access_scope)
                result = queue_module_feature_update(
                    record,
                    task_id=f"restricted-project-scope-{index}",
                    route=route,
                    memory_root=self.memory_root,
                    now=NOW,
                )
                self.assertEqual("scope_restricted", result["outcome"])
                self.assertEqual([], result["sideEffects"])
        self.assertFalse((self.memory_root / "extensions" / "ad_hoc" / "notes").exists())

    def test_retry_at_a_later_time_reuses_the_same_note_identity(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        first = queue_module_feature_update(
            _lesson_record(), task_id="stable-retry", route=route, memory_root=self.memory_root, now=NOW
        )
        second = queue_module_feature_update(
            _lesson_record(),
            task_id="stable-retry",
            route=route,
            memory_root=self.memory_root,
            now="2026-09-11T12:00:01Z",
        )

        self.assertEqual("queued", first["outcome"])
        self.assertEqual("already_queued", second["outcome"])
        self.assertEqual(first["notePath"], second["notePath"])

    def test_directory_fsync_failure_reports_published_note_as_partial(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        with patch.object(memory_context_runtime.os, "fsync", side_effect=[None, OSError("directory fsync failed")]):
            result = queue_module_feature_update(
                _lesson_record(), task_id="partial-note", route=route, memory_root=self.memory_root, now=NOW
            )

        self.assertEqual("partial", result["outcome"])
        self.assertTrue(Path(result["notePath"]).is_file())
        self.assertEqual(["ad-hoc module/feature memory note queued; durability confirmation failed"], result["sideEffects"])
        self.assertTrue(any("fsync" in error for error in result["errors"]))

    def test_temp_cleanup_failure_after_publication_reports_partial_side_effect(self):
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }
        original_unlink = Path.unlink

        def fail_temporary_cleanup(path: Path, *args, **kwargs):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                raise OSError("temporary cleanup failed")
            return original_unlink(path, *args, **kwargs)

        with patch.object(memory_context_runtime.Path, "unlink", new=fail_temporary_cleanup):
            result = queue_module_feature_update(
                _lesson_record(), task_id="partial-cleanup", route=route, memory_root=self.memory_root, now=NOW
            )

        self.assertEqual("partial", result["outcome"])
        self.assertTrue(Path(result["notePath"]).is_file())
        self.assertEqual(["ad-hoc module/feature memory note queued; durability confirmation failed"], result["sideEffects"])
        self.assertTrue(any("cleanup" in error for error in result["errors"]))

    def test_symlinked_extensions_path_is_rejected_before_creating_outside_directories(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        extensions = self.memory_root / "extensions"
        try:
            extensions.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        route = {
            "routeId": "backend-replay-sync",
            "projectId": "general",
            "repository": "backend",
            "application": "api",
            "module": "sync",
            "feature": "replay-acknowledgement",
            "routingConfidence": "explicit",
        }

        result = queue_module_feature_update(
            _lesson_record(), task_id="symlinked-note-root", route=route, memory_root=self.memory_root, now=NOW
        )

        self.assertEqual("blocked", result["outcome"])
        self.assertFalse((outside / "ad_hoc").exists())
        self.assertEqual([], result["sideEffects"])

    def test_lookup_and_writeback_receipts_match_published_schemas(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")

        lookup = lookup_memory_context(
            {"taskId": "schema-lookup", "intent": "simple_question", "inputText": "ReplayWorker"},
            memory_root=self.memory_root,
            now=NOW,
        )
        writeback = queue_module_feature_update(
            _lesson_record(),
            task_id="schema-writeback",
            route={
                "routeId": "backend-replay-sync",
                "projectId": "general",
                "repository": "backend",
                "application": "api",
                "module": "sync",
                "feature": "replay-acknowledgement",
                "routingConfidence": "explicit",
            },
            memory_root=self.memory_root,
            now=NOW,
        )
        contracts = (
            ("memory-context-lookup-v1.schema.json", lookup),
            ("module-feature-memory-writeback-v1.schema.json", writeback),
        )
        for name, receipt in contracts:
            with self.subTest(schema=name):
                schema = json.loads((PACKAGE_ROOT / "schemas" / name).read_text(encoding="utf-8"))
                errors = sorted(Draft202012Validator(schema).iter_errors(receipt), key=lambda item: list(item.path))
                self.assertEqual([], [error.message for error in errors])

        impossible_lookup = dict(lookup)
        impossible_lookup.update(
            outcome="matched",
            route=None,
            selectedPaths=[],
            sources=[],
            registryStatus="unavailable",
            sourceFirstRequired=True,
        )
        impossible_writeback = {
            "schemaVersion": "module-feature-memory-writeback-v1",
            "outcome": "queued",
            "canonicalFilesChanged": False,
            "noClobber": True,
            "errors": [],
            "sideEffects": [],
        }
        lookup_schema = json.loads((PACKAGE_ROOT / "schemas" / "memory-context-lookup-v1.schema.json").read_text(encoding="utf-8"))
        writeback_schema = json.loads((PACKAGE_ROOT / "schemas" / "module-feature-memory-writeback-v1.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(list(Draft202012Validator(lookup_schema).iter_errors(impossible_lookup)))
        self.assertTrue(list(Draft202012Validator(writeback_schema).iter_errors(impossible_writeback)))


if __name__ == "__main__":
    unittest.main()
