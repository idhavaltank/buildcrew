"""Read-only Git source inspection and strict branch availability checks."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class GitSourceError(ValueError):
    pass


def _git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)):
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode not in allowed:
        raise GitSourceError(f"git {' '.join(args[:2])} failed ({result.returncode})")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _resolve_git_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _redact_origin(value: str) -> str:
    """Keep repository location useful without exposing URL credentials/tokens."""
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            try:
                port = parsed.port
            except ValueError:
                port = None
            netloc = host if port is None else f"{host}:{port}"
            return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    if "@" in value:
        return value.rsplit("@", 1)[-1]
    return value


def _status_paths(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if line.startswith("? "):
            paths.append(line[2:])
        elif line.startswith("1 "):
            fields = line.split(" ", 8)
            if len(fields) == 9:
                paths.append(fields[8])
        elif line.startswith("2 "):
            fields = line.split(" ", 9)
            if len(fields) == 10:
                paths.extend(part for part in fields[9].split("\t") if part)
        elif line.startswith("u "):
            fields = line.split(" ", 10)
            if len(fields) == 11:
                paths.append(fields[10])
    return sorted(set(paths))


def inspect_source_context(
    path: str | Path,
    *,
    repository_id: str | None = None,
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    _, top, _ = _git(root, "rev-parse", "--show-toplevel")
    worktree = Path(top).resolve()
    _, git_dir, _ = _git(worktree, "rev-parse", "--git-dir")
    _, common_dir, _ = _git(worktree, "rev-parse", "--git-common-dir")
    _, head, _ = _git(worktree, "rev-parse", "HEAD")
    branch_code, branch, _ = _git(
        worktree,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        allowed=(0, 1),
    )
    _, status, _ = _git(
        worktree,
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
    )
    _, inventory, _ = _git(worktree, "worktree", "list", "--porcelain")
    _, origin, _ = _git(
        worktree,
        "remote",
        "get-url",
        "origin",
        allowed=(0, 2),
    )
    safe_origin = _redact_origin(origin)
    baseline_paths = _status_paths(status)
    return {
        "schemaVersion": "git-source-binding-v1",
        "repositoryId": repository_id
        or (
            f"remote:{safe_origin}"
            if safe_origin
            else f"path:{hashlib.sha256(str(worktree).encode()).hexdigest()[:16]}"
        ),
        "worktreePath": str(worktree),
        "worktreeId": str(_resolve_git_path(worktree, git_dir)),
        "commonDir": str(_resolve_git_path(worktree, common_dir)),
        "branch": branch if branch_code == 0 else None,
        "headSha": head,
        "checkoutKind": (
            "DETACHED"
            if branch_code
            else ("LINKED" if (worktree / ".git").is_file() else "PRIMARY")
        ),
        "dirty": bool(baseline_paths),
        "baselinePaths": baseline_paths,
        "baselineFingerprint": hashlib.sha256(status.encode()).hexdigest(),
        "worktreeInventory": inventory,
        "origin": safe_origin or None,
        "confidence": "EXACT",
    }


def check_branch_availability(
    root: str | Path,
    name: str,
) -> dict[str, Any]:
    root = Path(root)
    valid, _, _ = _git(root, "check-ref-format", "--branch", name, allowed=(0, 1))
    if valid:
        return {
            "status": "INVALID_BRANCH_NAME",
            "available": False,
            "conflicts": ["INVALID"],
        }
    local, _, _ = _git(
        root,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{name}",
        allowed=(0, 1),
    )
    try:
        remote, _, _ = _git(
            root,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{name}",
            allowed=(0, 2),
        )
    except GitSourceError:
        return {
            "status": "REMOTE_UNVERIFIED",
            "available": False,
            "conflicts": ["REMOTE_UNVERIFIED"],
        }
    _, inventory, _ = _git(root, "worktree", "list", "--porcelain")
    conflicts = (
        [] if remote == 2 else ["REMOTE"]
    ) + ([] if local == 1 else ["LOCAL"]) + (
        ["WORKTREE"] if f"branch refs/heads/{name}" in inventory else []
    )
    return {
        "status": "AVAILABLE" if not conflicts else "BRANCH_NAME_CONFLICT",
        "available": not conflicts,
        "conflicts": conflicts,
    }
