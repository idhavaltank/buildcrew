from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.global_activation import (  # noqa: E402
    BLOCK_START,
    configure_global_activation,
    rollback_global_activation,
    validate_global_activation,
    verify_global_activation,
)


class GlobalActivationTests(unittest.TestCase):
    def test_install_is_idempotent_and_preserves_existing_global_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            state_root = root / "state"
            codex_home.mkdir()
            instructions = codex_home / "AGENTS.md"
            original = "# Existing global rules\n\nKeep unrelated settings.\n"
            instructions.write_text(original, encoding="utf-8")

            first = configure_global_activation(codex_home=codex_home, state_root=state_root)
            installed = instructions.read_text(encoding="utf-8")
            second = configure_global_activation(codex_home=codex_home, state_root=state_root)

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertIn(original.rstrip(), installed)
            self.assertEqual(installed.count(BLOCK_START), 1)
            self.assertEqual(validate_global_activation(first["state"]), {"valid": True, "errors": []})
            self.assertEqual(verify_global_activation(codex_home=codex_home, state_root=state_root)["status"], "CONFIGURED")

    def test_conflict_is_blocked_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            (codex_home / "AGENTS.md").write_text("# Another single engineering workflow\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                configure_global_activation(codex_home=codex_home, state_root=root / "state")

    def test_rollback_removes_only_the_managed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            state_root = root / "state"
            codex_home.mkdir()
            instructions = codex_home / "AGENTS.md"
            original = "# Existing global rules\n"
            instructions.write_text(original, encoding="utf-8")
            configure_global_activation(codex_home=codex_home, state_root=state_root)

            result = rollback_global_activation(codex_home=codex_home, state_root=state_root)

            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertEqual(instructions.read_text(encoding="utf-8"), original)
            state = json.loads((state_root / "activation" / "global-codex.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "ROLLED_BACK")


if __name__ == "__main__":
    unittest.main()
