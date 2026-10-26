#!/usr/bin/env python3
"""Rehearse a safe skill-package promotion without touching live host state.

The archive is read-only input. Extraction, package testing, simulated configuration,
activation, rollback, and all rendered receipts exist only inside one temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from zipfile import BadZipFile, ZipFile, ZipInfo


APPROVAL_TOKEN = "APPROVE_REHEARSAL_ACTIVATION"
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
STAGES = (
    "candidate-validation",
    "installation",
    "active-configuration",
    "activation",
    "rollback",
)
ABSOLUTE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:[\\/]|/(?:home|tmp|var|Users|opt|mnt|media|private|workspace)/)[^\s\"']+)"
)
SECRET_VALUE = re.compile(
    r"(?i)(\b(?:password|token|secret|cookie|dsn|api[-_ ]?key)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+"
)
BEARER_VALUE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}")
EMAIL_VALUE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class PromotionError(ValueError):
    """A safe rehearsal precondition was not met."""


@lru_cache(maxsize=1)
def sandbox_available() -> bool:
    """Return whether this host can start the required isolated namespace."""

    sandbox = shutil.which("bwrap")
    if sandbox is None:
        return False
    probe = [
        sandbox,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--clearenv",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--",
        "/usr/bin/true",
    ]
    try:
        result = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact(value: str) -> str:
    value = ABSOLUTE_PATH.sub("[HOST_PATH]", value)
    value = SECRET_VALUE.sub(r"\1[REDACTED]", value)
    value = BEARER_VALUE.sub(r"\1[REDACTED]", value)
    return EMAIL_VALUE.sub("[REDACTED_EMAIL]", value)


def _safe_member_name(info: ZipInfo) -> str:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise PromotionError("archive contains an empty, NUL, or backslash member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PromotionError(f"archive contains an unsafe member path: {name!r}")
    return "/".join(path.parts)


def _is_symlink(info: ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _is_unsupported_special_file(info: ZipInfo) -> bool:
    mode = info.external_attr >> 16
    if not mode:
        return False
    kind = stat.S_IFMT(mode)
    return kind not in {0, stat.S_IFREG, stat.S_IFDIR}


def validate_archive(archive: Path) -> tuple[list[tuple[ZipInfo, str]], dict[str, Any]]:
    """Return safe regular-file members and non-sensitive validation facts."""

    if not archive.is_file():
        raise PromotionError("archive path is not a readable file")
    try:
        with ZipFile(archive) as package:
            infos = package.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise PromotionError("archive has too many members for a rehearsal")
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise PromotionError("archive is too large for a disposable rehearsal")

            files: list[tuple[ZipInfo, str]] = []
            names: set[str] = set()
            casefolded: set[str] = set()
            for info in infos:
                name = _safe_member_name(info)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise PromotionError(f"archive member exceeds size limit: {name}")
                if _is_symlink(info) or _is_unsupported_special_file(info):
                    raise PromotionError(f"archive contains a link or special file: {name}")
                if name in names or name.casefold() in casefolded:
                    raise PromotionError(f"archive contains duplicate member name: {name}")
                names.add(name)
                casefolded.add(name.casefold())
                if not info.is_dir():
                    files.append((info, name))

            file_names = {name for _, name in files}
            for name in file_names:
                parents = PurePosixPath(name).parents
                if any(str(parent) in file_names for parent in parents):
                    raise PromotionError("archive has a file/directory path collision")
            if not files:
                raise PromotionError("archive contains no regular files")
            return files, {
                "archiveName": archive.name,
                "archiveSha256": _sha256_path(archive),
                "memberCount": len(infos),
                "fileCount": len(files),
            }
    except BadZipFile as exc:
        raise PromotionError("archive is not a valid ZIP file") from exc


def _extract_and_compare(archive: Path, members: list[tuple[ZipInfo, str]], installed: Path) -> dict[str, Any]:
    expected: dict[str, str] = {}
    installed.mkdir(parents=True, exist_ok=False)
    with ZipFile(archive) as package:
        for info, name in members:
            destination = installed.joinpath(*PurePosixPath(name).parts)
            try:
                destination.resolve().relative_to(installed.resolve())
            except ValueError as exc:
                raise PromotionError("archive member escaped the isolated installation") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, destination.open("xb") as target:
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
            expected[name] = digest.hexdigest()

    actual = {
        file.relative_to(installed).as_posix(): _sha256_path(file)
        for file in installed.rglob("*")
        if file.is_file()
    }
    if actual != expected:
        raise PromotionError("archive-to-installed file parity check failed")
    return {"fileCount": len(actual), "parity": "exact-sha256"}


def _run_package_tests(command: str, installed: Path, timeout_seconds: int) -> dict[str, Any]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise PromotionError("test command could not be parsed") from exc
    if not argv:
        raise PromotionError("test command must not be empty")
    sandbox = shutil.which("bwrap")
    if sandbox is None:
        return {
            "command": _redact(command),
            "result": "sandbox_unavailable",
            "stderr": "bubblewrap is required for disposable package execution",
            "sandbox": "unavailable",
        }
    if not sandbox_available():
        return {
            "command": _redact(command),
            "result": "sandbox_unavailable",
            "stderr": "bubblewrap namespace sandbox could not be started",
            "sandbox": "unavailable",
        }
    # The extracted package is untrusted input.  Give it a read-only package
    # mount, an empty writable /tmp, no network, a minimal environment, and no
    # access to the caller's home, credentials, sockets, or state roots.
    sandbox_argv = [
        sandbox,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--clearenv",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--ro-bind", str(installed), "/workspace",
        "--chdir", "/workspace",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "LANG", "C.UTF-8",
        "--",
        *argv,
    ]
    try:
        completed = subprocess.run(
            sandbox_argv,
            cwd=installed,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": _redact(command),
            "result": "timed_out",
            "stdout": _redact((exc.stdout or "")[:10_000]),
            "stderr": _redact((exc.stderr or "")[:10_000]),
            "sandbox": "bubblewrap",
        }
    return {
        "command": _redact(command),
        "result": "passed" if completed.returncode == 0 else "failed",
        "exitCode": completed.returncode,
        "stdout": _redact(completed.stdout[:10_000]),
        "stderr": _redact(completed.stderr[:10_000]),
        "sandbox": "bubblewrap",
    }


def _receipt(
    stage: str,
    status: str,
    facts: dict[str, Any],
    *,
    commands: list[dict[str, Any]] | None = None,
    artifacts: list[str] | None = None,
    blockers: list[str] | None = None,
    residual_risk: list[str] | None = None,
) -> dict[str, Any]:
    timestamp = _now()
    return {
        "receiptVersion": "promotion-receipt-v1",
        "receiptId": f"promotion-{stage}",
        "stage": stage,
        "status": status,
        "startedAt": timestamp,
        "finishedAt": timestamp,
        "terminal": True,
        "evidence": {
            "commands": commands or [],
            "artifacts": artifacts or [],
            "facts": facts,
        },
        "blockers": blockers or [],
        "residualRisk": residual_risk or [],
    }


def _blocked_receipts(reason: str, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = {receipt["stage"] for receipt in existing}
    for stage in STAGES:
        if stage not in completed:
            existing.append(_receipt(stage, "NOT_STARTED", {}, blockers=[reason]))
    return existing


def _activation_blockers(
    *, rehearsal: bool, approval_token: str | None, reviewer: str | None, host_check: str | None
) -> list[str]:
    blockers = []
    if not rehearsal:
        blockers.append("activation requires --rehearsal")
    if approval_token != APPROVAL_TOKEN:
        blockers.append("activation requires the explicit rehearsal approval token")
    if not reviewer or not reviewer.strip():
        blockers.append("activation requires a named reviewer")
    if host_check != "PASS":
        blockers.append("activation requires --host-check PASS")
    return blockers


def run_rehearsal(
    archive: Path,
    test_command: str,
    *,
    activate: bool = False,
    rehearsal: bool = False,
    approval_token: str | None = None,
    reviewer: str | None = None,
    host_check: str | None = None,
    timeout_seconds: int = 60,
    prior_selection: str = "buildcrew",
    canary_seconds: int = 0,
    rollback_trigger: str = "validation-failure",
) -> dict[str, Any]:
    """Run one isolated rehearsal and return only redacted, portable evidence."""

    if timeout_seconds < 1:
        raise PromotionError("timeout_seconds must be at least one")
    if canary_seconds < 0:
        raise PromotionError("canary_seconds must not be negative")
    if not rollback_trigger.strip():
        raise PromotionError("rollback_trigger must not be empty")
    receipts: list[dict[str, Any]] = []
    try:
        members, source = validate_archive(archive)
    except PromotionError as exc:
        receipts.append(_receipt("candidate-validation", "FAIL", {"archiveName": archive.name}, blockers=[str(exc)]))
        _blocked_receipts("candidate validation failed", receipts)
        return {
            "schemaVersion": "promotion-rehearsal-v1",
            "mode": "temporary-rehearsal",
            "receipts": receipts,
            "terminalStatus": "FAILED",
        }

    receipts.append(_receipt(
        "candidate-validation",
        "PASS",
        source,
        artifacts=["source-archive-validated"],
        residual_risk=["Archive source remains external read-only input."],
    ))

    with tempfile.TemporaryDirectory(prefix="general-promotion-rehearsal-") as temporary:
        root = Path(temporary)
        installed = root / "installed"
        try:
            parity = _extract_and_compare(archive, members, installed)
            test_result = _run_package_tests(test_command, installed, timeout_seconds)
        except PromotionError as exc:
            receipts.append(_receipt("installation", "FAIL", {}, blockers=[str(exc)]))
            _blocked_receipts("installation failed", receipts)
            return {
                "schemaVersion": "promotion-rehearsal-v1",
                "mode": "temporary-rehearsal",
                "source": source,
                "receipts": receipts,
                "terminalStatus": "FAILED",
            }

        installation_status = "PASS" if test_result["result"] == "passed" else "FAIL"
        receipts.append(_receipt(
            "installation",
            installation_status,
            parity,
            commands=[test_result],
            artifacts=["isolated-installed-package", "archive-installed-parity"],
            blockers=[] if installation_status == "PASS" else ["extracted package test command did not pass"],
        ))
        if installation_status != "PASS":
            _blocked_receipts("installation test command failed", receipts)
            return {
                "schemaVersion": "promotion-rehearsal-v1",
                "mode": "temporary-rehearsal",
                "source": source,
                "receipts": receipts,
                "terminalStatus": "FAILED",
            }

        configuration = root / "active-selection.json"
        prior_config = {"selectedSkill": prior_selection}
        configuration.write_text(json.dumps(prior_config), encoding="utf-8")
        observed_prior = json.loads(configuration.read_text(encoding="utf-8"))
        receipts.append(_receipt(
            "active-configuration",
            "PASS" if observed_prior == prior_config else "FAIL",
            {"selectedSkill": observed_prior.get("selectedSkill"), "configurationScope": "temporary-only"},
            artifacts=["temporary-active-selection"],
            blockers=[] if observed_prior == prior_config else ["prior selection could not be verified"],
        ))

        if not activate:
            receipts.append(_receipt(
                "activation",
                "NOT_STARTED",
                {"activationRequested": False, "configurationScope": "temporary-only"},
                blockers=["activation is explicit and was not requested"],
            ))
            receipts.append(_receipt(
                "rollback",
                "NOT_STARTED",
                {"priorSelection": prior_selection},
                blockers=["no temporary activation occurred to roll back"],
            ))
            terminal_status = "REHEARSAL_READY"
        else:
            blockers = _activation_blockers(
                rehearsal=rehearsal,
                approval_token=approval_token,
                reviewer=reviewer,
                host_check=host_check,
            )
            if blockers:
                receipts.append(_receipt(
                    "activation",
                    "BLOCKED",
                    {"activationRequested": True, "configurationScope": "temporary-only"},
                    blockers=blockers,
                ))
                receipts.append(_receipt(
                    "rollback",
                    "NOT_STARTED",
                    {"priorSelection": prior_selection},
                    blockers=["activation gate did not pass"],
                ))
                terminal_status = "ACTIVATION_BLOCKED"
            else:
                candidate_config = {"selectedSkill": "isolated-candidate-package"}
                configuration.write_text(json.dumps(candidate_config), encoding="utf-8")
                activated = json.loads(configuration.read_text(encoding="utf-8"))
                receipts.append(_receipt(
                    "activation",
                    "PASS" if activated == candidate_config else "FAIL",
                    {
                        "activationRequested": True,
                        "reviewer": reviewer.strip(),
                        "hostCheck": host_check,
                        "canarySeconds": canary_seconds,
                        "canaryMode": "modeled-no-wait",
                        "rollbackTrigger": rollback_trigger.strip(),
                        "configurationScope": "temporary-only",
                    },
                    artifacts=["temporary-activation"],
                    blockers=[] if activated == candidate_config else ["temporary candidate selection was not observed"],
                ))
                configuration.write_text(json.dumps(prior_config), encoding="utf-8")
                restored = json.loads(configuration.read_text(encoding="utf-8"))
                restored_ok = restored == prior_config
                receipts.append(_receipt(
                    "rollback",
                    "PASS" if restored_ok else "FAIL",
                    {
                        "priorSelection": prior_selection,
                        "restoredSelection": restored.get("selectedSkill"),
                        "configurationScope": "temporary-only",
                    },
                    artifacts=["temporary-rollback"],
                    blockers=[] if restored_ok else ["prior supported selection was not restored"],
                ))
                terminal_status = "REHEARSED_AND_ROLLED_BACK" if restored_ok else "FAILED"

    return {
        "schemaVersion": "promotion-rehearsal-v1",
        "mode": "temporary-rehearsal",
        "source": source,
        "receipts": receipts,
        "terminalStatus": terminal_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Read-only package ZIP to rehearse")
    parser.add_argument("--test-command", required=True, help="Package test command run from the extracted package")
    parser.add_argument("--activate", action="store_true", help="Request temporary-only activation after all gates pass")
    parser.add_argument("--rehearsal", action="store_true", help="Acknowledge this is a temporary rehearsal")
    parser.add_argument("--approval-token", help="Must equal APPROVE_REHEARSAL_ACTIVATION when --activate is used")
    parser.add_argument("--reviewer", help="Named reviewer required when --activate is used")
    parser.add_argument("--host-check", help="Must be PASS when --activate is used")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--canary-seconds", type=int, default=0)
    parser.add_argument("--rollback-trigger", default="validation-failure")
    parser.add_argument("--output", type=Path, help="Write the sanitized rehearsal report to this path")
    args = parser.parse_args()
    try:
        report = run_rehearsal(
            args.archive,
            args.test_command,
            activate=args.activate,
            rehearsal=args.rehearsal,
            approval_token=args.approval_token,
            reviewer=args.reviewer,
            host_check=args.host_check,
            timeout_seconds=args.timeout_seconds,
            canary_seconds=args.canary_seconds,
            rollback_trigger=args.rollback_trigger,
        )
    except PromotionError as exc:
        report = {
            "schemaVersion": "promotion-rehearsal-v1",
            "mode": "temporary-rehearsal",
            "receipts": [],
            "terminalStatus": "FAILED",
            "error": str(exc),
        }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["terminalStatus"] in {"REHEARSAL_READY", "REHEARSED_AND_ROLLED_BACK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
