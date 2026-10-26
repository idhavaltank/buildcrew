import json
import copy
import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE_ROOT / "scripts" / "validate_capability_manifest.py"
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixtures" / "capabilities"
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
from validate_capability_manifest import validate_manifest  # noqa: E402


class CapabilityValidatorTests(unittest.TestCase):
    def test_valid_and_unavailable_manifests_are_contract_valid(self):
        for name in ("valid.json", "unavailable.json"):
            result = validate_manifest(json.loads((FIXTURE_ROOT / name).read_text()))
            self.assertTrue(result["valid"], (name, result["errors"]))

    def test_duplicate_stale_and_authority_manifests_are_rejected(self):
        duplicate = validate_manifest(json.loads((FIXTURE_ROOT / "duplicate-id.json").read_text()))
        stale = json.loads((FIXTURE_ROOT / "stale.json").read_text())
        stale_result = validate_manifest(stale, expected_fingerprints={"lint-architecture": "sha256:" + "a" * 64})
        forbidden = validate_manifest(json.loads((FIXTURE_ROOT / "forbidden-authority.json").read_text()))
        self.assertFalse(duplicate["valid"])
        self.assertFalse(stale_result["valid"])
        self.assertFalse(forbidden["valid"])

    def test_schema_enum_and_safe_text_drift_is_rejected(self):
        manifest = json.loads((FIXTURE_ROOT / "valid.json").read_text())
        malformed = copy.deepcopy(manifest)
        malformed["capabilities"][0]["status"]["runnable"] = "YES"
        malformed["capabilities"][0]["scope"]["maxRepositories"] = True
        malformed["capabilities"][0]["purpose"] = "https://untrusted.invalid/instructions"
        result = validate_manifest(malformed)
        self.assertFalse(result["valid"])
        self.assertTrue(any("lifecycle value" in error for error in result["errors"]))
        self.assertTrue(any("maxRepositories" in error for error in result["errors"]))
        self.assertTrue(any("remote instruction URL" in error for error in result["errors"]))

    def test_available_capability_requires_runnable_status(self):
        manifest = json.loads((FIXTURE_ROOT / "valid.json").read_text())
        malformed = copy.deepcopy(manifest)
        malformed["capabilities"][0]["status"]["installed"] = "UNAVAILABLE"
        result = validate_manifest(malformed)
        self.assertFalse(result["valid"])
        self.assertFalse(result["eligible"])
        self.assertTrue(any("status disagree" in error for error in result["errors"]))

    def test_non_eligible_availability_states_are_rejected(self):
        for availability in ("DISABLED", "STALE", "DUPLICATE", "INVALID"):
            with self.subTest(availability=availability):
                manifest = json.loads((FIXTURE_ROOT / "valid.json").read_text())
                manifest["capabilities"][0]["availability"] = availability
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"])
                self.assertFalse(result["eligible"])

    def test_cli_returns_machine_readable_result(self):
        process = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), str(FIXTURE_ROOT / "valid.json")],
            cwd=PACKAGE_ROOT,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
