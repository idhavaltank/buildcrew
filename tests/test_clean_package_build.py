from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from build_clean_package import (  # noqa: E402
    BuildError,
    MAX_SOURCE_FILE_BYTES,
    _contains_secret_content,
    build_archive,
)


class CleanPackageBuildTests(unittest.TestCase):
    def test_dirty_source_is_rejected_before_archive_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            (root / "README.md").write_text("candidate\n", encoding="utf-8")
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "bad.pyc").write_bytes(b"bytecode")
            (root / "local.log").write_text("secret-like local output\n", encoding="utf-8")
            archive = Path(directory) / "candidate.zip"

            with self.assertRaises(BuildError):
                build_archive(root, archive)
            self.assertFalse(archive.exists())

    def test_archive_must_be_outside_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            with self.assertRaises(BuildError):
                build_archive(root, root / "candidate.zip")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_non_regular_source_is_rejected_before_content_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            (root / "tests").mkdir(parents=True)
            fifo = root / "tests" / "blocking.txt"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(BuildError, "non-regular file"):
                build_archive(root, Path(directory) / "candidate.zip")

    def test_oversized_source_file_is_rejected_before_content_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            oversized = root / "README.md"
            with oversized.open("wb") as stream:
                stream.seek(MAX_SOURCE_FILE_BYTES)
                stream.write(b"x")

            with self.assertRaisesRegex(BuildError, "source file exceeds"):
                build_archive(root, Path(directory) / "candidate.zip")

    def test_report_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            (root / "README.md").write_text("candidate\n", encoding="utf-8")
            report = build_archive(root, Path(directory) / "candidate.zip")
            self.assertEqual(json.loads(json.dumps(report))["status"], "PASS")

    def test_secret_and_unapproved_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            (root / "README.md").write_text("candidate\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=do-not-package\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (root / ".env").unlink()
            secret_value = "hunter2-" + "not-for-package"
            (root / "README.md").write_text("password: " + secret_value + "\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            padded_secret = "live-secret-value-123456789" + "="
            (root / "README.md").write_text("api_key: " + padded_secret + "\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (root / "README.md").write_text("candidate\n", encoding="utf-8")
            local = root / "tests"
            local.mkdir()
            source_secret = "real-" + "credential-9876"
            (local / "fixture.py").write_text("token=" + source_secret + "\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")
            (local / "fixture.py").unlink()

            (local / "review.swp").write_text("editor recovery file\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "review.swp").unlink()
            (local / "local.sqlite").write_bytes(b"runtime state")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "local.sqlite").unlink()
            binary_secret = "real-" + "secret-value" + "-987654321"
            (local / "payload.bin").write_bytes(("api_key=" + binary_secret + "\n").encode("utf-8"))
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "payload.bin").unlink()
            (local / "control.bin").write_bytes(b"candidate\x01payload")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "control.bin").unlink()
            (local / "printable.bin").write_bytes(b"printable unknown suffix\n")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "printable.bin").unlink()
            live_marker = "<" + "live-secret-value-123456789" + ">"
            (local / "placeholder.txt").write_bytes(("api_key=" + live_marker + "\n").encode("utf-8"))
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

            (local / "placeholder.txt").unlink()
            (root / "unrelated.txt").write_text("not part of the package\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_archive(root, Path(directory) / "candidate.zip")

    def test_python_annotations_are_allowed_but_quoted_secrets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review_contracts.py"
            source.write_text(
                "authorization: Mapping[str, object]\n"
                "authorization = trusted_receipts.get(receipt_id)\n",
                encoding="utf-8",
            )
            self.assertFalse(_contains_secret_content(source))

            secret_value = "live-" + "credential-987654321"
            source.write_text(
                'token = "' + secret_value + '"\n',
                encoding="utf-8",
            )
            self.assertTrue(_contains_secret_content(source))

    def test_known_credentials_are_rejected_inside_python_wrapper_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.py"
            credential_values = (
                "sk-" + "proj-" + "A" * 32,
                "xoxb-" + "1234567890-" + "A" * 28,
                "sk_" + "live_" + "A" * 24,
                "AIza" + "A" * 35,
                "ghp_" + "A" * 36,
            )
            for credential in credential_values:
                with self.subTest(prefix=credential[:8]):
                    source.write_text(
                        'token = SecretStr("' + credential + '")\n',
                        encoding="utf-8",
                    )
                    self.assertTrue(_contains_secret_content(source))
