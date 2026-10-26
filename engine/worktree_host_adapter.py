"""Approved bounded Git worktree provisioning adapter."""
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .git_source_context import check_branch_availability


def provision_worktree(request: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "repositoryRoot",
        "worktreePath",
        "branch",
        "baseRef",
        "approvalId",
        "taskId",
        "laneId",
    )
    if any(not isinstance(request.get(key), str) or not request[key] for key in required):
        raise ValueError("validated approved request is required")

    root = Path(request["repositoryRoot"]).resolve()
    target = Path(request["worktreePath"]).resolve()
    branch = request["branch"]
    availability = check_branch_availability(root, branch)
    if not availability["available"]:
        return {
            "schemaVersion": "worktree-provisioning-receipt-v1",
            "status": "BLOCKED",
            "branch": branch,
            "worktreePath": str(target),
            "blockers": availability["conflicts"],
            "sideEffects": [],
        }
    if target.exists():
        return {
            "schemaVersion": "worktree-provisioning-receipt-v1",
            "status": "BLOCKED",
            "branch": branch,
            "worktreePath": str(target),
            "blockers": ["TARGET_EXISTS"],
            "sideEffects": [],
        }

    base = subprocess.run(
        ["git", "rev-parse", request["baseRef"]],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if base.returncode:
        return {
            "schemaVersion": "worktree-provisioning-receipt-v1",
            "status": "BLOCKED",
            "branch": branch,
            "worktreePath": str(target),
            "blockers": ["BASE_UNRESOLVED"],
            "sideEffects": [],
        }

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(target), base.stdout.strip()],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "schemaVersion": "worktree-provisioning-receipt-v1",
        "status": "COMPLETED" if not result.returncode else "FAILED",
        "branch": branch,
        "worktreePath": str(target),
        "baseSha": base.stdout.strip(),
        "blockers": [] if not result.returncode else ["GIT_WORKTREE_ADD_FAILED"],
        "sideEffects": ["git worktree add"] if not result.returncode else [],
    }
