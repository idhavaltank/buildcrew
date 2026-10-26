from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import host_integration_status  # noqa: E402


class HostStatusTests(unittest.TestCase):
    def _run(self, arguments: list[str]) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = host_integration_status.main(["--json", *arguments])
        self.assertEqual(0, result)
        return json.loads(output.getvalue())

    def test_detected_browser_and_adb_are_not_reported_as_missing_runtimes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            with patch.object(host_integration_status, "_playwright_detected", return_value=True):
                status = self._run([
                    "--playwright-node-modules", str(root),
                    "--browser-executable", "/bin/sh",
                    "--adb-path", "/bin/sh",
                    "--flutter-path", "/bin/sh",
                    "--xcrun-path", "/path/that/does/not/exist",
                ])
            self.assertEqual("AVAILABLE", status["browserAdapter"]["status"])
            self.assertFalse(status["browserAdapter"]["executionReady"])
            self.assertEqual("AVAILABLE", status["androidAdapter"]["status"])
            self.assertFalse(status["androidAdapter"]["executionReady"])
            self.assertEqual("AVAILABLE", status["flutterRuntime"]["status"])
            self.assertTrue(status["flutterRuntime"]["runtimeDetected"])
            self.assertFalse(status["flutterRuntime"]["executionReady"])
            self.assertEqual("UNAVAILABLE", status["iosAdapter"]["status"])

    def test_explicit_task_configuration_sets_execution_ready_without_claiming_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            with patch.object(host_integration_status, "_playwright_detected", return_value=True):
                status = self._run([
                    "--playwright-node-modules", str(root),
                    "--browser-executable", "/bin/sh",
                    "--browser-origin", "https://example.test",
                    "--browser-artifacts-dir", str(artifacts),
                    "--adb-path", "/bin/sh",
                    "--android-serial", "emulator-5554",
                    "--android-artifacts-dir", str(artifacts),
                    "--flutter-path", "/bin/sh",
                    "--flutter-project-root", str(root),
                    "--flutter-device", "flutter-device",
                    "--flutter-artifacts-dir", str(artifacts),
                    "--xcrun-path", "/bin/sh",
                    "--ios-udid", "ios-device",
                    "--ios-artifacts-dir", str(artifacts),
                ])
            self.assertTrue(status["browserAdapter"]["executionReady"])
            self.assertTrue(status["androidAdapter"]["executionReady"])
            self.assertTrue(status["flutterRuntime"]["executionReady"])
            self.assertTrue(status["iosAdapter"]["executionReady"])
            self.assertEqual("CONFIGURATION_REQUIRED", status["runtimeCoordinator"]["status"])
            self.assertEqual("CONFIGURATION_REQUIRED", status["modelProvider"]["status"])
            self.assertEqual("CONFIGURATION_REQUIRED", status["nativeAgentBridge"]["status"])
            self.assertEqual("CONFIGURATION_REQUIRED", status["installation"]["status"])
            self.assertEqual("CONFIGURATION_REQUIRED", status["ragProvider"]["status"])


if __name__ == "__main__":
    unittest.main()
