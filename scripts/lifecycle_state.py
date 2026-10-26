#!/usr/bin/env python3
"""Fail-closed lifecycle state transitions for the hybrid engineering workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
STATES = frozenset(
    {
        "RECEIVED",
        "IN_PROGRESS",
        "BLOCKED",
        "MISSING_COVERAGE",
        "INVALIDATED",
        "INTERRUPTED",
        "TIMED_OUT",
        "CRASHED",
        "UNVERIFIED",
        "READY_FOR_CLOSURE",
        "CLOSED",
    }
)

FAILURE_STATES = frozenset(
    {
        "BLOCKED",
        "MISSING_COVERAGE",
        "INVALIDATED",
        "INTERRUPTED",
        "TIMED_OUT",
        "CRASHED",
        "UNVERIFIED",
    }
)

_TRANSITIONS = {
    "RECEIVED": {"IN_PROGRESS", "BLOCKED", "MISSING_COVERAGE", "INVALIDATED"},
    "IN_PROGRESS": {
        "IN_PROGRESS",
        "BLOCKED",
        "MISSING_COVERAGE",
        "INVALIDATED",
        "INTERRUPTED",
        "TIMED_OUT",
        "CRASHED",
        "UNVERIFIED",
        "READY_FOR_CLOSURE",
    },
    "BLOCKED": {"IN_PROGRESS", "INVALIDATED"},
    "MISSING_COVERAGE": {"IN_PROGRESS", "BLOCKED", "INVALIDATED"},
    "INVALIDATED": {"IN_PROGRESS"},
    "INTERRUPTED": {"IN_PROGRESS", "BLOCKED"},
    "TIMED_OUT": {"IN_PROGRESS", "BLOCKED"},
    "CRASHED": {"IN_PROGRESS", "BLOCKED"},
    "UNVERIFIED": {"IN_PROGRESS", "BLOCKED"},
    "READY_FOR_CLOSURE": {"CLOSED", "IN_PROGRESS", "BLOCKED"},
    "CLOSED": set(),
}


class StateTransitionError(ValueError):
    """Raised when a lifecycle transition would weaken evidence guarantees."""


_CLOSURE_MARKER = object()


@dataclass(frozen=True)
class ValidatedClosureEvidence:
    """Ephemeral controller result minted only after artifact validation."""

    task_id: str
    source_fingerprint: str
    evidence_digest: str
    gate_names: tuple[str, ...]
    _marker: object = field(repr=False, compare=False)


def _mint_validated_closure_evidence(
    *,
    task_id: str,
    source_fingerprint: str,
    evidence_digest: str,
    gate_names: tuple[str, ...],
) -> ValidatedClosureEvidence:
    if not all(
        isinstance(value, str) and value
        for value in (task_id, source_fingerprint, evidence_digest)
    ):
        raise StateTransitionError("validated closure evidence identity is incomplete")
    if not gate_names or any(not isinstance(name, str) or not name for name in gate_names):
        raise StateTransitionError("validated closure evidence gate inventory is incomplete")
    return ValidatedClosureEvidence(
        task_id=task_id,
        source_fingerprint=source_fingerprint,
        evidence_digest=evidence_digest,
        gate_names=gate_names,
        _marker=_CLOSURE_MARKER,
    )


def _is_validated_closure_evidence(value: object) -> bool:
    return (
        isinstance(value, ValidatedClosureEvidence)
        and value._marker is _CLOSURE_MARKER
        and bool(value.task_id)
        and bool(value.source_fingerprint)
        and bool(value.evidence_digest)
        and bool(value.gate_names)
    )


def _require_state(value: str, label: str) -> None:
    if value not in STATES:
        raise StateTransitionError(f"{label}: unsupported state {value!r}")


def can_transition(
    current: str,
    target: str,
    *,
    closure_evidence: object | None = None,
) -> bool:
    """Return whether a state transition is legal under fail-closed rules."""

    _require_state(current, "current")
    _require_state(target, "target")
    if target == "CLOSED":
        return current == "READY_FOR_CLOSURE" and _is_validated_closure_evidence(
            closure_evidence
        )
    return target in _TRANSITIONS[current]


def transition(
    current: str,
    target: str,
    *,
    closure_evidence: object | None = None,
) -> str:
    """Apply a legal transition or raise without mutating external state."""

    if not can_transition(
        current,
        target,
        closure_evidence=closure_evidence,
    ):
        reason = "closure evidence is incomplete" if target == "CLOSED" else "transition is not allowed"
        raise StateTransitionError(f"{current} -> {target}: {reason}")
    return target


def closure_eligible(evidence: object) -> bool:
    """Raw status maps and booleans never authorize lifecycle closure."""

    return _is_validated_closure_evidence(evidence)
