from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.readability_gate import (  # noqa: E402
    ReadabilityGateError,
    build_readability_assessment,
    validate_readability_assessment,
)


class ReadabilityGateTests(unittest.TestCase):
    def assessment(self, files, policy=None):
        return build_readability_assessment(
            task_id="readability-task-1",
            source_fingerprint="sha256:" + "a" * 64,
            changed_files=files,
            policy=policy,
        )

    def test_clear_code_passes_and_does_not_persist_source(self):
        result = self.assessment([
            {
                "path": "src/feature.py",
                "content": "def add(left, right):\n    return left + right\n",
            }
        ])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["humanAuthorship"], "NOT_DETERMINABLE")
        self.assertNotIn("return left + right", json.dumps(result))
        self.assertEqual(validate_readability_assessment(result), {"valid": True, "errors": []})

    def test_complex_python_without_explanation_requires_review(self):
        source = "\n".join(
            [
                "def route(value):",
                "    if value:",
                "        for item in value:",
                "            if item.active and item.ready:",
                "                while item.pending:",
                "                    item.pending = False",
                "    return value",
            ]
        ) + "\n"
        result = self.assessment([{"path": "src/router.py", "content": source}], {"complexityCommentThreshold": 3})
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(any(item["rule"] == "non-obvious-logic-needs-explanation" for item in result["findings"]))

    def test_long_lines_trailing_whitespace_and_placeholder_are_reported(self):
        source = "value = '" + ("x" * 140) + "'  \n# TODO: implement cleanup\n"
        result = self.assessment([{"path": "src/example.py", "content": source}])
        rules = {item["rule"] for item in result["findings"]}
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue({"line-too-long", "trailing-whitespace", "unfinished-placeholder"} <= rules)

    def test_python_syntax_error_blocks_closure(self):
        result = self.assessment([{"path": "src/broken.py", "content": "def broken(:\n    pass\n"}])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any(item["rule"] == "syntax-error" and item["severity"] == "ERROR" for item in result["findings"]))

    def test_unknown_language_requires_explicit_review(self):
        result = self.assessment([{"path": "src/feature.unknown", "content": "if ready { run(); }\n"}])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(any(item["rule"] == "language-coverage" for item in result["findings"]))

    def test_empty_changed_file_set_blocks(self):
        result = self.assessment([])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["findings"][0]["rule"], "missing-changed-files")

    def test_invalid_path_is_blocked_without_echoing_source(self):
        result = self.assessment([{"path": "../secret.py", "content": "password = 'do-not-show'\n"}])
        self.assertEqual(result["status"], "BLOCKED")
        serialized = json.dumps(result)
        self.assertNotIn("do-not-show", serialized)
        self.assertNotIn("../secret.py", serialized)

    def test_policy_rejects_unknown_or_invalid_fields(self):
        with self.assertRaises(ReadabilityGateError):
            self.assessment([{"path": "src/a.py", "content": "x = 1\n"}], {"unknown": 1})
        with self.assertRaises(ReadabilityGateError):
            self.assessment([{"path": "src/a.py", "content": "x = 1\n"}], {"maxLineLength": 0})

    def test_schema_is_strict_and_versioned(self):
        schema = json.loads((PACKAGE_ROOT / "schemas" / "readability-assessment-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "readability-assessment-v1.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["humanAuthorship"]["const"], "NOT_DETERMINABLE")

    def test_cli_help_is_available(self):
        import subprocess

        completed = subprocess.run(
            [sys.executable, "scripts/validate_readability.py", "--help"],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("readability", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
