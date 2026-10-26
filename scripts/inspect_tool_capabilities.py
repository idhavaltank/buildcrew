#!/usr/bin/env python3
"""Create a read-only baseline tool-adapter manifest from observable host facts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.task_packet import CAPABILITY_IDS  # noqa: E402
from engine.tool_adapter import CONTROLLER_ID, STATUSES, validate_tool_adapter  # noqa: E402


MODES_BY_CAPABILITY = {
    "repo_read": "READ",
    "repo_write": "WRITE",
    "shell": "EXECUTE",
    "tests": "EXECUTE",
    "browser": "OBSERVE",
    "device": "OBSERVE",
    "subagents": "EXECUTE",
    "memory": "READ",
    "external_actions": "EXECUTE",
    "approval": "OBSERVE",
    "persistent_state": "WRITE",
    "worktree": "WRITE",
}


def _status(capability_id: str, repository: Path, memory_root: Path | None, state_root: Path | None) -> tuple[str, str, str]:
    if capability_id == "repo_read":
        return ("AVAILABLE", "observed", "repository directory is readable") if os.access(repository, os.R_OK) else ("UNAVAILABLE", "observed", "repository directory is not readable")
    if capability_id == "repo_write":
        return ("AVAILABLE", "observed", "repository directory is writable") if os.access(repository, os.W_OK) else ("UNAVAILABLE", "observed", "repository directory is not writable")
    if capability_id == "shell":
        shell = shutil.which("sh") or shutil.which("bash")
        return ("AVAILABLE", "observed", f"shell executable detected: {Path(shell).name}") if shell else ("UNAVAILABLE", "observed", "no shell executable detected")
    if capability_id == "memory" and memory_root is not None:
        return ("AVAILABLE", "observed", "configured memory root is readable") if os.access(memory_root, os.R_OK) else ("UNAVAILABLE", "observed", "configured memory root is not readable")
    if capability_id == "persistent_state" and state_root is not None:
        return ("AVAILABLE", "observed", "configured state root is writable") if os.access(state_root, os.W_OK) else ("UNAVAILABLE", "observed", "configured state root is not writable")
    if capability_id == "worktree":
        git_marker = repository / ".git"
        return ("AVAILABLE", "observed", "Git metadata marker detected") if git_marker.exists() else ("UNVERIFIED", "not_observed", "Git worktree capability was not verified")
    if capability_id == "external_actions":
        return ("UNAVAILABLE", "not_observed", "external actions are disabled by the safe baseline")
    return ("UNVERIFIED", "not_observed", "optional host capability was not probed")


def _parse_override(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise ValueError("--capability must use CAPABILITY=STATUS")
    capability_id, status = item.split("=", 1)
    if capability_id not in CAPABILITY_IDS:
        raise ValueError(f"unsupported capability: {capability_id}")
    if status not in STATUSES:
        raise ValueError(f"unsupported capability status: {status}")
    return capability_id, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-id", required=True, help="lowercase tool identifier")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--memory-root", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--capability", action="append", default=[], metavar="CAPABILITY=STATUS", help="host-declared override; repeat as needed")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.repository.is_dir():
        parser.error("--repository must point to a directory")
    try:
        overrides = dict(_parse_override(item) for item in args.capability)
    except ValueError as exc:
        parser.error(str(exc))
    capabilities = []
    for capability_id in sorted(CAPABILITY_IDS):
        status, proof, note = _status(capability_id, args.repository, args.memory_root, args.state_root)
        if capability_id in overrides:
            status = overrides[capability_id]
            proof = "host_declared"
            note = "host-declared status; no execution claim"
        capabilities.append({
            "capabilityId": capability_id,
            "status": status,
            "proof": proof,
            "mode": MODES_BY_CAPABILITY[capability_id],
            "note": note,
        })
    manifest = {
        "schemaVersion": "tool-adapter-manifest-v1",
        "adapterId": f"{args.tool_id}-local",
        "tool": {"toolId": args.tool_id, "displayName": args.display_name, "adapterVersion": "1.0.0"},
        "authority": {
            "mode": "NON_AUTHORITATIVE",
            "controller": CONTROLLER_ID,
            "canRoute": False,
            "canSelectModel": False,
            "canGrantWrite": False,
            "canCloseLifecycle": False,
            "canCreateState": False,
            "canAccessSecrets": False,
            "canRunArbitraryCode": False,
        },
        "capabilities": capabilities,
        "sideEffects": [],
    }
    validation = validate_tool_adapter(manifest)
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
