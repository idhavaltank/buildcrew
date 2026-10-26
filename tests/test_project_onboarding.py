from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.project_onboarding import (  # noqa: E402
    INTRO_MESSAGE,
    ProjectOnboardingError,
    classify_project_state,
    complete_onboarding,
    inspect_onboarding,
    validate_onboarding_state,
)


class ProjectOnboardingTests(unittest.TestCase):
    def test_new_project_is_classified_without_documentation(self):
        with tempfile.TemporaryDirectory() as directory:
            result = classify_project_state(directory)
        self.assertEqual(result["projectState"], "NEW_PROJECT")
        self.assertFalse(result["hasImplementation"])
        self.assertFalse(result["hasDocumentation"])

    def test_existing_project_without_docs_is_classified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            result = classify_project_state(root)
        self.assertEqual(result["projectState"], "EXISTING_WITHOUT_DOCUMENTATION")
        self.assertTrue(result["hasImplementation"])
        self.assertFalse(result["hasDocumentation"])

    def test_existing_project_with_docs_is_classified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "main.ts").write_text("export const ok = true;\n", encoding="utf-8")
            (root / "README.md").write_text("# Project\n", encoding="utf-8")
            result = classify_project_state(root)
        self.assertEqual(result["projectState"], "EXISTING_WITH_DOCUMENTATION")
        self.assertTrue(result["hasImplementation"])
        self.assertTrue(result["hasDocumentation"])

    def test_first_inspection_shows_intro_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            (root / "package.json").write_text("{}\n", encoding="utf-8")

            first = inspect_onboarding(
                root,
                state_root=state,
                project_id="demo",
                host_id="test-host",
                installation_id="install-1",
            )
            second = inspect_onboarding(
                root,
                state_root=state,
                project_id="demo",
                host_id="test-host",
                installation_id="install-1",
            )

        self.assertTrue(first["needsOnboarding"])
        self.assertTrue(first["showIntro"])
        self.assertEqual(first["message"], INTRO_MESSAGE)
        self.assertTrue(second["needsOnboarding"])
        self.assertFalse(second["showIntro"])
        self.assertIsNone(second["message"])

    def test_completion_suppresses_future_intro_and_stores_sanitized_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            inspect_onboarding(root, state_root=state, project_id="demo", host_id="test-host", installation_id="install-1")
            completed = complete_onboarding(
                root,
                state_root=state,
                project_id="demo",
                host_id="test-host",
                installation_id="install-1",
                context={
                    "summary": "Python project with a small command-line entry point.",
                    "decisions": ["Use focused tests before broad tests."],
                    "references": ["src/main.py"],
                    "nextAction": "Confirm the requested feature and acceptance criteria.",
                },
            )
            after = inspect_onboarding(root, state_root=state, project_id="demo", host_id="test-host", installation_id="install-1")
            serialized = json.dumps(completed)

        self.assertEqual(completed["status"], "COMPLETED")
        self.assertFalse(after["needsOnboarding"])
        self.assertFalse(after["showIntro"])
        self.assertNotIn("raw prompt", serialized.casefold())
        self.assertEqual(validate_onboarding_state(completed["state"]), {"valid": True, "errors": []})

    def test_new_installation_gets_its_own_first_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            first = inspect_onboarding(root, state_root=state, project_id="demo", host_id="test-host", installation_id="install-1")
            second_install = inspect_onboarding(root, state_root=state, project_id="demo", host_id="test-host", installation_id="install-2")
        self.assertTrue(first["showIntro"])
        self.assertTrue(second_install["showIntro"])

    def test_each_host_has_its_own_first_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            first = inspect_onboarding(root, state_root=state, project_id="demo", host_id="tool-one", installation_id="install-1")
            other_host = inspect_onboarding(root, state_root=state, project_id="demo", host_id="tool-two", installation_id="install-1")
        self.assertTrue(first["showIntro"])
        self.assertTrue(other_host["showIntro"])

    def test_context_rejects_raw_prompt_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            inspect_onboarding(root, state_root=state, project_id="demo", host_id="test-host", installation_id="install-1")
            with self.assertRaises(ProjectOnboardingError):
                complete_onboarding(
                    root,
                    state_root=state,
                    project_id="demo",
                    host_id="test-host",
                    installation_id="install-1",
                    context={
                        "summary": "summary",
                        "decisions": [],
                        "references": [],
                        "nextAction": "next",
                        "rawPrompt": "must not be persisted",
                    },
                )

    def test_onboarding_schema_is_strict_and_versioned(self):
        schema = json.loads((PACKAGE_ROOT / "schemas" / "project-onboarding-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "project-onboarding-v1.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], "project-onboarding-v1")


if __name__ == "__main__":
    unittest.main()
