import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import verify_package_parity  # noqa: E402
from verify_package_parity import compare_package  # noqa: E402


class PackageParityTests(unittest.TestCase):
    def make_archive(self, source: Path, archive: Path, *, mutate: bool = False) -> None:
        with ZipFile(archive, "w", ZIP_DEFLATED) as package:
            for path in source.rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    if mutate and path.name == "README.md":
                        data += b"changed"
                    package.writestr(
                        f"{source.name}/{path.relative_to(source).as_posix()}",
                        data,
                    )

    def test_source_and_archive_parity_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "run.py").write_text("pass\n", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "PASS", report["errors"])
        self.assertEqual(report["fileCount"], 2)
        self.assertEqual(report["sideEffects"], [])

    def test_content_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive, mutate=True)
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("README.md", report["mismatched"])

    def test_archive_bytecode_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            with ZipFile(archive, "a", ZIP_DEFLATED) as package:
                package.writestr(f"{root.name}/scripts/__pycache__/bad.pyc", b"bytecode")
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("forbidden generated" in error for error in report["errors"]))

    def test_duplicate_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            with ZipFile(archive, "a", ZIP_DEFLATED) as package:
                package.writestr(f"{root.name}/README.md", b"replacement")
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("duplicate archive member" in error for error in report["errors"]))

    def test_case_colliding_archive_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            with ZipFile(archive, "a", ZIP_DEFLATED) as package:
                package.writestr(f"{root.name}/readme.md", b"collision")
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("case-colliding archive members" in error for error in report["errors"]))

    def test_symlink_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            link = ZipInfo(f"{root.name}/linked-readme")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with ZipFile(archive, "a", ZIP_DEFLATED) as package:
                package.writestr(link, b"README.md")
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("non-regular archive member" in error for error in report["errors"]))

    def test_source_symlink_is_rejected_instead_of_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            outside = Path(temporary) / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            (root / "README.md").symlink_to(outside)
            archive = Path(temporary) / "package.zip"
            with ZipFile(archive, "w", ZIP_DEFLATED) as package:
                package.writestr(f"{root.name}/README.md", b"outside")
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("source contains symlink" in error for error in report["errors"]))

    def test_encrypted_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            raw = bytearray(archive.read_bytes())
            local_header = raw.index(b"PK\x03\x04")
            central_header = raw.index(b"PK\x01\x02")
            local_flags = int.from_bytes(raw[local_header + 6 : local_header + 8], "little") | 0x1
            central_flags = int.from_bytes(raw[central_header + 8 : central_header + 10], "little") | 0x1
            raw[local_header + 6 : local_header + 8] = local_flags.to_bytes(2, "little")
            raw[central_header + 8 : central_header + 10] = central_flags.to_bytes(2, "little")
            archive.write_bytes(raw)
            report = compare_package(root, archive)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("encrypted archive member" in error for error in report["errors"]))

    def test_archive_member_count_limit_fails_before_decompression(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            (root / "OTHER.md").write_text("other", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            with patch.object(verify_package_parity, "MAX_ARCHIVE_MEMBERS", 1):
                report = compare_package(root, archive)
        self.assertEqual("FAIL", report["status"])
        self.assertTrue(any("member count" in error for error in report["errors"]))

    def test_backslash_and_trailing_dot_member_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "buildcrew"
            root.mkdir()
            (root / "README.md").write_text("package", encoding="utf-8")
            archive = Path(temporary) / "package.zip"
            self.make_archive(root, archive)
            with ZipFile(archive, "a", ZIP_DEFLATED) as package:
                package.writestr(f"{root.name}\\escape.py", b"unsafe")
                package.writestr(f"{root.name}/trailing./value.py", b"unsafe")
            report = compare_package(root, archive)
        self.assertEqual("FAIL", report["status"])
        self.assertGreaterEqual(
            sum("unsafe archive member" in error for error in report["errors"]), 2
        )


if __name__ == "__main__":
    unittest.main()
