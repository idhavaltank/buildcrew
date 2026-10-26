#!/usr/bin/env python3
"""Build verified, versioned skill and plugin archives from a clean source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

sys.dont_write_bytecode = True

try:
    from build_clean_package import BuildError, _relative_files, build_archive
    from verify_package_parity import compare_package
except ImportError:  # pragma: no cover - package import fallback
    from .build_clean_package import BuildError, _relative_files, build_archive
    from .verify_package_parity import compare_package


VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
REQUIRED_PUBLIC_FILES = ("SKILL.md", "README.md", "LICENSE", "CONTRIBUTING.md", "CHANGELOG.md", "RELEASE.md")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(source: Path) -> dict:
    for name in REQUIRED_PUBLIC_FILES:
        if not (source / name).is_file():
            raise BuildError(f"release source is missing {name}")
    try:
        value = json.loads((source / "config/plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError("release source needs a valid config/plugin.json") from error
    if not isinstance(value, dict) or value.get("name") != source.name:
        raise BuildError("plugin name must match the skill directory name")
    if value.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise BuildError("unsupported portable plugin schema")
    if not isinstance(value.get("version"), str) or not VERSION_PATTERN.fullmatch(value["version"]):
        raise BuildError("plugin version must use major.minor.patch")
    if not isinstance(value.get("description"), str) or not value["description"].strip():
        raise BuildError("plugin description is required")
    if not isinstance(value.get("license"), str) or not value["license"].strip():
        raise BuildError("plugin license identifier is required")
    author = value.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip():
        raise BuildError("plugin author name is required")
    skill_header = (source / "SKILL.md").read_text(encoding="utf-8").splitlines()[:5]
    if f"name: {value['name']}" not in skill_header:
        raise BuildError("SKILL.md frontmatter name must match the plugin name")
    if value["license"] == "MIT" and not (source / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n"):
        raise BuildError("MIT metadata requires the MIT license text")
    return value


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_archive(path: Path, entries: dict[str, bytes]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as package:
        for name, content in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)


def _skill_files(archive: Path, name: str, expected_hashes: dict[str, str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    prefix = f"{name}/"
    with ZipFile(archive) as package:
        for member in package.namelist():
            if not member.startswith(prefix):
                raise BuildError("skill archive has a member outside its package root")
            relative = member[len(prefix):]
            if relative not in expected_hashes or relative in files:
                raise BuildError(f"skill archive has an unexpected or repeated member: {relative}")
            content = package.read(member)
            if hashlib.sha256(content).hexdigest() != expected_hashes[relative]:
                raise BuildError(f"skill archive content changed: {relative}")
            files[relative] = content
    if set(files) != set(expected_hashes):
        raise BuildError("skill archive is missing source files")
    return files


def _plugin_entries(files: dict[str, bytes], manifest: dict, *, claude: bool) -> dict[str, bytes]:
    name = manifest["name"]
    entries = {f"{name}/skills/{name}/{relative}": content for relative, content in files.items()}
    entries[f"{name}/LICENSE"] = files["LICENSE"]
    entries[f"{name}/README.md"] = files["README.md"]
    if claude:
        claude_manifest = {
            "name": name,
            "displayName": "BuildCrew",
            "version": manifest["version"],
            "description": manifest["description"],
            "author": manifest["author"],
            "license": manifest["license"],
        }
        entries[f"{name}/.claude-plugin/plugin.json"] = _json_bytes(claude_manifest)
    else:
        entries[f"{name}/plugin.json"] = files["config/plugin.json"]
    return entries


def _verify_plugin(path: Path, entries: dict[str, bytes]) -> None:
    with ZipFile(path) as package:
        names = package.namelist()
        if len(names) != len(set(names)) or set(names) != set(entries):
            raise BuildError(f"plugin archive has missing, extra, or repeated files: {path.name}")
        for name, expected in entries.items():
            if package.read(name) != expected:
                raise BuildError(f"plugin archive content differs: {name}")


def build_release(source_root: Path, output_dir: Path) -> dict:
    source = source_root.expanduser().resolve(strict=True)
    output = output_dir.expanduser().resolve(strict=False)
    if not source.is_dir() or output == source or source in output.parents:
        raise BuildError("release output must be outside the source directory")
    if output.exists():
        raise BuildError("release output directory already exists")

    _relative_files(source)  # Fail before creating output when local/generated state is present.
    manifest = _manifest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".buildcrew-release-", dir=output.parent))
    try:
        stem = f"{manifest['name']}-v{manifest['version']}"
        skill = staging / f"{stem}-skill.zip"
        portable = staging / f"{stem}-agent-plugin.zip"
        claude = staging / f"{stem}-claude-plugin.zip"

        source_report = build_archive(source, skill)
        parity = compare_package(source, skill)
        if parity["status"] != "PASS":
            raise BuildError("direct skill archive failed source parity")
        files = _skill_files(skill, manifest["name"], source_report["sourceSha256"])
        if json.loads(files["config/plugin.json"]) != manifest:
            raise BuildError("plugin metadata changed during the build")
        for archive, is_claude in ((portable, False), (claude, True)):
            entries = _plugin_entries(files, manifest, claude=is_claude)
            _write_archive(archive, entries)
            _verify_plugin(archive, entries)

        checksums = {path.name: _sha256(path) for path in (skill, portable, claude)}
        (staging / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
            encoding="utf-8",
        )
        report = {
            "status": "PASS",
            "name": manifest["name"],
            "version": manifest["version"],
            "license": manifest["license"],
            "sourceFileCount": source_report["sourceFileCount"],
            "packages": checksums,
            "sourceParity": "PASS",
            "hostExecutionVerified": False,
        }
        (staging / "release-report.json").write_bytes(_json_bytes(report))
        if output.exists():
            raise BuildError("release output directory appeared during the build")
        os.rename(staging, output)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_release(args.source_root, args.output_dir)
    except (BuildError, OSError, ValueError) as error:
        print(json.dumps({"status": "FAIL", "errors": [str(error)]}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
