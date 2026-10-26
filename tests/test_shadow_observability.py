import json
import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "operator-status-v1.schema.json"
sys.path.insert(0, str(SCRIPT_ROOT))

from shadow_observability import (  # noqa: E402
    OperatorStatusError,
    build_operator_status,
    validate_shadow_report,
)


def passing_report():
    return {
        "taskId": "task-observe-1",
        "authority": "buildcrew",
        "fingerprint": "sha256:fixture",
        "planVersion": "plan-5",
        "writer": None,
        "blockers": [],
        "nextAction": "retain shadow-only operation",
        "candidateStatus": "CANDIDATE_UNAVAILABLE",
        "shadowStatus": "PASS",
        "staticStatus": "PASS",
        "results": [{
            "reconciliation": {"final": {
                "route": "NEW_TASK",
                "complexity": "NORMAL",
                "riskTier": "LOW",
            }},
            "expectation": {"passed": True},
        }],
    }


class ShadowObservabilityTests(unittest.TestCase):
    def test_passing_status_keeps_unverified_boundaries_explicit(self):
        status = build_operator_status(passing_report())

        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["route"], "NEW_TASK")
        self.assertEqual(status["staticStatus"], "PASS")
        self.assertEqual(status["runtimeStatus"], "UNAVAILABLE")
        self.assertEqual(status["deviceStatus"], "UNAVAILABLE")
        self.assertEqual(status["activationStatus"], "NOT_STARTED")
        self.assertEqual(status["sideEffects"], [])

    def test_shadow_side_effects_fail_closed(self):
        report = passing_report()
        report["sideEffects"] = ["write receipt"]

        with self.assertRaises(OperatorStatusError) as raised:
            build_operator_status(report)

        self.assertIn("side effects", str(raised.exception))
        self.assertFalse(validate_shadow_report(report)["valid"])

    def test_missing_required_field_fails_closed(self):
        report = passing_report()
        del report["planVersion"]

        with self.assertRaises(OperatorStatusError) as raised:
            build_operator_status(report)

        self.assertIn("planVersion", str(raised.exception))

    def test_invalid_status_is_rejected(self):
        report = passing_report()
        report["runtimeStatus"] = "SUCCESS_BY_INFERENCE"

        with self.assertRaises(OperatorStatusError):
            build_operator_status(report)

    def test_credentials_are_redacted_in_errors_and_status_evidence(self):
        report = passing_report()
        report["blockers"] = ["token=super-secret-value"]
        report["nextAction"] = "inspect password=another-secret"

        status = build_operator_status(report)
        rendered = json.dumps(status)

        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("another-secret", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_schema_declares_all_stable_fields(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue({
            "taskId", "route", "complexity", "riskTier", "authority", "fingerprint",
            "planVersion", "writer", "staticStatus", "reviewStatus", "runtimeStatus",
            "deviceStatus", "publicationStatus", "activationStatus", "learningStatus",
            "blockers", "nextAction", "candidateStatus", "shadowStatus", "sideEffects",
        }.issubset(required))


if __name__ == "__main__":
    unittest.main()
