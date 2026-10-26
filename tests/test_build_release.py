from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

from build_release import BuildError, build_release  # noqa: E402
from verify_package_parity import compare_package  # noqa: E402


class ReleaseBuildTests(unittest.TestCase):
    def source(self, parent: Path) -> Path:
        source = parent / "buildcrew"
        (source / "config").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: buildcrew\ndescription: Test skill.\n---\n\n# Test skill\n",
            encoding="utf-8",
        )
        for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "RELEASE.md"):
            (source / name).write_text(f"# {name}\n", encoding="utf-8")
        (source / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 Dhaval Tank\n", encoding="utf-8")
        (source / "config/plugin.json").write_text(json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "buildcrew",
            "version": "0.1.0",
            "description": "Test engineering skill",
            "author": {"name": "Dhaval Tank"},
            "license": "MIT",
        }), encoding="utf-8")
        return source

    def test_release_has_three_matching_reproducible_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            first = root / "first"
            second = root / "second"
            report = build_release(source, first)
            repeated = build_release(source, second)

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["packages"], repeated["packages"])
            self.assertFalse(report["hostExecutionVerified"])
            self.assertEqual(json.loads((first / "release-report.json").read_text()), report)
            self.assertEqual(3, len((first / "SHA256SUMS").read_text().splitlines()))

            stem = "buildcrew-v0.1.0"
            skill = first / f"{stem}-skill.zip"
            self.assertEqual("PASS", compare_package(source, skill)["status"])
            for filename, manifest_path in (
                (f"{stem}-agent-plugin.zip", "buildcrew/plugin.json"),
                (f"{stem}-claude-plugin.zip", "buildcrew/.claude-plugin/plugin.json"),
            ):
                with self.subTest(filename=filename), ZipFile(first / filename) as package:
                    names = package.namelist()
                    self.assertEqual(len(names), len(set(names)))
                    self.assertIn(manifest_path, names)
                    self.assertIn("buildcrew/LICENSE", names)
                    self.assertEqual(
                        (source / "SKILL.md").read_bytes(),
                        package.read("buildcrew/skills/buildcrew/SKILL.md"),
                    )
                    self.assertEqual("buildcrew", json.loads(package.read(manifest_path))["name"])

    def test_generated_state_blocks_release_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            cache = source / "scripts/__pycache__"
            cache.mkdir(parents=True)
            (cache / "bad.pyc").write_bytes(b"bytecode")
            output = root / "release"
            with self.assertRaisesRegex(BuildError, "generated or repository state"):
                build_release(source, output)
            self.assertFalse(output.exists())

    def test_manifest_and_license_must_be_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            metadata = source / "config/plugin.json"
            manifest = json.loads(metadata.read_text(encoding="utf-8"))
            manifest["name"] = "unrelated-plugin"
            metadata.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "match the skill directory"):
                build_release(source, root / "release")
            self.assertFalse((root / "release").exists())

            manifest["name"] = "buildcrew"
            metadata.write_text(json.dumps(manifest), encoding="utf-8")
            (source / "LICENSE").write_text("Other license text\n", encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "MIT license text"):
                build_release(source, root / "release")
            self.assertFalse((root / "release").exists())

    def test_existing_release_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            output = root / "release"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("existing release", encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "already exists"):
                build_release(source, output)
            self.assertEqual("existing release", marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
