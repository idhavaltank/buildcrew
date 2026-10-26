"""Bounded browser, Android, iOS Simulator, and Flutter QA contracts.

The v2 package owns the QA packet, evidence rules, and terminal status.  A
host owns the browser/emulator implementation and supplies it through the
small transport protocol below.  No transport is created implicitly, and an
absent transport always produces an explicit unavailable/blocked result.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Protocol, Sequence

from .qa_contracts import validate_qa_contract


MANIFEST_SCHEMA_VERSION = "qa-adapter-manifest-v1"
RUN_SCHEMA_VERSION = "qa-adapter-run-v1"
RECEIPT_SCHEMA_VERSION = "qa-adapter-receipt-v1"
ADAPTER_KINDS = {"browser", "android_emulator", "ios_simulator", "flutter"}
ADAPTER_STATUSES = {"AVAILABLE", "UNAVAILABLE", "MISCONFIGURED", "BLOCKED", "NOT_VERIFIED"}
RUN_STATUSES = {"PASS", "FAILED", "BLOCKED", "NOT_VERIFIED"}
EVIDENCE_KINDS = {"screenshot", "log", "trace", "network", "device", "command", "other"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class QAAdapterError(ValueError):
    """Raised when a host adapter crosses the AE boundary incorrectly."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QAAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_id(value: Any, label: str) -> str:
    result = _text(value, label)
    if len(result) > 200 or not _SAFE_ID.fullmatch(result):
        raise QAAdapterError(f"{label} contains unsafe characters")
    return result


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _safe_ref(value: Any, label: str) -> str:
    ref = _text(value, label)
    if ref.startswith(("/", "\\", "file:", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", ref):
        return "[HOST_ARTIFACT]"
    if len(ref) > 500:
        raise QAAdapterError(f"{label} is too long")
    return ref


def _safe_summary(value: Any, label: str) -> str:
    summary = _text(value, label)
    if len(summary) > 500:
        raise QAAdapterError(f"{label} is too long")
    lowered = summary.casefold()
    if any(token in lowered for token in ("password=", "token=", "authorization:", "-----begin")):
        raise QAAdapterError(f"{label} contains sensitive material")
    return summary


class AdapterTransport(Protocol):
    """Host-owned browser/emulator transport.

    Implementations may use Playwright, WebDriver, ADB, simctl, Flutter, or another approved
    runtime.  AE only passes the validated request and consumes sanitized
    structured output; it never imports or invokes a runtime directly.
    """

    def probe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return sanitized availability and runtime identity."""

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run the approved workflow and return sanitized evidence."""


class WorkflowAdapterTransport(Protocol):
    """Optional real-runtime seam that receives bound workflow step specs."""

    def run_workflow(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        """Run the approved workflow without evaluating arbitrary step text."""


def validate_adapter_manifest(value: Any) -> dict[str, Any]:
    """Validate a declarative adapter manifest with unknown-field rejection."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["manifest: expected object"]}
    required = {
        "schemaVersion", "adapterId", "kind", "hostId", "version", "status",
        "capabilities", "targetAllowlist", "evidenceKinds", "sourceBound",
        "externalSideEffects",
    }
    errors.extend(f"manifest: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"manifest: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest.schemaVersion: expected {MANIFEST_SCHEMA_VERSION}")
    for key in ("adapterId", "hostId", "version"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"manifest.{key}: expected non-empty string")
    if value.get("kind") not in ADAPTER_KINDS:
        errors.append("manifest.kind: unsupported adapter kind")
    if value.get("status") not in ADAPTER_STATUSES:
        errors.append("manifest.status: unsupported adapter status")
    for key in ("capabilities", "targetAllowlist", "evidenceKinds"):
        items = value.get(key)
        if not isinstance(items, list) or not items or not all(isinstance(item, str) and item.strip() for item in items):
            errors.append(f"manifest.{key}: expected a non-empty string array")
        elif len(items) != len(set(items)):
            errors.append(f"manifest.{key}: duplicate values are not allowed")
    if isinstance(value.get("evidenceKinds"), list):
        errors.extend(
            f"manifest.evidenceKinds[{index}]: unsupported evidence kind"
            for index, item in enumerate(value["evidenceKinds"])
            if item not in EVIDENCE_KINDS
        )
    if value.get("sourceBound") is not True:
        errors.append("manifest.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("manifest.externalSideEffects: must be HOST_CONTROLLED")
    return {"valid": not errors, "errors": errors}


def validate_adapter_run(value: Any) -> dict[str, Any]:
    """Validate the exact request that crosses into a host transport."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["run: expected object"]}
    required = {
        "schemaVersion", "runId", "adapterId", "packetId", "taskId", "sourceFingerprint",
        "target", "targetIdentity", "stepIds", "timeoutSeconds", "externalState", "network", "credentials",
    }
    errors.extend(f"run: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"run: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RUN_SCHEMA_VERSION:
        errors.append(f"run.schemaVersion: expected {RUN_SCHEMA_VERSION}")
    for key in ("runId", "adapterId", "packetId", "taskId", "target"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"run.{key}: expected non-empty string")
    fingerprint = value.get("sourceFingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint.strip()) < 8:
        errors.append("run.sourceFingerprint: expected a source fingerprint")
    identity = value.get("targetIdentity")
    if not isinstance(identity, Mapping):
        errors.append("run.targetIdentity: expected object")
    else:
        expected = {"name", "version", "build", "environment"}
        errors.extend(f"run.targetIdentity: missing required field {key}" for key in sorted(expected - set(identity)))
        errors.extend(f"run.targetIdentity: unknown field {key}" for key in sorted(set(identity) - expected))
        for key in expected:
            if not isinstance(identity.get(key), str) or not identity[key].strip():
                errors.append(f"run.targetIdentity.{key}: expected non-empty string")
    step_ids = value.get("stepIds")
    if not isinstance(step_ids, list) or not step_ids or not all(isinstance(item, str) and item.strip() for item in step_ids):
        errors.append("run.stepIds: expected a non-empty string array")
    elif len(step_ids) != len(set(step_ids)):
        errors.append("run.stepIds: duplicate values are not allowed")
    timeout = value.get("timeoutSeconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1 or timeout > 86_400:
        errors.append("run.timeoutSeconds: expected integer from 1 to 86400")
    for key, choices in {
        "externalState": {"NONE", "DISPOSABLE_ONLY", "EXPLICIT_APPROVAL"},
        "network": {"DISABLED", "ALLOWLISTED", "HOST_CONTROLLED"},
        "credentials": {"NONE", "SANDBOXED", "HOST_SUPPLIED"},
    }.items():
        if value.get(key) not in choices:
            errors.append(f"run.{key}: unsupported value")
    return {"valid": not errors, "errors": errors}


def validate_adapter_receipt(value: Any) -> dict[str, Any]:
    """Validate a host adapter receipt without trusting it as AE closure."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["adapterReceipt: expected object"]}
    required = {
        "schemaVersion", "receiptId", "runId", "adapterId", "kind", "status", "sourceFingerprint",
        "targetIdentity", "hostExecution", "stepResults", "artifactRefs", "blockers", "sideEffects",
    }
    errors.extend(f"adapterReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"adapterReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"adapterReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("receiptId", "runId", "adapterId", "sourceFingerprint"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"adapterReceipt.{key}: expected non-empty string")
    if value.get("kind") not in ADAPTER_KINDS:
        errors.append("adapterReceipt.kind: unsupported adapter kind")
    if value.get("status") not in RUN_STATUSES:
        errors.append("adapterReceipt.status: unsupported status")
    identity = value.get("targetIdentity")
    if not isinstance(identity, Mapping) or set(identity) != {"name", "version", "build", "environment"}:
        errors.append("adapterReceipt.targetIdentity: expected exact target identity")
    host_execution = value.get("hostExecution")
    if not isinstance(host_execution, Mapping) or set(host_execution) != {"attempted", "transport", "proofLevel"}:
        errors.append("adapterReceipt.hostExecution: expected attempted/transport/proofLevel")
    elif not isinstance(host_execution.get("attempted"), bool) or host_execution.get("transport") not in {"INJECTED_HOST", "NONE"} or host_execution.get("proofLevel") not in {"UNAVAILABLE", "HOST_BOUND"}:
        errors.append("adapterReceipt.hostExecution: invalid execution state")
    steps = value.get("stepResults")
    if not isinstance(steps, list) or not steps:
        errors.append("adapterReceipt.stepResults: expected non-empty array")
    else:
        seen: set[str] = set()
        for index, item in enumerate(steps):
            label = f"adapterReceipt.stepResults[{index}]"
            if not isinstance(item, Mapping) or set(item) != {"stepId", "status", "evidenceRefs"}:
                errors.append(f"{label}: expected exact step result")
                continue
            step_id = item.get("stepId")
            if not isinstance(step_id, str) or not step_id.strip() or step_id in seen:
                errors.append(f"{label}.stepId: missing or duplicate step id")
            seen.add(str(step_id))
            if item.get("status") not in {"PASS", "FAILED", "BLOCKED", "NOT_VERIFIED"}:
                errors.append(f"{label}.status: unsupported status")
            refs = item.get("evidenceRefs")
            if not isinstance(refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in refs):
                errors.append(f"{label}.evidenceRefs: expected string array")
            if item.get("status") == "PASS" and not refs:
                errors.append(f"{label}.evidenceRefs: PASS requires evidence")
    refs = value.get("artifactRefs")
    if not isinstance(refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in refs):
        errors.append("adapterReceipt.artifactRefs: expected string array")
    blockers = value.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(item, str) and item.strip() for item in blockers):
        errors.append("adapterReceipt.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("adapterReceipt.sideEffects: must be empty; host side effects require separate approval evidence")
    if value.get("status") == "PASS" and blockers:
        errors.append("adapterReceipt.blockers: PASS cannot contain blockers")
    return {"valid": not errors, "errors": errors}


class QAAdapter:
    """Common adapter implementation used by platform-specific hosts."""

    def __init__(self, manifest: Mapping[str, Any], transport: AdapterTransport | None = None):
        result = validate_adapter_manifest(manifest)
        if not result["valid"]:
            raise QAAdapterError("invalid adapter manifest: " + "; ".join(result["errors"]))
        self.manifest = dict(manifest)
        self.transport = transport

    @property
    def adapter_id(self) -> str:
        return str(self.manifest["adapterId"])

    @property
    def kind(self) -> str:
        return str(self.manifest["kind"])

    def _unavailable_reason(self) -> str:
        if self.transport is None:
            return "host transport is not configured"
        if self.manifest["status"] != "AVAILABLE":
            return f"adapter manifest status is {self.manifest['status']}"
        return "host adapter did not report availability"

    def probe(self, *, target: str, source_fingerprint: str, target_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request = {
            "schemaVersion": RUN_SCHEMA_VERSION,
            "runId": "probe-" + self.adapter_id,
            "adapterId": self.adapter_id,
            "packetId": "probe",
            "taskId": "probe",
            "sourceFingerprint": source_fingerprint,
            "target": target,
            "targetIdentity": dict(target_identity or {"name": target, "version": "unknown", "build": "unknown", "environment": "probe"}),
            "stepIds": ["probe"],
            "timeoutSeconds": 30,
            "externalState": "NONE",
            "network": "DISABLED",
            "credentials": "NONE",
        }
        validation = validate_adapter_run(request)
        if not validation["valid"]:
            return {"status": "MISCONFIGURED", "adapterId": self.adapter_id, "kind": self.kind, "blockers": validation["errors"]}
        if self.transport is None or self.manifest["status"] != "AVAILABLE":
            return {
                "status": "UNAVAILABLE",
                "adapterId": self.adapter_id,
                "kind": self.kind,
                "blockers": [self._unavailable_reason()],
                "hostExecution": "NOT_ATTEMPTED",
            }
        try:
            response = self.transport.probe(request)
        except Exception as exc:  # host errors become evidence, never exceptions from a task path
            return {"status": "NOT_VERIFIED", "adapterId": self.adapter_id, "kind": self.kind, "blockers": [f"host probe failed: {type(exc).__name__}"]}
        if not isinstance(response, Mapping) or response.get("status") not in {"AVAILABLE", "UNAVAILABLE", "NOT_VERIFIED"}:
            return {"status": "NOT_VERIFIED", "adapterId": self.adapter_id, "kind": self.kind, "blockers": ["host probe returned an invalid status"]}
        sanitized = {"adapterId": self.adapter_id, "kind": self.kind, **{str(key): value for key, value in response.items() if key != "raw"}}
        runtime_identity = sanitized.get("runtimeIdentity")
        if isinstance(runtime_identity, Mapping):
            try:
                sanitized["runtimeIdentity"] = {
                    key: _safe_summary(runtime_identity[key], f"runtimeIdentity.{key}")
                    for key in ("name", "version", "build", "environment")
                }
            except (KeyError, QAAdapterError):
                return {"status": "NOT_VERIFIED", "adapterId": self.adapter_id, "kind": self.kind, "blockers": ["host probe returned an unsafe runtime identity"]}
        return sanitized

    def run(self, request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        validation = validate_adapter_run(request)
        if not validation["valid"]:
            raise QAAdapterError("invalid adapter run: " + "; ".join(validation["errors"]))
        if request.get("adapterId") != self.adapter_id:
            raise QAAdapterError("run adapterId does not match registered adapter")
        expected_steps = [str(item["stepId"]) for item in workflow_steps]
        requested_steps = list(request["stepIds"])
        if set(expected_steps) != set(requested_steps):
            raise QAAdapterError("run stepIds do not match workflow steps")
        identity = dict(request["targetIdentity"])
        common = {
            "receiptId": f"{request['runId']}:{self.adapter_id}",
            "runId": request["runId"],
            "adapterId": self.adapter_id,
            "kind": self.kind,
            "sourceFingerprint": request["sourceFingerprint"],
        }
        if self.transport is None or self.manifest["status"] != "AVAILABLE":
            adapter_receipt = {
                "schemaVersion": RECEIPT_SCHEMA_VERSION,
                **common,
                "status": "BLOCKED",
                "targetIdentity": identity,
                "hostExecution": {"attempted": False, "transport": "NONE", "proofLevel": "UNAVAILABLE"},
                "stepResults": [{"stepId": step_id, "status": "BLOCKED", "evidenceRefs": []} for step_id in expected_steps],
                "artifactRefs": [],
                "blockers": [self._unavailable_reason()],
                "sideEffects": [],
            }
            return self._result(adapter_receipt, request, workflow_steps)
        try:
            run_workflow = getattr(self.transport, "run_workflow", None)
            raw = run_workflow(dict(request), workflow_steps) if callable(run_workflow) else self.transport.run(dict(request))
        except Exception as exc:
            raw = {"status": "NOT_VERIFIED", "blockers": [f"host run failed: {type(exc).__name__}"]}
        if not isinstance(raw, Mapping):
            raw = {"status": "NOT_VERIFIED", "blockers": ["host run returned a non-object result"]}
        adapter_receipt = self._receipt_from_host(raw, common, identity, expected_steps)
        return self._result(adapter_receipt, request, workflow_steps)

    def _receipt_from_host(
        self,
        raw: Mapping[str, Any],
        common: Mapping[str, Any],
        identity: Mapping[str, Any],
        expected_steps: Sequence[str],
    ) -> dict[str, Any]:
        status = raw.get("status") if raw.get("status") in RUN_STATUSES else "NOT_VERIFIED"
        blockers: list[str] = []
        for item in raw.get("blockers", []):
            if not isinstance(item, str) or not item.strip():
                continue
            try:
                blockers.append(_safe_summary(item, "host blocker"))
            except QAAdapterError:
                blockers.append("[REDACTED_HOST_BLOCKER]")
        if raw.get("status") not in RUN_STATUSES:
            blockers.append("host run returned an invalid status")
        raw_identity = raw.get("targetIdentity", identity)
        if not isinstance(raw_identity, Mapping) or set(raw_identity) != {"name", "version", "build", "environment"}:
            raw_identity = dict(identity)
            blockers.append("host run did not return exact target identity")
        else:
            try:
                raw_identity = {
                    key: _safe_summary(raw_identity[key], f"targetIdentity.{key}")
                    for key in ("name", "version", "build", "environment")
                }
            except QAAdapterError:
                raw_identity = dict(identity)
                blockers.append("host target identity contained sensitive or unsafe text")
            if raw_identity != dict(identity):
                blockers.append("host target identity differs from the requested identity")
        raw_steps = raw.get("stepResults")
        by_id = {str(item.get("stepId")): item for item in raw_steps if isinstance(item, Mapping) and item.get("stepId")} if isinstance(raw_steps, list) else {}
        step_results: list[dict[str, Any]] = []
        for step_id in expected_steps:
            item = by_id.get(step_id, {})
            step_status = item.get("status") if item.get("status") in {"PASS", "FAILED", "BLOCKED", "NOT_VERIFIED"} else "NOT_VERIFIED"
            refs = [str(ref) for ref in item.get("evidenceRefs", []) if isinstance(ref, str) and ref.strip()] if isinstance(item.get("evidenceRefs"), list) else []
            if step_status == "PASS" and not refs:
                step_status = "NOT_VERIFIED"
                blockers.append(f"step {step_id} passed without evidence")
            if step_id not in by_id:
                blockers.append(f"host run omitted step {step_id}")
            safe_refs: list[str] = []
            for ref in refs:
                try:
                    safe_refs.append(_safe_ref(ref, "evidenceRef"))
                except QAAdapterError:
                    safe_refs.append("[HOST_ARTIFACT]")
                    blockers.append(f"step {step_id} contained an unsafe evidence reference")
            step_results.append({"stepId": step_id, "status": step_status, "evidenceRefs": safe_refs})
        artifacts = raw.get("artifactRefs", [])
        artifact_refs: list[str] = []
        if isinstance(artifacts, list):
            for ref in artifacts:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                try:
                    artifact_refs.append(_safe_ref(ref, "artifactRef"))
                except QAAdapterError:
                    artifact_refs.append("[HOST_ARTIFACT]")
                    blockers.append("host returned an unsafe artifact reference")
        if status == "PASS" and (blockers or any(item["status"] != "PASS" for item in step_results)):
            status = "NOT_VERIFIED"
        return {
            "schemaVersion": RECEIPT_SCHEMA_VERSION,
            **common,
            "status": status,
            "targetIdentity": raw_identity,
            "hostExecution": {"attempted": True, "transport": "INJECTED_HOST", "proofLevel": "HOST_BOUND"},
            "stepResults": step_results,
            "artifactRefs": artifact_refs,
            "blockers": sorted(set(blockers)),
            "sideEffects": [],
        }

    def _result(self, adapter_receipt: Mapping[str, Any], request: Mapping[str, Any], workflow_steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        validation = validate_adapter_receipt(adapter_receipt)
        if not validation["valid"]:
            raise QAAdapterError("adapter receipt failed validation: " + "; ".join(validation["errors"]))
        evidence: list[dict[str, Any]] = []
        evidence_by_ref: dict[str, dict[str, Any]] = {}
        for step in adapter_receipt["stepResults"]:
            for ref in step["evidenceRefs"]:
                evidence_by_ref.setdefault(ref, {
                    "evidenceId": ref,
                    "kind": "other",
                    "summary": f"Host evidence reference for {step['stepId']}",
                    "contentHash": _digest({"ref": ref, "sourceFingerprint": request["sourceFingerprint"]}),
                    "ref": ref,
                })
        for ref in adapter_receipt["artifactRefs"]:
            evidence_by_ref.setdefault(ref, {
                "evidenceId": ref,
                "kind": "other",
                "summary": "Sanitized host artifact reference",
                "contentHash": _digest({"ref": ref, "sourceFingerprint": request["sourceFingerprint"]}),
                "ref": ref,
            })
        evidence.extend(evidence_by_ref.values())
        started_datetime = datetime.now(timezone.utc).replace(microsecond=0)
        started = started_datetime.isoformat().replace("+00:00", "Z")
        finished = (started_datetime + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        core_steps = []
        for item in adapter_receipt["stepResults"]:
            core_steps.append({
                "stepId": item["stepId"],
                "status": "FAIL" if item["status"] == "FAILED" else item["status"],
                "evidenceRefs": list(item["evidenceRefs"]),
            })
        core_status = {
            "PASS": "PASS",
            "FAILED": "FAIL",
            "BLOCKED": "BLOCKED",
            "NOT_VERIFIED": "NOT_VERIFIED",
        }[adapter_receipt["status"]]
        if core_status == "PASS" and not all(item["status"] == "PASS" for item in core_steps):
            core_status = "NOT_VERIFIED"
        qa_receipt = {
            "schemaVersion": "qa-receipt-v1",
            "receiptId": adapter_receipt["receiptId"],
            "packetId": request["packetId"],
            "requestId": request.get("requestId", request["packetId"]),
            "taskId": request["taskId"],
            "sourceFingerprint": request["sourceFingerprint"],
            "adapter": self.kind,
            "runtimeIdentity": {
                "target": adapter_receipt["targetIdentity"]["name"],
                "version": adapter_receipt["targetIdentity"]["version"],
                "build": adapter_receipt["targetIdentity"]["build"],
            },
            "startedAt": started,
            "finishedAt": finished,
            "status": core_status,
            "steps": core_steps,
            "evidence": evidence,
            "findings": [],
            "skippedOrBlocked": [
                {"stepId": item["stepId"], "reason": "; ".join(adapter_receipt["blockers"])}
                for item in core_steps
                if item["status"] in {"BLOCKED", "NOT_VERIFIED", "FAIL"}
            ],
            "sideEffects": [],
            "terminal": True,
            "residualRisk": list(adapter_receipt["blockers"]),
        }
        qa_validation = validate_qa_contract("receipt", qa_receipt)
        if not qa_validation["valid"]:
            raise QAAdapterError("QA receipt failed validation: " + "; ".join(qa_validation["errors"]))
        return {
            "status": adapter_receipt["status"],
            "adapterReceipt": dict(adapter_receipt),
            "qaReceipt": qa_receipt,
            "hostProof": adapter_receipt["hostExecution"]["proofLevel"],
            "live": bool(adapter_receipt["hostExecution"]["attempted"]),
        }


class BrowserQAAdapter(QAAdapter):
    """Browser adapter specialization; runtime implementation stays host-owned."""

    def __init__(self, manifest: Mapping[str, Any], transport: AdapterTransport | None = None):
        if manifest.get("kind") != "browser":
            raise QAAdapterError("browser adapter requires kind=browser")
        super().__init__(manifest, transport)


class AndroidQAAdapter(QAAdapter):
    """Android emulator/device adapter specialization; ADB stays host-owned."""

    def __init__(self, manifest: Mapping[str, Any], transport: AdapterTransport | None = None):
        if manifest.get("kind") != "android_emulator":
            raise QAAdapterError("Android adapter requires kind=android_emulator")
        super().__init__(manifest, transport)


class IOSSimulatorQAAdapter(QAAdapter):
    """iOS Simulator adapter specialization; simctl stays host-owned."""

    def __init__(self, manifest: Mapping[str, Any], transport: AdapterTransport | None = None):
        if manifest.get("kind") != "ios_simulator":
            raise QAAdapterError("iOS adapter requires kind=ios_simulator")
        super().__init__(manifest, transport)


class FlutterQAAdapter(QAAdapter):
    """Flutter adapter specialization; the Flutter CLI stays host-owned."""

    def __init__(self, manifest: Mapping[str, Any], transport: AdapterTransport | None = None):
        if manifest.get("kind") != "flutter":
            raise QAAdapterError("Flutter adapter requires kind=flutter")
        super().__init__(manifest, transport)


def unavailable_manifest(*, adapter_id: str, kind: str, host_id: str = "host-unavailable") -> dict[str, Any]:
    """Build an explicit disabled-by-host manifest for status reporting."""

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "adapterId": adapter_id,
        "kind": kind,
        "hostId": host_id,
        "version": "0.0.0",
        "status": "UNAVAILABLE",
        "capabilities": ["probe"],
        "targetAllowlist": ["none"],
        "evidenceKinds": ["command", "log"],
        "sourceBound": True,
        "externalSideEffects": "HOST_CONTROLLED",
    }
