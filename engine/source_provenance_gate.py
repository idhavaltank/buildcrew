"""Fail-closed source decision for AE mutation routing."""
from typing import Any, Mapping


def decide_source_provenance(
    current: Mapping[str, Any],
    lane: Mapping[str, Any] | None,
    *,
    read_only: bool,
    requested_paths: list[str] | None = None,
) -> dict[str, Any]:
    if read_only:
        return {
            "schemaVersion": "source-provenance-decision-v1",
            "decision": "READ_ONLY_CURRENT",
            "blockers": [],
        }
    if not lane:
        return {
            "schemaVersion": "source-provenance-decision-v1",
            "decision": "CONFIRM_CURRENT",
            "blockers": ["current source is unbound"],
        }
    source = lane.get("source", {})
    keys = ("repositoryId", "worktreePath", "worktreeId", "headSha", "baselineFingerprint")
    if any(source.get(key) != current.get(key) for key in keys):
        return {
            "schemaVersion": "source-provenance-decision-v1",
            "decision": "PROVISION_OR_REUSE",
            "blockers": ["source binding changed"],
        }
    if not lane.get("writerOwner") or not set(requested_paths or []).issubset(
        set(lane.get("writeAllowlist", []))
    ):
        return {
            "schemaVersion": "source-provenance-decision-v1",
            "decision": "BLOCKED",
            "blockers": ["writer lease missing or scope overlaps"],
        }
    return {
        "schemaVersion": "source-provenance-decision-v1",
        "decision": "CONTINUE_CURRENT",
        "blockers": [],
    }
