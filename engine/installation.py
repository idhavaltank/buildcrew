"""Portable installation and retirement planning without implicit host writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


RECEIPT_SCHEMA_VERSION = "installation-receipt-v1"
INSTALL_STATUSES = {"INSTALLED", "ROLLED_BACK", "RETIRED", "BLOCKED", "CAPABILITY_UNAVAILABLE", "NOT_VERIFIED"}
SUPPORTED_PLATFORMS = {"linux", "macos", "windows"}


class InstallationError(ValueError):
    """Raised when a target cannot be safely evaluated."""


class InstallationHost(Protocol):
    """Host-owned atomic staging/switch/rollback interface."""

    def stage(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Stage a validated package outside the active root."""

    def switch(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Atomically switch the approved active root."""

    def rollback(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Restore the prior active root."""

    def retire(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Retire a package after active-state parity is proven."""


def validate_target_root(target_root: str | Path, *, protected_roots: Sequence[str | Path] = ()) -> dict[str, Any]:
    """Check path ownership without creating or deleting anything."""

    try:
        target = Path(target_root).expanduser().resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        return {"valid": False, "errors": [f"target root is not resolvable: {type(exc).__name__}"]}
    errors: list[str] = []
    if target == Path("/"):
        errors.append("target root cannot be filesystem root")
    for raw in protected_roots:
        try:
            protected = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, TypeError):
            continue
        if target == protected or protected in target.parents or target in protected.parents:
            errors.append("target root overlaps a protected source or active root")
    return {"valid": not errors, "errors": errors, "resolved": str(target)}


def validate_installation_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["installationReceipt: expected object"]}
    required = {"schemaVersion", "receiptId", "candidateId", "candidateFingerprint", "targetRootRef", "platform", "status", "live", "atomicSwitch", "oneController", "stages", "blockers", "residualRisk", "externalMutation", "sideEffects"}
    errors.extend(f"installationReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"installationReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"installationReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("receiptId", "candidateId", "candidateFingerprint", "targetRootRef"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"installationReceipt.{key}: expected non-empty string")
    if value.get("platform") not in SUPPORTED_PLATFORMS:
        errors.append("installationReceipt.platform: unsupported platform")
    if value.get("status") not in INSTALL_STATUSES:
        errors.append("installationReceipt.status: unsupported status")
    for key in ("live", "atomicSwitch", "oneController", "externalMutation"):
        if not isinstance(value.get(key), bool):
            errors.append(f"installationReceipt.{key}: expected boolean")
    if not isinstance(value.get("stages"), list) or not value["stages"] or not all(isinstance(item, Mapping) and set(item) == {"stage", "status", "summary"} for item in value["stages"]):
        errors.append("installationReceipt.stages: expected stage/status/summary array")
    for key in ("blockers", "residualRisk"):
        if not isinstance(value.get(key), list) or not all(isinstance(item, str) and item.strip() for item in value[key]):
            errors.append(f"installationReceipt.{key}: expected string array")
    if value.get("sideEffects") != []:
        errors.append("installationReceipt.sideEffects: must be empty; externalMutation records host state separately")
    if value.get("status") == "INSTALLED" and (value.get("live") is not True or value.get("atomicSwitch") is not True or value.get("oneController") is not True):
        errors.append("installationReceipt: INSTALLED requires live atomic one-controller proof")
    return {"valid": not errors, "errors": errors}


class InstallationController:
    """Plan and optionally execute an explicitly approved host installation."""

    def __init__(self, host: InstallationHost | None = None, *, protected_roots: Sequence[str | Path] = ()):
        self.host = host
        self.protected_roots = tuple(protected_roots)

    def portability_report(self, *, target_root: str | Path, platforms: Sequence[str] = ("linux", "macos", "windows")) -> dict[str, Any]:
        target_check = validate_target_root(target_root, protected_roots=self.protected_roots)
        platform_status = {platform: "NOT_VERIFIED" if platform in SUPPORTED_PLATFORMS else "INVALID" for platform in platforms}
        return {"targetRoot": target_check.get("resolved", str(target_root)), "targetValid": target_check["valid"], "targetErrors": target_check["errors"], "platforms": platform_status, "sourceOnly": True, "sideEffects": []}

    def install(
        self,
        candidate: Mapping[str, Any],
        *,
        target_root: str | Path,
        platform: str,
        approval: Mapping[str, Any] | None = None,
        retire_after_switch: bool = False,
    ) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidateId", "")) if isinstance(candidate, Mapping) else ""
        fingerprint = str(candidate.get("sourceFingerprint", "")) if isinstance(candidate, Mapping) else ""
        errors: list[str] = []
        if not candidate_id or not fingerprint:
            errors.append("candidateId and sourceFingerprint are required")
        if platform not in SUPPORTED_PLATFORMS:
            errors.append("unsupported installation platform")
        target_check = validate_target_root(target_root, protected_roots=self.protected_roots)
        errors.extend(target_check["errors"])
        approval_id = "not-approved"
        if not isinstance(approval, Mapping):
            errors.append("owner approval is required")
        else:
            approval_id = str(approval.get("approvalId", "not-approved"))
            if approval.get("scope") != "ACTIVE_SKILL_HOST":
                errors.append("approval.scope must be ACTIVE_SKILL_HOST")
            if not isinstance(approval.get("approvedBy"), str) or not approval["approvedBy"].strip():
                errors.append("approval.approvedBy is required")
        if self.host is None:
            errors.append("installation host is not configured")
        status = "CAPABILITY_UNAVAILABLE" if "installation host is not configured" in errors else "BLOCKED" if errors else "NOT_VERIFIED"
        base = {
            "schemaVersion": RECEIPT_SCHEMA_VERSION,
            "receiptId": f"{candidate_id or 'candidate'}:{fingerprint[:12] or 'unknown'}:install",
            "candidateId": candidate_id or "unknown-candidate",
            "candidateFingerprint": fingerprint or "unknown-source",
            "targetRootRef": "[HOST_ROOT]" if target_check.get("resolved") else "[UNRESOLVED_ROOT]",
            "platform": platform if platform in SUPPORTED_PLATFORMS else "linux",
            "status": status,
            "live": False,
            "atomicSwitch": False,
            "oneController": False,
            "stages": [{"stage": "preflight", "status": "BLOCKED" if errors else "NOT_VERIFIED", "summary": "installation was not executed by the source-only package"}],
            "blockers": sorted(set(errors)),
            "residualRisk": ["No approved live installation evidence exists."],
            "externalMutation": False,
            "sideEffects": [],
        }
        if errors:
            validation = validate_installation_receipt(base)
            if not validation["valid"]:
                raise InstallationError("installation receipt failed validation: " + "; ".join(validation["errors"]))
            return {"status": status, "receipt": base, "hostAttempted": False}
        request = {"schemaVersion": "installation-request-v1", "candidateId": candidate_id, "candidateFingerprint": fingerprint, "targetRootRef": "[HOST_ROOT]", "platform": platform, "approvalId": approval_id, "retireAfterSwitch": retire_after_switch}
        try:
            staged = self.host.stage(request)
            base["stages"].append({"stage": "staging", "status": "PASS" if isinstance(staged, Mapping) and staged.get("status") == "PASS" else "FAIL", "summary": "host staging completed"})
            if not isinstance(staged, Mapping) or staged.get("status") != "PASS":
                raise InstallationError("host staging failed")
            switched = self.host.switch(request)
            switch_ok = isinstance(switched, Mapping) and switched.get("status") == "PASS"
            base["stages"].append({"stage": "switch", "status": "PASS" if switch_ok else "FAIL", "summary": "atomic active-root switch completed"})
            if not switch_ok:
                rolled = self.host.rollback(request)
                rollback_ok = isinstance(rolled, Mapping) and rolled.get("status") == "PASS"
                base["stages"].append({"stage": "rollback", "status": "PASS" if rollback_ok else "FAIL", "summary": "active-root recovery completed"})
                base["status"] = "ROLLED_BACK" if rollback_ok else "NOT_VERIFIED"
                base["live"] = True
                base["externalMutation"] = True
                base["blockers"] = [] if rollback_ok else ["live installation rollback failed"]
                base["residualRisk"] = [] if rollback_ok else ["Active root recovery is not proven."]
            else:
                if retire_after_switch:
                    retired = self.host.retire(request)
                    retire_ok = isinstance(retired, Mapping) and retired.get("status") == "PASS"
                    base["stages"].append({"stage": "retirement", "status": "PASS" if retire_ok else "FAIL", "summary": "legacy state retirement completed"})
                    base["status"] = "RETIRED" if retire_ok else "NOT_VERIFIED"
                else:
                    base["status"] = "INSTALLED"
                base["live"] = True
                base["atomicSwitch"] = True
                base["oneController"] = isinstance(switched.get("oneController"), bool) and switched.get("oneController") is True
                base["externalMutation"] = True
                if not base["oneController"]:
                    base["status"] = "NOT_VERIFIED"
                    base["blockers"] = ["host did not prove exactly one active controller"]
        except Exception as exc:
            base["status"] = "NOT_VERIFIED"
            base["live"] = True
            base["externalMutation"] = True
            base["blockers"] = [f"host installation failed: {type(exc).__name__}"]
        validation = validate_installation_receipt(base)
        if not validation["valid"]:
            raise InstallationError("installation receipt failed validation: " + "; ".join(validation["errors"]))
        return {"status": base["status"], "receipt": base, "hostAttempted": True}
