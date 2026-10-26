"""Pure disposable promotion, canary, rollback, and recovery simulation."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

from .safety_boundaries import redact_value


SCHEMA_VERSION = "promotion-simulation-v1"
MODE = "DISPOSABLE_SIMULATION"
PRIOR_SELECTION = "buildcrew"


def _safe_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep candidate evidence portable and free of credentials or host paths."""

    value = redact_value(dict(candidate))

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            if any(part.startswith(("/home/", "/Users/", "/private/", "/tmp/", "/var/", "/mnt/", "/media/")) for part in item.split()):
                return "[REDACTED_PATH]"
            return item
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, Mapping):
            return {str(key): clean(child) for key, child in item.items()}
        return item

    return clean(value)


def _stage(stage: str, status: str, summary: str, *, blockers: list[str] | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "scope": "DISPOSABLE_ONLY",
        "summary": summary,
        "blockers": list(blockers or []),
    }


def simulate_promotion(
    candidate: Mapping[str, Any],
    *,
    canary_pass: bool = True,
    activation_requested: bool = False,
    recovery_pass: bool = True,
) -> dict[str, Any]:
    """Simulate a complete reversible lifecycle without touching live state.

    ``activation_requested`` means "exercise the activation and rollback path"
    in the disposable model; it is not permission to activate a real host.
    """

    if not isinstance(candidate, Mapping):
        candidate = {}
        candidate_valid = False
    else:
        candidate_valid = True

    candidate_view = _safe_candidate(candidate)
    candidate_id = str(candidate_view.get("candidateId") or candidate_view.get("version") or "candidate")
    candidate_fingerprint = str(candidate_view.get("sourceFingerprint") or "")
    if not candidate_fingerprint:
        candidate_fingerprint = sha256(repr(candidate_view).encode("utf-8")).hexdigest()

    stages: list[dict[str, Any]] = [
        _stage(
            "candidate-validation",
            "PASS" if candidate_valid else "BLOCKED",
            "candidate shape accepted for disposable simulation" if candidate_valid else "candidate must be an object",
            blockers=[] if candidate_valid else ["candidate is not an object"],
        ),
        _stage(
            "installation",
            "PASS" if candidate_valid else "NOT_STARTED",
            "candidate installed into modeled disposable state" if candidate_valid else "installation skipped after candidate validation",
        ),
    ]

    if not candidate_valid:
        stages.extend([
            _stage("canary", "NOT_STARTED", "canary skipped after validation failure"),
            _stage("activation", "NOT_STARTED", "activation is never attempted"),
            _stage("rollback", "PASS", "disposable state cleanup completed"),
            _stage("recovery", "PASS", "prior selection remains unchanged"),
        ])
        terminal = "SIMULATION_BLOCKED"
        blockers = ["candidate must be an object"]
        residual_risk = ["No candidate lifecycle evidence was available."]
    elif not canary_pass:
        stages.extend([
            _stage("canary", "FAIL", "modeled canary health criteria failed", blockers=["canary health assertion failed"]),
            _stage("activation", "BLOCKED", "activation blocked because canary failed", blockers=["canary did not pass"]),
            _stage("rollback", "PASS", "disposable candidate state was removed after canary failure"),
            _stage("recovery", "PASS" if recovery_pass else "FAIL", "prior selection recovery was modeled" if recovery_pass else "recovery assertion failed", blockers=[] if recovery_pass else ["recovery assertion failed"]),
        ])
        terminal = "SIMULATION_BLOCKED" if recovery_pass else "SIMULATION_RECOVERY_FAILED"
        blockers = ["canary health assertion failed"]
        residual_risk = [] if recovery_pass else ["Recovery proof failed inside the disposable simulation."]
    elif not activation_requested:
        stages.extend([
            _stage("canary", "PASS", "modeled canary health criteria passed"),
            _stage("activation", "NOT_STARTED", "activation path was not requested"),
            _stage("rollback", "NOT_STARTED", "rollback path was not requested"),
            _stage("recovery", "NOT_STARTED", "recovery path was not requested"),
        ])
        terminal = "SIMULATION_READY"
        blockers = []
        residual_risk = ["Activation, rollback, and recovery were not exercised."]
    else:
        stages.extend([
            _stage("canary", "PASS", "modeled canary health criteria passed"),
            _stage("activation", "PASS", "activation was modeled in disposable state only"),
            _stage("rollback", "PASS", "candidate was rolled back to the prior modeled selection"),
            _stage("recovery", "PASS" if recovery_pass else "FAIL", "prior selection recovery was modeled" if recovery_pass else "recovery assertion failed", blockers=[] if recovery_pass else ["recovery assertion failed"]),
        ])
        terminal = "SIMULATED_AND_ROLLED_BACK" if recovery_pass else "SIMULATION_RECOVERY_FAILED"
        blockers = [] if recovery_pass else ["recovery assertion failed"]
        residual_risk = [] if recovery_pass else ["Recovery proof failed inside the disposable simulation."]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": MODE,
        "simulationId": sha256(repr((candidate_id, candidate_fingerprint, canary_pass, activation_requested, recovery_pass)).encode("utf-8")).hexdigest(),
        "candidate": candidate_view,
        "candidateFingerprint": candidate_fingerprint,
        "priorSelection": PRIOR_SELECTION,
        "stages": stages,
        "terminalStatus": terminal,
        "blockers": blockers,
        "residualRisk": residual_risk,
        "configurationScope": "DISPOSABLE_ONLY",
        "liveChanged": False,
        "sideEffects": [],
    }


def validate_promotion_simulation(value: Any) -> dict[str, Any]:
    """Validate simulation output and enforce the no-live-state invariant."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["promotionSimulation: expected object"]}
    required = {
        "schemaVersion", "mode", "simulationId", "candidate", "candidateFingerprint", "priorSelection",
        "stages", "terminalStatus", "blockers", "residualRisk", "configurationScope", "liveChanged", "sideEffects",
    }
    allowed = required | {"validation"}
    errors.extend(f"promotionSimulation: unknown field {key}" for key in sorted(set(value) - allowed))
    errors.extend(f"promotionSimulation: missing required field {key}" for key in sorted(required - set(value)))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"promotionSimulation.schemaVersion: expected {SCHEMA_VERSION}")
    if value.get("mode") != MODE:
        errors.append(f"promotionSimulation.mode: expected {MODE}")
    if value.get("configurationScope") != "DISPOSABLE_ONLY":
        errors.append("promotionSimulation.configurationScope: must be DISPOSABLE_ONLY")
    if value.get("liveChanged") is not False:
        errors.append("promotionSimulation.liveChanged: must be false")
    if value.get("sideEffects") != []:
        errors.append("promotionSimulation.sideEffects: must be empty")
    for key in ("simulationId", "candidateFingerprint", "priorSelection", "terminalStatus"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"promotionSimulation.{key}: expected non-empty string")
    if not isinstance(value.get("candidate"), Mapping):
        errors.append("promotionSimulation.candidate: expected object")
    for key in ("stages", "blockers", "residualRisk"):
        if not isinstance(value.get(key), list):
            errors.append(f"promotionSimulation.{key}: expected array")
    stages = value.get("stages")
    if isinstance(stages, list):
        expected = ["candidate-validation", "installation", "canary", "activation", "rollback", "recovery"]
        actual = [stage.get("stage") for stage in stages if isinstance(stage, Mapping)]
        if actual != expected:
            errors.append("promotionSimulation.stages: expected ordered lifecycle stages")
        for index, stage in enumerate(stages):
            if not isinstance(stage, Mapping):
                errors.append(f"promotionSimulation.stages[{index}]: expected object")
                continue
            if stage.get("scope") != "DISPOSABLE_ONLY":
                errors.append(f"promotionSimulation.stages[{index}].scope: must be DISPOSABLE_ONLY")
            if stage.get("status") not in {"PASS", "FAIL", "BLOCKED", "NOT_STARTED"}:
                errors.append(f"promotionSimulation.stages[{index}].status: unsupported value")
    validation = value.get("validation")
    if validation is not None and (
        not isinstance(validation, Mapping)
        or not isinstance(validation.get("valid"), bool)
        or not isinstance(validation.get("errors"), list)
    ):
        errors.append("promotionSimulation.validation: expected valid boolean and errors array")
    return {"valid": not errors, "errors": errors}
