from __future__ import annotations

from pathlib import Path
import sys
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from scripts.global_input_adapter import IntakeError, sanitize_intake  # noqa: E402


class GlobalInputAdapterTests(unittest.TestCase):
    def test_sanitizes_host_intake_to_routing_fields(self):
        result = sanitize_intake(
            {
                "requestType": " implementation ",
                "actionable": True,
                "objective": " Add the feature ",
                "summary": "token=super-secret-value",
                "repository": "/tmp/project",
                "prompt": "raw user prompt must not pass through",
                "model": "secret-model-selection",
            }
        )
        self.assertEqual(result["requestType"], "IMPLEMENTATION")
        self.assertTrue(result["actionable"])
        self.assertNotIn("prompt", result)
        self.assertNotIn("model", result)
        self.assertEqual(result["summary"], "token=[REDACTED]")

    def test_rejects_authority_bearing_or_invalid_fields(self):
        with self.assertRaises(IntakeError):
            sanitize_intake({"requestType": "QUESTION", "actionable": "yes"})
        with self.assertRaises(IntakeError):
            sanitize_intake({"requestType": "QUESTION", "objective": "\x00unsafe"})


if __name__ == "__main__":
    unittest.main()
