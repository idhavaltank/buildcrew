#!/usr/bin/env python3
"""Build a deterministic, source-only AE package archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_NAMES = {".DS_Store"}
FORBIDDEN_DIRS = {
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".venv", "venv", "node_modules", "dist", "build", ".cache",
}
FORBIDDEN_SUFFIX_NAMES = {
    ".log", ".tmp", ".swp", ".swo", ".coverage", ".sqlite", ".sqlite3",
    ".db", ".bak", ".orig", ".rej",
}
ALLOWED_TOP_LEVEL = {
    "SKILL.md", "README.md", "LICENSE", "CONTRIBUTING.md", "CHANGELOG.md",
    "RELEASE.md", "IMPLEMENTATION_REPORT.md", "config", "engine",
    "references", "schemas", "scripts", "tests", "AE-V1-VS-V2-COMPARISON-REPORT.html",
    "AE-V2-SAFE-LAYERS-IMPLEMENTATION-PLAN.md", "AE-V2-HOST-INTEGRATION-IMPLEMENTATION-PLAN.md",
    "AE-V2-PENDING-FEATURES-CHECKLIST.html",
    ".agents", ".codex-plugin", ".github", "docs", ".gitignore", ".nojekyll",
    "AGENTS.md", "CONTRIBUTORS.md", "INSTALL.md", "SECURITY.md", "plugin.json",
    "buildcrew-workflow.html",
}
PUBLIC_REPOSITORY_TOP_LEVEL = {
    ".agents", ".codex-plugin", ".github", "docs", ".gitignore", ".nojekyll",
    "AGENTS.md", "CONTRIBUTORS.md", "INSTALL.md", "SECURITY.md", "plugin.json",
    "buildcrew-workflow.html",
}
SECRET_NAME = re.compile(r"(?i)^(?:\.env(?:\..*)?|credentials(?:\..*)?|secrets(?:\..*)?|id_[a-z0-9_-]+)$")
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
TEXT_SUFFIXES = {
    ".cfg", ".conf", ".ini", ".json", ".cjs", ".js", ".jsx", ".md", ".py", ".pyi",
    ".rst", ".sh", ".toml", ".ts", ".tsx", ".txt", ".html", ".yaml", ".yml",
}
SECRET_CONTENT = re.compile(
    r"(?i)\b(?:aws[_-]?secret[_-]?access[_-]?key|password|token|secret|cookie|dsn|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\b\s*[:=]\s*[\"']?([^\s,;\"'}]+)"
)
PRIVATE_KEY_CONTENT = re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----", re.I)
KNOWN_CREDENTIAL_CONTENT = re.compile(
    r"(?:"
    r"\bAKIA[0-9A-Z]{16}\b"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{20,}\b"
    r"|\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"
    r"|\bAIza[0-9A-Za-z_-]{30,}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r")"
)
SAFE_SECRET_MARKERS = {
    "[REDACTED]", "[REDACTED_EMAIL]", "another-secret", "do-not-forward",
    "do-not-leak", "do-not-package", "do-not-read-this-value", "do-not-show", "not-for-a-report",
    "secret", "secret-value", "super-secret-value", "super-secret-value-123456",
}
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 64 * 1024 * 1024


class BuildError(ValueError):
    """Raised when a source tree is not safe to package."""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_secret_content(path: Path) -> bool:
    if path.suffix.casefold() not in TEXT_SUFFIXES and path.name != "LICENSE":
        return True
    try:
        raw = path.read_bytes()
        if any(byte < 32 and byte not in {9, 10, 13} or byte == 127 for byte in raw):
            return True
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    if PRIVATE_KEY_CONTENT.search(content) or KNOWN_CREDENTIAL_CONTENT.search(content):
        return True
    for match in SECRET_CONTENT.finditer(content):
        raw_value = re.sub(r"\\[nrt]+$", "", match.group(1).strip())
        if (
            path.suffix.casefold() == ".py"
            and not raw_value.startswith(("\"", "'"))
            and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]\n]*\]?|\([^\n]*\)?)",
                raw_value,
            )
        ):
            continue
        value = raw_value.strip("\\\"'")
        if value in SAFE_SECRET_MARKERS:
            continue
        if value.casefold() in {"example", "placeholder", "dummy", "test", "testing"}:
            continue
        if len(value) >= 8:
            return True
    return False


def _relative_files(root: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    files: list[tuple[Path, str]] = []
    rejected: list[str] = []
    total_bytes = 0
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for directory in sorted(directories):
            directory_path = current_path / directory
            relative_directory = directory_path.relative_to(root).as_posix()
            if directory_path.is_symlink():
                raise BuildError(f"source contains symlink directory: {relative_directory}")
            if directory in FORBIDDEN_DIRS:
                raise BuildError(f"source contains generated or repository state directory: {relative_directory}")
            if current_path == root and directory in PUBLIC_REPOSITORY_TOP_LEVEL:
                continue
            if current_path == root and directory not in ALLOWED_TOP_LEVEL:
                raise BuildError(f"unapproved top-level directory: {directory}")
            kept_directories.append(directory)
        directories[:] = kept_directories
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if current_path == root and name in PUBLIC_REPOSITORY_TOP_LEVEL:
                continue
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise BuildError(f"source contains symlink: {relative}")
            if not stat.S_ISREG(metadata.st_mode):
                raise BuildError(f"source contains non-regular file: {relative}")
            if metadata.st_size > MAX_SOURCE_FILE_BYTES:
                raise BuildError(
                    f"source file exceeds {MAX_SOURCE_FILE_BYTES} bytes: {relative}"
                )
            total_bytes += metadata.st_size
            if total_bytes > MAX_SOURCE_TOTAL_BYTES:
                raise BuildError(
                    "source files exceed aggregate safety limit of "
                    f"{MAX_SOURCE_TOTAL_BYTES} bytes"
                )
            if SECRET_NAME.fullmatch(name) or path.suffix.casefold() in SECRET_SUFFIXES:
                raise BuildError(f"source contains a secret-like file name: {relative}")
            if (
                name in FORBIDDEN_NAMES
                or name.casefold() in FORBIDDEN_SUFFIX_NAMES
                or path.suffix in FORBIDDEN_SUFFIXES
                or path.suffix in FORBIDDEN_SUFFIX_NAMES
                or name.endswith("~")
            ):
                raise BuildError(f"source contains generated or local artifact: {relative}")
            if current_path == root and name not in ALLOWED_TOP_LEVEL:
                raise BuildError(f"unapproved top-level file: {relative}")
            if _contains_secret_content(path):
                raise BuildError(f"source contains secret-like content: {relative}")
            parts = PurePosixPath(relative).parts
            if any(part in {"", ".", ".."} for part in parts):
                raise BuildError(f"unsafe source path: {relative}")
            files.append((path, relative))
    return files, rejected


def build_archive(source_root: Path, archive: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise BuildError("source root must be a directory")
    archive = archive.resolve()
    if archive.parent == source_root or source_root in archive.parents:
        raise BuildError("archive must be outside the source root")
    files, rejected = _relative_files(source_root)
    archive.parent.mkdir(parents=True, exist_ok=True)
    root_name = source_root.name
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as package:
        for path, relative in files:
            member = f"{root_name}/{relative}"
            info = ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, path.read_bytes())
    return {
        "schemaVersion": "clean-package-build-v1",
        "status": "PASS",
        "sourceRoot": root_name,
        "sourceFileCount": len(files),
        "archive": archive.name,
        "rejectedLocalArtifacts": rejected,
        "sourceSha256": {relative: _digest(path) for path, relative in files},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_archive(args.source_root, args.archive)
    except (BuildError, OSError) as exc:
        report = {"schemaVersion": "clean-package-build-v1", "status": "FAIL", "errors": [str(exc)]}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
