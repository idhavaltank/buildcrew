"""Compatibility wrapper for the canonical project-context shadow binder.

Older v2 callers pass a small packet rather than the canonical intake
projection. The wrapper preserves that API while routing all behavior through
``engine.context_shadow`` so there is only one shadow-binding implementation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .context_shadow import bind_project_context_shadow
from .context_receipts import validate_context_map, validate_policy_bundle, validate_receipt


def _intake_from_packet(packet: Mapping[str, Any], context_map: Mapping[str, Any]) -> dict[str, Any]:
    repository = context_map.get("repository", {}) if isinstance(context_map, Mapping) else {}
    fingerprints = context_map.get("fingerprints", {}) if isinstance(context_map, Mapping) else {}
    return {
        "taskId": str(packet.get("taskId") or "shadow-task"),
        "repositoryId": str(repository.get("repositoryId") or "shadow-repository"),
        "sourceFingerprint": fingerprints.get("sourceFingerprint", ""),
        "contextFingerprint": fingerprints.get("contextFingerprint", ""),
        "taskScopeFingerprint": fingerprints.get("taskScopeFingerprint", ""),
        "requestType": str(packet.get("requestType") or "SHADOW_BINDING"),
        "normalizedObjective": str(packet.get("objective") or "shadow sidecar binding"),
        "targetPaths": list(packet.get("targetPaths", ["."])) if isinstance(packet.get("targetPaths", ["."]), list) else ["."],
        "acceptanceCriteria": list(packet.get("acceptanceCriteria", [])) if isinstance(packet.get("acceptanceCriteria", []), list) else [],
    }


def bind_context_shadow(
    packet: Mapping[str, Any],
    context_map: Mapping[str, Any],
    receipt: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the legacy packet shape with a canonical report-only binding."""

    checks = {
        "contextMap": validate_context_map(context_map),
        "receipt": validate_receipt(receipt),
        "policy": validate_policy_bundle(policy),
    }
    binding = bind_project_context_shadow(_intake_from_packet(packet, context_map), context_map, receipt, policy)
    conflicts = list(context_map.get("conflicts", [])) if isinstance(context_map, Mapping) else []
    if binding["status"] != "PASS":
        conflicts.extend({"conflictId": "shadow-binding", "blocking": True, "reason": blocker} for blocker in binding["blockers"])
    result = deepcopy(dict(packet))
    result["contextShadow"] = {
        "status": "SHADOW_PASS" if binding["status"] == "PASS" else "SHADOW_CONFLICT",
        "checks": checks,
        "conflicts": conflicts,
        "enforced": False,
        "sideEffects": [],
        "binding": binding,
    }
    return result
