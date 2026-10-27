#!/usr/bin/env python3
"""Compare the live source directory with the files in a standalone ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


PUBLIC_REPOSITORY_TOP_LEVEL = {
    ".agents", ".codex-plugin", ".github", "docs", ".claude", ".gitignore", ".nojekyll",
    "AGENTS.md", "CONTRIBUTORS.md", "INSTALL.md", "SECURITY.md", "plugin.json",
    "buildcrew-workflow.html",
}


FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_MEMBER_NAME_BYTES = 512


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(root: Path) -> tuple[dict[str, str], list[str], list[str]]:
    files: dict[str, str] = {}
    forbidden: list[str] = []
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] in PUBLIC_REPOSITORY_TOP_LEVEL:
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            errors.append(f"source contains symlink: {relative}")
            continue
        if path.is_dir():
            continue
        if not stat.S_ISREG(metadata.st_mode):
            errors.append(f"source contains non-regular file: {relative}")
            continue
        if "__pycache__" in path.parts or path.suffix in FORBIDDEN_SUFFIXES:
            forbidden.append(relative)
            continue
        files[relative] = _digest(path)
    return files, forbidden, errors


def _archive_files(archive: Path, root_name: str) -> tuple[dict[str, str], list[str], list[str]]:
    files: dict[str, str] = {}
    forbidden: list[str] = []
    errors: list[str] = []
    seen_members: set[str] = set()
    seen_casefolded_members: dict[str, str] = {}
    prefix = root_name.strip("/") + "/"
    try:
        with ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                return {}, [], [
                    f"archive member count exceeds {MAX_ARCHIVE_MEMBERS}"
                ]
            declared_total = sum(info.file_size for info in members)
            if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                return {}, [], [
                    "archive declared uncompressed size exceeds "
                    f"{MAX_TOTAL_UNCOMPRESSED_BYTES} bytes"
                ]
            observed_total = 0
            for info in members:
                name = info.filename
                path = PurePosixPath(name)
                if (
                    "\x00" in name
                    or "\\" in name
                    or len(name.encode("utf-8")) > MAX_MEMBER_NAME_BYTES
                    or path.is_absolute()
                    or ".." in path.parts
                    or any(part.endswith((" ", ".")) for part in path.parts)
                ):
                    errors.append(f"unsafe archive member: {name!r}")
                    continue
                if name in seen_members:
                    errors.append(f"duplicate archive member: {name!r}")
                    continue
                seen_members.add(name)
                casefolded_name = unicodedata.normalize("NFC", name).casefold()
                previous_name = seen_casefolded_members.get(casefolded_name)
                if previous_name is not None and previous_name != name:
                    errors.append(
                        "case-colliding archive members: "
                        f"{previous_name!r} and {name!r}"
                    )
                    continue
                seen_casefolded_members[casefolded_name] = name
                if info.flag_bits & 0x1:
                    errors.append(f"encrypted archive member: {name!r}")
                    continue
                if info.file_size > MAX_MEMBER_UNCOMPRESSED_BYTES:
                    errors.append(f"oversized archive member: {name!r}")
                    continue
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    errors.append(f"excessive compression ratio: {name!r}")
                    continue
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o177777
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG}:
                    errors.append(f"non-regular archive member: {name!r}")
                    continue
                if not name.startswith(prefix):
                    errors.append(f"archive member outside package root: {name!r}")
                    continue
                relative = name[len(prefix):]
                if not relative:
                    errors.append("archive contains an empty package-relative path")
                    continue
                if "__pycache__" in PurePosixPath(relative).parts or Path(relative).suffix in FORBIDDEN_SUFFIXES:
                    forbidden.append(relative)
                    continue
                with package.open(info) as stream:
                    digest = hashlib.sha256()
                    observed_member = 0
                    resource_limit_hit = False
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        observed_member += len(chunk)
                        observed_total += len(chunk)
                        if observed_member > MAX_MEMBER_UNCOMPRESSED_BYTES:
                            errors.append(f"oversized archive member while reading: {name!r}")
                            resource_limit_hit = True
                            break
                        if observed_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            errors.append("archive uncompressed bytes exceed safety limit")
                            resource_limit_hit = True
                            break
                        digest.update(chunk)
                    if resource_limit_hit:
                        continue
                files[relative] = digest.hexdigest()
    except (OSError, BadZipFile) as exc:
        errors.append(f"archive read failed: {exc}")
    return files, forbidden, errors


def compare_package(source_root: Path, archive: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    source, source_forbidden, source_errors = _source_files(source_root)
    archived, archive_forbidden, errors = _archive_files(archive.resolve(), source_root.name)
    errors.extend(source_errors)
    missing = sorted(set(source) - set(archived))
    extra = sorted(set(archived) - set(source))
    mismatched = sorted(
        name for name in set(source) & set(archived) if source[name] != archived[name]
    )
    errors.extend(f"source contains forbidden generated file: {name}" for name in source_forbidden)
    errors.extend(f"archive contains forbidden generated file: {name}" for name in archive_forbidden)
    errors.extend(f"missing from archive: {name}" for name in missing)
    errors.extend(f"extra in archive: {name}" for name in extra)
    errors.extend(f"content mismatch: {name}" for name in mismatched)
    return {
        "schemaVersion": "package-parity-v1",
        "status": "PASS" if not errors else "FAIL",
        "sourceRootName": source_root.name,
        "fileCount": len(source),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "errors": errors,
        "sideEffects": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.source_root.is_dir():
        parser.error("--source-root must be a directory")
    report = compare_package(args.source_root, args.archive)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
