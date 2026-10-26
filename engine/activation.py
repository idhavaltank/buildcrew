"""Approved host boundary for candidate canary, activation, rollback, and retirement."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .promotion_simulation import simulate_promotion, validate_promotion_simulation


RECEIPT_SCHEMA_VERSION = "promotion-activation-receipt-v1"
LIVE_STATUSES = {"ACTIVATED", "ROLLED_BACK", "BLOCKED", "FAILED", "CAPABILITY_UNAVAILABLE", "RETIRED"}


class ActivationError(ValueError):
    """Raised when an activation request is outside the explicit host boundary."""


class ActivationHost(Protocol):
    """Host-owned active-skill switch; implementations must be atomic and reversible."""

    def snapshot(self, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return the active-state identity before candidate changes."""

    def canary(self, candidate: Mapping[str, Any], observation: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run the approved canary and return sanitized health evidence."""

    def activate(self, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        """Switch the active candidate after canary approval."""

    def rollback(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        """Atomically restore the prior active state."""

    def retire(self, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        """Retire a candidate only after active-state ownership is clear."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActivationError(f"{label} must be a non-empty string")
    return value.strip()


def validate_activation_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["activationReceipt: expected object"]}
    required = {"schemaVersion", "receiptId", "candidateId", "candidateFingerprint", "priorSelection", "status", "live", "approvalId", "stages", "rollbackAvailable", "blockers", "residualRisk", "externalMutation", "sideEffects", "generatedAt"}
    errors.extend(f"activationReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"activationReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"activationReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("receiptId", "candidateId", "candidateFingerprint", "priorSelection", "approvalId", "generatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"activationReceipt.{key}: expected non-empty string")
    if value.get("status") not in LIVE_STATUSES:
        errors.append("activationReceipt.status: unsupported status")
    for key in ("live", "rollbackAvailable", "externalMutation"):
        if not isinstance(value.get(key), bool):
            errors.append(f"activationReceipt.{key}: expected boolean")
    stages = value.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("activationReceipt.stages: expected non-empty array")
    else:
        for index, stage in enumerate(stages):
            if not isinstance(stage, Mapping) or set(stage) != {"stage", "status", "summary"}:
                errors.append(f"activationReceipt.stages[{index}]: expected stage/status/summary")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value["blockers"]):
        errors.append("activationReceipt.blockers: expected string array")
    if not isinstance(value.get("residualRisk"), list) or not all(isinstance(item, str) and item.strip() for item in value["residualRisk"]):
        errors.append("activationReceipt.residualRisk: expected string array")
    if value.get("sideEffects") != []:
        errors.append("activationReceipt.sideEffects: must be empty; externalMutation records host state separately")
    if value.get("status") == "ACTIVATED" and (value.get("live") is not True or value.get("externalMutation") is not True):
        errors.append("activationReceipt: ACTIVATED requires live external mutation proof")
    return {"valid": not errors, "errors": errors}


def _candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
    candidate_id = _text(candidate.get("candidateId") or candidate.get("version"), "candidate.candidateId")
    fingerprint = _text(candidate.get("sourceFingerprint"), "candidate.sourceFingerprint")
    return candidate_id, fingerprint


def _receipt(candidate: Mapping[str, Any], *, status: str, live: bool, approval_id: str, prior_selection: str, stages: list[dict[str, str]], blockers: list[str], residual_risk: list[str], external_mutation: bool) -> dict[str, Any]:
    candidate_id, fingerprint = _candidate_identity(candidate)
    receipt = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "receiptId": f"{candidate_id}:{fingerprint[:12]}:activation",
        "candidateId": candidate_id,
        "candidateFingerprint": fingerprint,
        "priorSelection": prior_selection,
        "status": status,
        "live": live,
        "approvalId": approval_id,
        "stages": stages,
        "rollbackAvailable": any(stage["stage"] == "rollback" and stage["status"] == "READY" for stage in stages),
        "blockers": sorted(set(blockers)),
        "residualRisk": sorted(set(residual_risk)),
        "externalMutation": external_mutation,
        "sideEffects": [],
        "generatedAt": _now(),
    }
    validation = validate_activation_receipt(receipt)
    if not validation["valid"]:
        raise ActivationError("activation receipt failed validation: " + "; ".join(validation["errors"]))
    return receipt


class CandidateActivationController:
    """Use a host switch only after explicit approval and observable canary proof."""

    def __init__(self, host: ActivationHost | None = None):
        self.host = host

    def rehearse(self, candidate: Mapping[str, Any], *, canary_pass: bool = True, recovery_pass: bool = True) -> dict[str, Any]:
        simulation = simulate_promotion(candidate, canary_pass=canary_pass, activation_requested=True, recovery_pass=recovery_pass)
        validation = validate_promotion_simulation(simulation)
        if not validation["valid"]:
            raise ActivationError("promotion rehearsal failed validation: " + "; ".join(validation["errors"]))
        return {"status": simulation["terminalStatus"], "live": False, "receipt": simulation}

    def activate(
        self,
        candidate: Mapping[str, Any],
        *,
        approval: Mapping[str, Any] | None = None,
        observation: Mapping[str, Any] | None = None,
        prior_selection: str = "buildcrew",
        retire: bool = False,
    ) -> dict[str, Any]:
        candidate_id, _ = _candidate_identity(candidate)
        approval_id = "not-approved"
        blockers: list[str] = []
        if not isinstance(approval, Mapping):
            blockers.append("named owner approval is required")
        else:
            approval_id = str(approval.get("approvalId", "not-approved"))
            for key in ("approvalId", "approvedBy", "canaryWindow", "rollbackTrigger"):
                if not isinstance(approval.get(key), str) or not approval[key].strip():
                    blockers.append(f"approval.{key}: required")
            if approval.get("scope") != "ACTIVE_SKILL_HOST":
                blockers.append("approval.scope must be ACTIVE_SKILL_HOST")
        if self.host is None:
            blockers.append("activation host is not configured")
        if blockers:
            receipt = _receipt(candidate, status="CAPABILITY_UNAVAILABLE" if "activation host is not configured" in blockers else "BLOCKED", live=False, approval_id=approval_id, prior_selection=prior_selection, stages=[{"stage": "preflight", "status": "BLOCKED", "summary": "live activation was not attempted"}], blockers=blockers, residual_risk=["No live activation evidence exists."], external_mutation=False)
            return {"status": receipt["status"], "receipt": receipt, "hostAttempted": False}
        stages: list[dict[str, str]] = [{"stage": "preflight", "status": "PASS", "summary": "candidate and owner approval validated"}]
        try:
            snapshot = self.host.snapshot(candidate)
            stages.append({"stage": "snapshot", "status": "PASS", "summary": "prior active state was captured"})
            canary = self.host.canary(candidate, observation or {})
            canary_ok = isinstance(canary, Mapping) and canary.get("status") == "PASS"
            stages.append({"stage": "canary", "status": "PASS" if canary_ok else "FAIL", "summary": "host canary observation completed"})
            if not canary_ok:
                rollback = self.host.rollback(snapshot)
                rollback_ok = isinstance(rollback, Mapping) and rollback.get("status") == "PASS"
                stages.append({"stage": "rollback", "status": "PASS" if rollback_ok else "FAIL", "summary": "prior active state rollback completed"})
                receipt = _receipt(candidate, status="ROLLED_BACK" if rollback_ok else "FAILED", live=True, approval_id=approval_id, prior_selection=prior_selection, stages=stages, blockers=[] if rollback_ok else ["live rollback failed"], residual_risk=[] if rollback_ok else ["Active state recovery is not proven."], external_mutation=True)
                return {"status": receipt["status"], "receipt": receipt, "hostAttempted": True}
            activated = self.host.activate(candidate)
            activation_ok = isinstance(activated, Mapping) and activated.get("status") == "PASS"
            stages.append({"stage": "activation", "status": "PASS" if activation_ok else "FAIL", "summary": "host active switch completed"})
            if not activation_ok:
                rollback = self.host.rollback(snapshot)
                rollback_ok = isinstance(rollback, Mapping) and rollback.get("status") == "PASS"
                stages.append({"stage": "rollback", "status": "PASS" if rollback_ok else "FAIL", "summary": "activation failure recovery completed"})
                receipt = _receipt(candidate, status="ROLLED_BACK" if rollback_ok else "FAILED", live=True, approval_id=approval_id, prior_selection=prior_selection, stages=stages, blockers=[] if rollback_ok else ["live rollback failed"], residual_risk=[] if rollback_ok else ["Active state recovery is not proven."], external_mutation=True)
                return {"status": receipt["status"], "receipt": receipt, "hostAttempted": True}
            if retire:
                retirement = self.host.retire(candidate)
                retired_ok = isinstance(retirement, Mapping) and retirement.get("status") == "PASS"
                stages.append({"stage": "retirement", "status": "PASS" if retired_ok else "FAIL", "summary": "candidate retirement completed"})
                final_status = "RETIRED" if retired_ok else "FAILED"
            else:
                final_status = "ACTIVATED"
            receipt = _receipt(candidate, status=final_status, live=True, approval_id=approval_id, prior_selection=prior_selection, stages=stages, blockers=[] if final_status != "FAILED" else ["live retirement failed"], residual_risk=[] if final_status != "FAILED" else ["Retirement proof is incomplete."], external_mutation=True)
            return {"status": receipt["status"], "receipt": receipt, "hostAttempted": True}
        except Exception as exc:
            receipt = _receipt(candidate, status="FAILED", live=True, approval_id=approval_id, prior_selection=prior_selection, stages=stages + [{"stage": "host", "status": "FAIL", "summary": f"host operation failed: {type(exc).__name__}"}], blockers=[f"host operation failed: {type(exc).__name__}"], residual_risk=["The host may require an operator recovery check."], external_mutation=True)
            return {"status": receipt["status"], "receipt": receipt, "hostAttempted": True}
