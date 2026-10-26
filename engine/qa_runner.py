"""AE-owned runner for bounded QA adapter execution.

The runner owns workflow sequencing and terminal receipts.  Browser, Android,
iOS Simulator, and Flutter adapters are subordinate transports registered by
the host.  Missing or failed transports remain visible as BLOCKED/NOT_VERIFIED
and never become a false PASS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .qa_adapters import QAAdapter, QAAdapterError, validate_adapter_receipt
from .qa_contracts import validate_qa_contract


RUNNER_SCHEMA_VERSION = "qa-runtime-run-v1"
TARGET_TO_KIND = {
    "WEB": "browser",
    "MOBILE": "android_emulator",
    "IOS": "ios_simulator",
    "FLUTTER": "flutter",
}


class QARunnerError(ValueError):
    """Raised when a packet cannot safely cross into a registered adapter."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def validate_qa_runtime_run(value: Any) -> dict[str, Any]:
    """Validate the runner envelope and its AE ownership declaration."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["qaRuntimeRun: expected object"]}
    required = {
        "schemaVersion", "runId", "packetId", "taskId", "sourceFingerprint", "status",
        "controller", "adapterIds", "adapterReceipts", "qaReceipt", "blockers", "live", "sideEffects",
    }
    errors.extend(f"qaRuntimeRun: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"qaRuntimeRun: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RUNNER_SCHEMA_VERSION:
        errors.append(f"qaRuntimeRun.schemaVersion: expected {RUNNER_SCHEMA_VERSION}")
    for key in ("runId", "packetId", "taskId", "sourceFingerprint", "controller"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"qaRuntimeRun.{key}: expected non-empty string")
    if value.get("controller") != "AE":
        errors.append("qaRuntimeRun.controller: only AE may own the runner")
    if value.get("status") not in {"PASS", "FAILED", "BLOCKED", "NOT_VERIFIED"}:
        errors.append("qaRuntimeRun.status: unsupported value")
    if not isinstance(value.get("live"), bool):
        errors.append("qaRuntimeRun.live: expected boolean")
    adapter_ids = value.get("adapterIds")
    if not isinstance(adapter_ids, list) or not all(isinstance(item, str) and item.strip() for item in adapter_ids):
        errors.append("qaRuntimeRun.adapterIds: expected string array")
    elif len(adapter_ids) != len(set(adapter_ids)):
        errors.append("qaRuntimeRun.adapterIds: duplicate adapter ids are not allowed")
    receipts = value.get("adapterReceipts")
    if not isinstance(receipts, list):
        errors.append("qaRuntimeRun.adapterReceipts: expected array")
    else:
        for index, receipt in enumerate(receipts):
            result = validate_adapter_receipt(receipt)
            errors.extend(f"adapterReceipts[{index}]: {error}" for error in result["errors"])
    qa_result = validate_qa_contract("receipt", value.get("qaReceipt"))
    errors.extend(f"qaReceipt: {error}" for error in qa_result["errors"])
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value.get("blockers", [])):
        errors.append("qaRuntimeRun.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("qaRuntimeRun.sideEffects: must be empty")
    if value.get("status") == "PASS" and value.get("blockers"):
        errors.append("qaRuntimeRun.blockers: PASS cannot contain blockers")
    return {"valid": not errors, "errors": errors}


class AdapterRegistry:
    """One AE-visible registry of subordinate QA adapters."""

    def __init__(self) -> None:
        self._by_kind: dict[str, QAAdapter] = {}

    def register(self, adapter: QAAdapter) -> None:
        if not isinstance(adapter, QAAdapter):
            raise QARunnerError("registry accepts QAAdapter instances only")
        if adapter.kind in self._by_kind:
            raise QARunnerError(f"duplicate adapter kind: {adapter.kind}")
        self._by_kind[adapter.kind] = adapter

    def get(self, kind: str) -> QAAdapter | None:
        return self._by_kind.get(kind)

    def ids(self) -> list[str]:
        return sorted(adapter.adapter_id for adapter in self._by_kind.values())


def _target_identity(kind: str, packet: Mapping[str, Any]) -> dict[str, str]:
    allowlist = packet.get("targetAllowlist", {})
    key_by_kind = {
        "browser": "webTargets",
        "android_emulator": "androidTargets",
        "ios_simulator": "iosTargets",
        "flutter": "flutterTargets",
    }
    key = key_by_kind.get(kind, "testDataScope")
    values = allowlist.get(key, []) if isinstance(allowlist, Mapping) else []
    target = str(values[0]) if isinstance(values, list) and values else f"{kind}-target"
    return {"name": target, "version": "host-supplied", "build": "host-supplied", "environment": "approved-target"}


def _blocked_qa_receipt(packet: Mapping[str, Any], step_ids: list[str], reason: str, *, adapter: str = "source_only") -> dict[str, Any]:
    started = _now()
    finished = started + timedelta(seconds=1)
    receipt = {
        "schemaVersion": "qa-receipt-v1",
        "receiptId": f"{packet.get('packetId', 'packet')}:blocked",
        "packetId": str(packet.get("packetId", "unknown-packet")),
        "requestId": str(packet.get("requestId", "unknown-request")),
        "taskId": str(packet.get("taskId", "unknown-task")),
        "sourceFingerprint": str(packet.get("sourceFingerprint", "unknown-source")),
        "adapter": adapter,
        "runtimeIdentity": {"target": "not-run", "version": "not-run", "build": "not-run"},
        "startedAt": _iso(started),
        "finishedAt": _iso(finished),
        "status": "BLOCKED",
        "steps": [{"stepId": step_id, "status": "BLOCKED", "evidenceRefs": []} for step_id in step_ids] or [{"stepId": "preflight", "status": "BLOCKED", "evidenceRefs": []}],
        "evidence": [],
        "findings": [],
        "skippedOrBlocked": [{"stepId": step_id, "reason": reason} for step_id in step_ids] or [{"stepId": "preflight", "reason": reason}],
        "sideEffects": [],
        "terminal": True,
        "residualRisk": [reason],
    }
    validation = validate_qa_contract("receipt", receipt)
    if not validation["valid"]:
        raise QARunnerError("blocked QA receipt failed validation: " + "; ".join(validation["errors"]))
    return receipt


def _aggregate_receipts(packet: Mapping[str, Any], receipts: list[Mapping[str, Any]], *, blockers: list[str]) -> dict[str, Any]:
    all_steps: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    all_blocked: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    for receipt in receipts:
        all_steps.extend(dict(item) for item in receipt.get("steps", []) if isinstance(item, Mapping))
        all_blocked.extend(dict(item) for item in receipt.get("skippedOrBlocked", []) if isinstance(item, Mapping))
        for item in receipt.get("evidence", []):
            if isinstance(item, Mapping) and item.get("evidenceId") not in evidence_ids:
                evidence_ids.add(str(item["evidenceId"]))
                all_evidence.append(dict(item))
    statuses = {str(item.get("status")) for item in all_steps}
    if not receipts:
        status = "BLOCKED"
    elif "FAIL" in statuses:
        status = "FAILED"
    elif "BLOCKED" in statuses:
        status = "BLOCKED"
    elif "NOT_VERIFIED" in statuses:
        status = "NOT_VERIFIED"
    else:
        status = "PASS"
    started = _now()
    finished = started + timedelta(seconds=1)
    receipt = {
        "schemaVersion": "qa-receipt-v1",
        "receiptId": f"{packet['packetId']}:runtime",
        "packetId": packet["packetId"],
        "requestId": packet["requestId"],
        "taskId": packet["taskId"],
        "sourceFingerprint": packet["sourceFingerprint"],
        "adapter": receipts[0]["adapter"] if len(receipts) == 1 else "compatibility",
        "runtimeIdentity": {
            "target": "multi-target" if len(receipts) > 1 else receipts[0]["runtimeIdentity"]["target"] if receipts else "not-run",
            "version": "host-adapter" if receipts else "not-run",
            "build": packet["packetId"],
        },
        "startedAt": _iso(started),
        "finishedAt": _iso(finished),
        "status": status,
        "steps": all_steps or [{"stepId": "preflight", "status": "BLOCKED", "evidenceRefs": []}],
        "evidence": all_evidence,
        "findings": [],
        "skippedOrBlocked": all_blocked or ([{"stepId": "preflight", "reason": reason} for reason in blockers] if blockers else []),
        "sideEffects": [],
        "terminal": True,
        "residualRisk": sorted(set(blockers + [str(item) for item in all_blocked if isinstance(item, Mapping) and item.get("reason")])),
    }
    validation = validate_qa_contract("receipt", receipt)
    if not validation["valid"]:
        raise QARunnerError("aggregate QA receipt failed validation: " + "; ".join(validation["errors"]))
    return receipt


def run_qa_workflow(
    workflow: Mapping[str, Any],
    packet: Mapping[str, Any],
    registry: AdapterRegistry,
    *,
    context_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one validated workflow through registered subordinate adapters."""

    workflow_result = validate_qa_contract("workflow-map", workflow)
    packet_result = validate_qa_contract("execution-packet", packet)
    if not workflow_result["valid"] or not packet_result["valid"]:
        reasons = [f"workflow: {error}" for error in workflow_result["errors"]]
        reasons.extend(f"packet: {error}" for error in packet_result["errors"])
        receipt = _blocked_qa_receipt(packet if isinstance(packet, Mapping) else {}, [], "; ".join(reasons))
        return _runner_result(packet if isinstance(packet, Mapping) else {}, receipt, [], reasons)
    if workflow.get("status") != "READY" or packet.get("status") != "READY":
        reason = "workflow and packet must both be READY"
        receipt = _blocked_qa_receipt(packet, [str(item.get("stepId")) for item in workflow.get("steps", []) if isinstance(item, Mapping)], reason)
        return _runner_result(packet, receipt, [], [reason])
    if workflow.get("sourceFingerprint") != packet.get("sourceFingerprint"):
        reason = "workflow and packet source fingerprints differ"
        receipt = _blocked_qa_receipt(packet, [str(item.get("stepId")) for item in workflow.get("steps", []) if isinstance(item, Mapping)], reason)
        return _runner_result(packet, receipt, [], [reason])
    if context_gate is not None and context_gate.get("decision") != "ALLOW":
        reason = "project-context preflight did not allow QA execution"
        receipt = _blocked_qa_receipt(packet, [str(item.get("stepId")) for item in workflow.get("steps", []) if isinstance(item, Mapping)], reason)
        return _runner_result(packet, receipt, [], [reason])

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    blockers: list[str] = []
    for step in workflow["steps"]:
        target = str(step.get("target"))
        kind = TARGET_TO_KIND.get(target)
        if kind is None:
            blockers.append(f"unsupported workflow target: {step.get('target')}")
            continue
        grouped.setdefault(kind, []).append(step)
    adapter_receipts: list[Mapping[str, Any]] = []
    qa_receipts: list[Mapping[str, Any]] = []
    for kind, steps in grouped.items():
        adapter = registry.get(kind)
        if adapter is None:
            blockers.append(f"no registered adapter for {kind}")
            continue
        identity = _target_identity(kind, packet)
        manifest_targets = adapter.manifest.get("targetAllowlist", [])
        if adapter.manifest.get("status") == "AVAILABLE" and identity["name"] not in manifest_targets:
            reason = f"{kind} target {identity['name']} is outside the adapter allowlist"
            blockers.append(reason)
            request = {
                "schemaVersion": "qa-adapter-run-v1",
                "runId": f"{packet['packetId']}:{kind}",
                "adapterId": adapter.adapter_id,
                "packetId": packet["packetId"],
                "taskId": packet["taskId"],
                "sourceFingerprint": packet["sourceFingerprint"],
                "target": identity["name"],
                "targetIdentity": identity,
                "stepIds": [str(item["stepId"]) for item in steps],
                "timeoutSeconds": packet["timeoutSeconds"],
                "externalState": packet["isolation"]["externalState"],
                "network": packet["isolation"]["network"],
                "credentials": packet["isolation"]["credentials"],
            }
            blocked_manifest = dict(adapter.manifest)
            blocked_manifest["status"] = "BLOCKED"
            result = QAAdapter(blocked_manifest, None).run(request, steps)
            adapter_receipts.append(result["adapterReceipt"])
            qa_receipts.append(result["qaReceipt"])
            continue
        probe = adapter.probe(target=identity["name"], source_fingerprint=packet["sourceFingerprint"], target_identity=identity)
        runtime_identity = probe.get("runtimeIdentity") if isinstance(probe, Mapping) else None
        if probe.get("status") == "AVAILABLE" and isinstance(runtime_identity, Mapping):
            required_identity = {"name", "version", "build", "environment"}
            if set(runtime_identity) == required_identity and all(isinstance(runtime_identity.get(key), str) and runtime_identity[key].strip() for key in required_identity) and runtime_identity.get("name") == identity["name"]:
                identity = {key: str(runtime_identity[key]) for key in ("name", "version", "build", "environment")}
            else:
                blockers.append(f"{kind} probe returned an invalid or mismatched runtime identity")
        request = {
            "schemaVersion": "qa-adapter-run-v1",
            "runId": f"{packet['packetId']}:{kind}",
            "adapterId": adapter.adapter_id,
            "packetId": packet["packetId"],
            "taskId": packet["taskId"],
            "sourceFingerprint": packet["sourceFingerprint"],
            "target": identity["name"],
            "targetIdentity": identity,
            "stepIds": [str(item["stepId"]) for item in steps],
            "timeoutSeconds": packet["timeoutSeconds"],
            "externalState": packet["isolation"]["externalState"],
            "network": packet["isolation"]["network"],
            "credentials": packet["isolation"]["credentials"],
        }
        try:
            if probe.get("status") != "AVAILABLE":
                blocked_adapter = QAAdapter(adapter.manifest, None)
                result = blocked_adapter.run(request, steps)
                blockers.extend(str(item) for item in probe.get("blockers", []) if isinstance(item, str))
            else:
                result = adapter.run(request, steps)
        except QAAdapterError as exc:
            blockers.append(f"{kind} adapter rejected run: {str(exc)}")
            continue
        adapter_receipts.append(result["adapterReceipt"])
        qa_receipts.append(result["qaReceipt"])
    receipt = _aggregate_receipts(packet, qa_receipts, blockers=blockers)
    return _runner_result(packet, receipt, adapter_receipts, blockers)


def _runner_result(packet: Mapping[str, Any], receipt: Mapping[str, Any], adapter_receipts: list[Mapping[str, Any]], blockers: list[str]) -> dict[str, Any]:
    status = str(receipt.get("status", "BLOCKED"))
    result = {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "runId": f"{packet.get('packetId', 'unknown')}:runner",
        "packetId": str(packet.get("packetId", "unknown-packet")),
        "taskId": str(packet.get("taskId", "unknown-task")),
        "sourceFingerprint": str(packet.get("sourceFingerprint", "unknown-source")),
        "status": status,
        "controller": "AE",
        "adapterIds": sorted(str(item.get("adapterId")) for item in adapter_receipts),
        "adapterReceipts": [dict(item) for item in adapter_receipts],
        "qaReceipt": dict(receipt),
        "blockers": sorted(set(str(item) for item in blockers if str(item).strip())),
        "live": any(item.get("hostExecution", {}).get("proofLevel") == "HOST_BOUND" for item in adapter_receipts),
        "sideEffects": [],
    }
    validation = validate_qa_runtime_run(result)
    if not validation["valid"]:
        raise QARunnerError("QA runtime result failed validation: " + "; ".join(validation["errors"]))
    return result
