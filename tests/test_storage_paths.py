from __future__ import annotations

from contextlib import ExitStack, redirect_stderr
from dataclasses import asdict
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

from engine.context_continuity import default_state_root  # noqa: E402
from learning_runtime import default_store_path, main as learning_main, record_task_learning  # noqa: E402
from memory_context_runtime import default_memory_root, lookup_main  # noqa: E402
from progress_checklist import progress_state_path  # noqa: E402
from update_daily_log import validate_worklog_root  # noqa: E402
import storage_paths  # noqa: E402


class StoragePathTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.user_home = self.root / "user"
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(Path, "home", return_value=self.user_home))
        self.stack.enter_context(patch.object(storage_paths.sys, "platform", "linux"))

    def test_fresh_install_uses_linux_application_data_without_creating_directories(self):
        paths = storage_paths.resolve_storage_paths()
        self.assertEqual(self.user_home / ".local/share/buildcrew", paths.home)
        self.assertEqual(paths.home / "memory", paths.memory_root)
        self.assertEqual(paths.memory_root / "autonomous-engineering/learning-store.json", paths.learning_store)
        self.assertTrue(all(path.is_relative_to(paths.home) for path in asdict(paths).values()))
        self.assertFalse(self.user_home.exists())

    def test_linux_respects_absolute_xdg_data_home_and_ignores_relative_values(self):
        os.environ["XDG_DATA_HOME"] = str(self.root / "xdg-data")
        self.assertEqual(self.root / "xdg-data/buildcrew", storage_paths.resolve_storage_paths().home)
        os.environ["XDG_DATA_HOME"] = "relative-data"
        self.assertEqual(
            self.user_home / ".local/share/buildcrew", storage_paths.resolve_storage_paths().home,
        )

    def test_macos_uses_application_support(self):
        with patch.object(storage_paths.sys, "platform", "darwin"):
            self.assertEqual(
                self.user_home / "Library/Application Support/buildcrew",
                storage_paths.resolve_storage_paths().home,
            )

    def test_windows_prefers_local_app_data_then_roaming_then_user_home(self):
        with patch.object(storage_paths.sys, "platform", "win32"):
            os.environ["LOCALAPPDATA"] = str(self.root / "local")
            os.environ["APPDATA"] = str(self.root / "roaming")
            self.assertEqual(self.root / "local/buildcrew", storage_paths.resolve_storage_paths().home)
            del os.environ["LOCALAPPDATA"]
            self.assertEqual(self.root / "roaming/buildcrew", storage_paths.resolve_storage_paths().home)
            del os.environ["APPDATA"]
            self.assertEqual(
                self.user_home / "AppData/Local/buildcrew", storage_paths.resolve_storage_paths().home,
            )

    def test_shared_home_takes_precedence_over_inherited_legacy_settings(self):
        shared = self.root / "shared"
        os.environ.update({
            "BUILDCREW_HOME": str(shared),
            "CODEX_MEMORY_ROOT": str(self.root / "legacy-memory"),
            "AUTONOMOUS_ENGINEERING_LEARNING_STORE": str(self.root / "legacy-store.json"),
            "AUTONOMOUS_ENGINEERING_PROGRESS_ROOT": str(self.root / "legacy-progress"),
            "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT": str(self.root / "legacy-continuity"),
            "CODEX_DAILY_WORKLOG_ROOT": str(self.root / "legacy-worklogs"),
            "CODEX_DAILY_WORKLOG_FALLBACK_ROOT": str(self.root / "legacy-fallback"),
        })
        paths = storage_paths.resolve_storage_paths()
        self.assertEqual(shared, paths.home)
        self.assertTrue(all(path.is_relative_to(shared) for path in asdict(paths).values()))
        self.assertFalse(shared.exists())

    def test_tool_neutral_component_settings_override_shared_home_and_legacy_settings(self):
        shared = self.root / "shared"
        os.environ["BUILDCREW_HOME"] = str(shared)
        settings = {
            "memory_root": ("BUILDCREW_MEMORY_ROOT", "CODEX_MEMORY_ROOT"),
            "learning_store": ("BUILDCREW_LEARNING_STORE", "AUTONOMOUS_ENGINEERING_LEARNING_STORE"),
            "progress_root": ("BUILDCREW_PROGRESS_ROOT", "AUTONOMOUS_ENGINEERING_PROGRESS_ROOT"),
            "continuity_root": ("BUILDCREW_CONTINUITY_ROOT", "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT"),
            "worklog_root": ("BUILDCREW_WORKLOG_ROOT", "CODEX_DAILY_WORKLOG_ROOT"),
            "worklog_fallback_root": ("BUILDCREW_WORKLOG_FALLBACK_ROOT", "CODEX_DAILY_WORKLOG_FALLBACK_ROOT"),
        }
        for name, (neutral, legacy) in settings.items():
            os.environ[neutral] = str(self.root / "neutral" / name)
            os.environ[legacy] = str(self.root / "legacy" / name)
        paths = storage_paths.resolve_storage_paths()
        for name, (neutral, _) in settings.items():
            self.assertEqual(Path(os.environ[neutral]), getattr(paths, name))

    def test_neutral_memory_setting_keeps_store_with_memory_despite_legacy_store(self):
        memory = self.root / "neutral-memory"
        os.environ["BUILDCREW_MEMORY_ROOT"] = str(memory)
        os.environ["AUTONOMOUS_ENGINEERING_LEARNING_STORE"] = str(self.root / "legacy-store.json")
        paths = storage_paths.resolve_storage_paths()
        self.assertEqual(memory, paths.memory_root)
        self.assertEqual(memory / "autonomous-engineering/learning-store.json", paths.learning_store)

    def test_explicit_paths_override_the_shared_home(self):
        os.environ["BUILDCREW_HOME"] = str(self.root / "shared")
        overrides = {
            name: self.root / "explicit" / name
            for name in (
                "memory_root", "learning_store", "progress_root", "continuity_root",
                "worklog_root", "worklog_fallback_root",
            )
        }
        paths = storage_paths.resolve_storage_paths(**overrides)
        for name, path in overrides.items():
            self.assertEqual(path, getattr(paths, name))

    def test_explicit_memory_root_keeps_implicit_learning_store_together(self):
        memory = self.root / "custom-memory"
        os.environ["AUTONOMOUS_ENGINEERING_LEARNING_STORE"] = str(self.root / "old-store.json")
        os.environ["BUILDCREW_LEARNING_STORE"] = str(self.root / "other-store.json")
        expected = memory / "autonomous-engineering/learning-store.json"
        self.assertEqual(expected, default_store_path(memory_root=memory))
        result = record_task_learning(
            {"taskId": "lookup", "intent": "simple_question", "status": "completed"},
            memory_root=memory,
        )
        self.assertEqual(str(expected), result["storePath"])
        self.assertEqual("skipped_simple", result["outcome"])
        self.assertFalse(memory.exists())

    def test_legacy_environment_settings_work_without_shared_home(self):
        settings = {
            "memory_root": "CODEX_MEMORY_ROOT",
            "learning_store": "AUTONOMOUS_ENGINEERING_LEARNING_STORE",
            "progress_root": "AUTONOMOUS_ENGINEERING_PROGRESS_ROOT",
            "continuity_root": "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT",
            "worklog_root": "CODEX_DAILY_WORKLOG_ROOT",
            "worklog_fallback_root": "CODEX_DAILY_WORKLOG_FALLBACK_ROOT",
        }
        for name, variable in settings.items():
            os.environ[variable] = str(self.root / "configured-legacy" / name)
        paths = storage_paths.resolve_storage_paths()
        for name, variable in settings.items():
            self.assertEqual(Path(os.environ[variable]), getattr(paths, name))

    def test_existing_codex_data_requires_an_explicit_legacy_setting(self):
        legacy = self.user_home / ".codex"
        directories = {
            "memory_root": legacy / "memories",
            "progress_root": legacy / "state/autonomous-engineering/progress",
            "continuity_root": legacy / "ae-continuity",
            "worklog_root": legacy / "chokidar",
            "worklog_fallback_root": legacy / "chokidar-fallback",
        }
        for path in directories.values():
            path.mkdir(parents=True)
            (path / "existing-data").write_text("keep", encoding="utf-8")
        paths = storage_paths.resolve_storage_paths()
        self.assertEqual(paths.home / "memory", paths.memory_root)
        self.assertEqual(paths.home / "progress", paths.progress_root)
        self.assertEqual(paths.home / "continuity", paths.continuity_root)
        self.assertEqual(paths.home / "worklogs", paths.worklog_root)
        self.assertFalse(paths.home.exists())

        os.environ.update({
            "CODEX_MEMORY_ROOT": str(directories["memory_root"]),
            "AUTONOMOUS_ENGINEERING_PROGRESS_ROOT": str(directories["progress_root"]),
            "AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT": str(directories["continuity_root"]),
            "CODEX_DAILY_WORKLOG_ROOT": str(directories["worklog_root"]),
            "CODEX_DAILY_WORKLOG_FALLBACK_ROOT": str(directories["worklog_fallback_root"]),
        })
        paths = storage_paths.resolve_storage_paths()
        for name, path in directories.items():
            self.assertEqual(path, getattr(paths, name))
            self.assertEqual("keep", (path / "existing-data").read_text(encoding="utf-8"))
        self.assertEqual(legacy / "memories/autonomous-engineering/learning-store.json", paths.learning_store)
        self.assertFalse(paths.home.exists())

    def test_existing_continuity_directory_can_be_reused_with_neutral_setting(self):
        previous = self.root / "previous-installation" / "continuity"
        previous.mkdir(parents=True)
        marker = previous / "existing-session.json"
        marker.write_text("keep", encoding="utf-8")
        os.environ["AUTONOMOUS_ENGINEERING_CONTINUITY_ROOT"] = str(previous)

        paths = storage_paths.resolve_storage_paths()
        self.assertEqual(previous, paths.continuity_root)
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))
        self.assertFalse(paths.home.exists())

    def test_neutral_storage_is_selected_without_removing_legacy_storage(self):
        legacy = self.user_home / ".codex/memories"
        legacy.mkdir(parents=True)
        neutral = self.user_home / ".local/share/buildcrew/memory"
        neutral.mkdir(parents=True)
        self.assertEqual(neutral, default_memory_root())
        self.assertTrue(legacy.is_dir())

    def test_tool_neutral_worklog_safety_settings_and_legacy_restrictions(self):
        shared = self.root / "shared"
        root = shared / "worklogs"
        os.environ["BUILDCREW_WORKLOG_ALLOWED_ROOTS"] = str(shared)
        self.assertEqual(root, validate_worklog_root(root))

        os.environ["CODEX_WORKLOG_ALLOWED_ROOTS"] = str(self.root / "other")
        with self.assertRaisesRegex(ValueError, "outside configured allowlist"):
            validate_worklog_root(root)
        os.environ["CODEX_WORKLOG_ALLOWED_ROOTS"] = str(shared)
        self.assertEqual(root, validate_worklog_root(root))

        os.environ["BUILDCREW_PROTECTED_ROOTS"] = str(root)
        with self.assertRaisesRegex(ValueError, "protected runtime path"):
            validate_worklog_root(root)
        del os.environ["BUILDCREW_PROTECTED_ROOTS"]
        os.environ["CODEX_PROTECTED_ROOTS"] = str(root)
        with self.assertRaisesRegex(ValueError, "protected runtime path"):
            validate_worklog_root(root)

    def test_relative_shared_home_is_rejected(self):
        os.environ["BUILDCREW_HOME"] = "relative-storage"
        with self.assertRaisesRegex(ValueError, "absolute path"):
            storage_paths.resolve_storage_paths()

    def test_invalid_home_produces_cli_configuration_errors_without_recursive_resolution(self):
        os.environ["BUILDCREW_HOME"] = "relative-storage"
        for main in (lookup_main, learning_main):
            with self.subTest(entrypoint=main.__module__):
                errors = io.StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
                    main(["--input-json", str(self.root / "unused.json")])
                self.assertEqual(2, raised.exception.code)
                self.assertIn("BUILDCREW_HOME must be an absolute path", errors.getvalue())

    def test_runtime_helpers_use_current_configuration_without_import_time_defaults(self):
        for name in ("first", "second"):
            shared = self.root / name
            os.environ["BUILDCREW_HOME"] = str(shared)
            self.assertEqual(shared / "memory", default_memory_root())
            self.assertEqual(shared / "memory/autonomous-engineering/learning-store.json", default_store_path())
            self.assertEqual(shared / "progress", progress_state_path("shared-task").parent)
            self.assertEqual(shared / "continuity", default_state_root())


if __name__ == "__main__":
    unittest.main()
