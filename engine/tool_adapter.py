"""Provider-neutral tool adapters and cross-tool compatibility evaluation."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .task_packet import CAPABILITY_IDS, profile_task, validate_task_packet


SCHEMA_VERSION = "tool-adapter-manifest-v1"
REPORT_SCHEMA_VERSION = "tool-compatibility-report-v1"
CONTROLLER_ID = "BuildCrew"
STATUSES = {"AVAILABLE", "LIMITED", "UNAVAILABLE", "UNVERIFIED"}
PROOFS = {"observed", "host_declared", "contract_fixture", "not_observed"}
MODES = {"READ", "WRITE", "EXECUTE", "OBSERVE"}
TOOL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
URL = re.compile(r"(?:https?://|ftp://|javascript:|data:)", re.I)
FORBIDDEN_KEYS = {
    "route",
    "routing",
    "model",
    "modelselection",
    "prompt",
    "prompts",
    "command",
    "commands",
    "callback",
    "callbacks",
    "secret",
    "secrets",
    "credential",
    "credentials",
}


def _errors_for_keys(value: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    errors = [f"{label}: missing required field {key}" for key in sorted(required - set(value))]
    errors.extend(f"{label}: unknown field {key}" for key in sorted(set(value) - required))
    return errors


def _walk(value: Any, path: str = "manifest"):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), f"{path}.{key}", child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def validate_tool_adapter(value: Any) -> dict[str, Any]:
    """Validate a non-authoritative tool adapter manifest."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "eligible": False, "errors": ["adapter: expected object"]}
    root_required = {"schemaVersion", "adapterId", "tool", "authority", "capabilities", "sideEffects"}
    errors.extend(_errors_for_keys(value, root_required, "adapter"))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"adapter.schemaVersion: expected {SCHEMA_VERSION}")
    adapter_id = value.get("adapterId")
    if not isinstance(adapter_id, str) or not TOOL_ID.fullmatch(adapter_id):
        errors.append("adapter.adapterId: expected a lowercase slug")

    tool = value.get("tool")
    if not isinstance(tool, Mapping):
        errors.append("adapter.tool: expected object")
    else:
        errors.extend(_errors_for_keys(tool, {"toolId", "displayName", "adapterVersion"}, "adapter.tool"))
        if not isinstance(tool.get("toolId"), str) or not TOOL_ID.fullmatch(tool.get("toolId", "")):
            errors.append("adapter.tool.toolId: expected a lowercase slug")
        for key in ("displayName", "adapterVersion"):
            if not isinstance(tool.get(key), str) or not tool[key].strip():
                errors.append(f"adapter.tool.{key}: expected non-empty text")

    authority = value.get("authority")
    authority_required = {
        "mode",
        "controller",
        "canRoute",
        "canSelectModel",
        "canGrantWrite",
        "canCloseLifecycle",
        "canCreateState",
        "canAccessSecrets",
        "canRunArbitraryCode",
    }
    if not isinstance(authority, Mapping):
        errors.append("adapter.authority: expected object")
    else:
        errors.extend(_errors_for_keys(authority, authority_required, "adapter.authority"))
        if authority.get("mode") != "NON_AUTHORITATIVE" or authority.get("controller") != CONTROLLER_ID:
            errors.append(f"adapter.authority: only a non-authoritative {CONTROLLER_ID} adapter is allowed")
        for key in authority_required - {"mode", "controller"}:
            if authority.get(key) is not False:
                errors.append(f"adapter.authority.{key}: must be false")

    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("adapter.capabilities: expected non-empty array")
        capabilities = []
    seen: set[str] = set()
    capability_required = {"capabilityId", "status", "proof", "mode"}
    for index, capability in enumerate(capabilities):
        label = f"adapter.capabilities[{index}]"
        if not isinstance(capability, Mapping):
            errors.append(f"{label}: expected object")
            continue
        errors.extend(f"{label}: missing required field {key}" for key in sorted(capability_required - set(capability)))
        errors.extend(f"{label}: unknown field {key}" for key in sorted(set(capability) - (capability_required | {"note"})))
        capability_id = capability.get("capabilityId")
        if capability_id not in CAPABILITY_IDS:
            errors.append(f"{label}.capabilityId: unsupported value")
        elif capability_id in seen:
            errors.append(f"{label}.capabilityId: duplicate value")
        else:
            seen.add(capability_id)
        if capability.get("status") not in STATUSES:
            errors.append(f"{label}.status: unsupported value")
        if capability.get("proof") not in PROOFS:
            errors.append(f"{label}.proof: unsupported value")
        if capability.get("mode") not in MODES:
            errors.append(f"{label}.mode: unsupported value")
        if "note" in capability and (not isinstance(capability["note"], str) or len(capability["note"]) > 500):
            errors.append(f"{label}.note: expected text up to 500 characters")

    if value.get("sideEffects") != []:
        errors.append("adapter.sideEffects: must be empty")
    for key, location, child in _walk(value):
        if key.replace("_", "").replace("-", "").lower() in FORBIDDEN_KEYS:
            errors.append(f"{location}: authority-bearing field is forbidden")
        if isinstance(child, str) and URL.search(child):
            errors.append(f"{location}: remote instruction URL is forbidden")

    return {"valid": not errors, "eligible": not errors, "errors": errors}


def _required_capabilities(packet: Mapping[str, Any]) -> set[str]:
    required = {item["capabilityId"] for item in packet.get("requiredCapabilities", []) if isinstance(item, Mapping)}
    actions = packet.get("allowedActions", {})
    if actions.get("read"):
        required.add("repo_read")
    if actions.get("write"):
        required.add("repo_write")
    if actions.get("execute"):
        required.add("shell")
    if actions.get("external"):
        required.add("external_actions")
    for evidence in packet.get("requiredEvidence", []):
        if evidence == "source":
            required.add("repo_read")
        elif evidence == "test":
            required.add("tests")
        elif evidence == "browser":
            required.add("browser")
        elif evidence == "device":
            required.add("device")
        elif evidence == "deployment":
            required.add("deployment")
    return required


def evaluate_task_against_adapter(packet: Mapping[str, Any], adapter: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a portable task against one host/tool manifest."""

    packet_validation = validate_task_packet(packet)
    adapter_validation = validate_tool_adapter(adapter)
    if not packet_validation["valid"] or not adapter_validation["valid"]:
        return {
            "status": "INVALID",
            "taskId": packet.get("taskId"),
            "adapterId": adapter.get("adapterId"),
            "errors": packet_validation["errors"] + adapter_validation["errors"],
            "sourceOnly": True,
            "executed": False,
            "sideEffects": [],
        }

    profile = profile_task(packet)
    capability_map = {item["capabilityId"]: item for item in adapter["capabilities"]}
    required = sorted(_required_capabilities(packet))
    missing: list[str] = []
    limited: list[str] = []
    available: list[str] = []
    for capability_id in required:
        status = capability_map.get(capability_id, {}).get("status")
        if status == "AVAILABLE":
            available.append(capability_id)
        elif status in {"LIMITED", "UNVERIFIED"}:
            limited.append(capability_id)
        else:
            missing.append(capability_id)

    if profile["approvalGate"]["status"] == "BLOCKED":
        status = "BLOCKED"
    elif missing:
        status = "BLOCKED"
    elif limited:
        status = "MISSING_COVERAGE"
    else:
        status = "READY"
    return {
        "status": status,
        "taskId": packet["taskId"],
        "adapterId": adapter["adapterId"],
        "toolId": adapter["tool"]["toolId"],
        "route": profile["route"],
        "budget": profile["budget"],
        "approvalGate": profile["approvalGate"],
        "requiredCapabilities": required,
        "availableCapabilities": available,
        "limitedCapabilities": limited,
        "missingCapabilities": missing,
        "requiredEvidence": packet["requiredEvidence"],
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }


def compare_task_across_adapters(packet: Mapping[str, Any], adapters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce a source-only compatibility report for several tool adapters."""

    results = [evaluate_task_against_adapter(packet, adapter) for adapter in adapters]
    statuses = {result.get("status") for result in results}
    if "INVALID" in statuses:
        overall = "INVALID"
    elif statuses == {"READY"}:
        overall = "COMPATIBLE"
    elif statuses == {"BLOCKED"}:
        overall = "BLOCKED"
    else:
        overall = "MISSING_COVERAGE"
    routes = {result.get("route") for result in results if result.get("route")}
    report_id = sha256(json.dumps({"task": packet, "results": results}, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "reportId": f"sha256:{report_id}",
        "taskId": packet.get("taskId"),
        "overallStatus": overall,
        "routeAgreement": len(routes) <= 1,
        "adapters": [result.get("adapterId") for result in results],
        "results": results,
        "sourceOnly": True,
        "executed": False,
        "sideEffects": [],
    }
