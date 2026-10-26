"""A tiny host-dispatch boundary used by deterministic AE tests.

The production host owns the real model invocation.  This adapter deliberately
only records the arguments AE would pass, making accidental model escalation
observable without calling a provider or creating external state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence

from .model_selection import (
    ASSIGNMENT_DISPATCH_BINDING_KEYS,
    SELECTION_BINDING_KEYS,
    validate_assignment,
)


class DispatchBoundaryError(ValueError):
    """Raised when a selection cannot safely cross the host boundary."""


class AssignmentClaimStore(Protocol):
    """Host-owned atomic idempotency boundary for assignment identities."""

    def claim(self, assignment_ids: Sequence[str]) -> None:
        """Claim all IDs or raise without claiming any ID."""


class InMemoryAssignmentClaimStore:
    """Process-local claim store used by the deterministic recording adapter."""

    def __init__(self) -> None:
        self._claimed: set[str] = set()
        self._lock = RLock()

    def claim(self, assignment_ids: Sequence[str]) -> None:
        ids = list(assignment_ids)
        if len(ids) != len(set(ids)):
            raise DispatchBoundaryError("commit contains duplicate assignment identities")
        with self._lock:
            if any(assignment_id in self._claimed for assignment_id in ids):
                raise DispatchBoundaryError("assignment has already been dispatched")
            self._claimed.update(ids)


NON_DISPATCHABLE_SELECTION_STATUSES = {
    "DIRECT_NO_ASSIGNMENT",
    "CAPABILITY_UNAVAILABLE",
    "HOST_INHERITED",
    "REJECTED",
}
DISPATCHABLE_SELECTION_STATUSES = {"SELECTED", "OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"}


class RecordingHostAdapter:
    """Record validated dispatch arguments and enforce one-time assignment claims."""

    def __init__(self, *, now: str | None = None, claim_store: AssignmentClaimStore | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.now = now
        self.claim_store = claim_store if claim_store is not None else InMemoryAssignmentClaimStore()
        self._dispatch_lock = RLock()

    def _prepare_call(
        self,
        assignment: Mapping[str, Any],
        *,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        expected_assignment_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        validation = validate_assignment(
            assignment,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=current,
        )
        if not validation["valid"]:
            raise DispatchBoundaryError("assignment failed freshness/contract validation")
        if expected_assignment_binding is not None:
            if (
                not isinstance(expected_assignment_binding, Mapping)
                or any(key not in expected_assignment_binding for key in ASSIGNMENT_DISPATCH_BINDING_KEYS)
            ):
                raise DispatchBoundaryError("assignment binding is incomplete")
            if any(
                assignment.get(key) != expected_assignment_binding.get(key)
                for key in ASSIGNMENT_DISPATCH_BINDING_KEYS
            ):
                raise DispatchBoundaryError("assignment does not match enclosing selection binding")
        status = assignment.get("status")
        if status not in {"SELECTED", "OVERRIDE_EVIDENCED", "FALLBACK_EVIDENCED"}:
            raise DispatchBoundaryError(f"assignment is not dispatchable: {status}")
        model = assignment.get("resolvedModel")
        effort = assignment.get("reasoningEffort")
        role = assignment.get("role")
        if model is None or effort is None:
            raise DispatchBoundaryError("dispatch requires resolved model and reasoning effort")
        if status == "SELECTED" and assignment.get("desiredModel") != model:
            raise DispatchBoundaryError("selected model differs from the task-bound desired model")
        if role == "normal-coordinator" and model != "model-balanced":
            raise DispatchBoundaryError("normal coordination requires BalancedModel")
        if role in {"high-risk-coordinator", "final-audit"} and model != "model-advanced":
            raise DispatchBoundaryError("coupled/high-risk coordination requires AdvancedModel")
        call = {
            "assignmentId": assignment.get("assignmentId"),
            "role": role,
            "model": model,
            "reasoningEffort": effort,
            "scopeId": assignment.get("scope", {}).get("scopeId"),
        }
        return call

    def preflight(
        self,
        assignment: Mapping[str, Any],
        *,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        expected_assignment_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate one call without crossing the recording boundary."""

        return self._prepare_call(
            assignment,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            expected_assignment_binding=expected_assignment_binding,
        )

    def commit(self, calls: list[dict[str, Any]]) -> None:
        """Record calls only after the complete selection has passed preflight."""

        assignment_ids = [call.get("assignmentId") for call in calls]
        if any(not isinstance(assignment_id, str) or not assignment_id for assignment_id in assignment_ids):
            raise DispatchBoundaryError("commit requires assignment identities")
        if len(assignment_ids) != len(set(assignment_ids)):
            raise DispatchBoundaryError("commit contains duplicate assignment identities")
        with self._dispatch_lock:
            self.claim_store.claim(assignment_ids)
            self.calls.extend(calls)

    def dispatch(
        self,
        assignment: Mapping[str, Any],
        *,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        expected_assignment_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and record one standalone assignment."""

        call = self._prepare_call(
            assignment,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            expected_assignment_binding=expected_assignment_binding,
        )
        self.commit([call])
        return {"dispatched": True, "call": call}


def dispatch_selection(
    selection: Mapping[str, Any],
    host: RecordingHostAdapter,
    *,
    expected_binding: Mapping[str, Any] | None = None,
    expected_policy_fingerprint: str | None = None,
    expected_assignment_bindings: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Dispatch a selection against trusted task/policy identity.

    The expected binding and policy fingerprint must come from the AE
    controller/host context, not from the selection envelope being dispatched.
    """

    if not isinstance(selection, Mapping):
        raise DispatchBoundaryError("selection must be an object")
    status = selection.get("status")
    assignments = selection.get("assignments", [])
    if not isinstance(assignments, list):
        raise DispatchBoundaryError("selection assignments must be an array")
    if status in NON_DISPATCHABLE_SELECTION_STATUSES:
        if status in {"DIRECT_NO_ASSIGNMENT", "REJECTED"} and assignments:
            raise DispatchBoundaryError("non-dispatchable selection cannot contain assignments")
        if status == "CAPABILITY_UNAVAILABLE":
            allowed = {"SELECTED", "CAPABILITY_UNAVAILABLE", "HOST_INHERITED"}
            if not assignments or any(not isinstance(assignment, Mapping) or assignment.get("status") not in allowed for assignment in assignments):
                raise DispatchBoundaryError("non-dispatchable selection contains an inconsistent assignment")
            if not any(assignment.get("status") == "CAPABILITY_UNAVAILABLE" for assignment in assignments):
                raise DispatchBoundaryError("unavailable selection must contain an unavailable assignment")
        if status == "HOST_INHERITED":
            allowed = {"SELECTED", "HOST_INHERITED"}
            if not assignments or any(not isinstance(assignment, Mapping) or assignment.get("status") not in allowed for assignment in assignments):
                raise DispatchBoundaryError("non-dispatchable selection contains an inconsistent assignment")
            if not any(assignment.get("status") == "HOST_INHERITED" for assignment in assignments):
                raise DispatchBoundaryError("inherited selection must contain an inherited assignment")
        return {"dispatched": False, "calls": [], "reason": status}
    if status not in DISPATCHABLE_SELECTION_STATUSES:
        raise DispatchBoundaryError(f"selection is not dispatchable: {status}")
    if not assignments:
        raise DispatchBoundaryError("dispatchable selection must contain assignments")
    if (
        not isinstance(expected_binding, Mapping)
        or any(key not in expected_binding for key in SELECTION_BINDING_KEYS)
        or not isinstance(expected_policy_fingerprint, str)
        or not expected_policy_fingerprint
    ):
        raise DispatchBoundaryError("dispatch requires a complete trusted task and policy binding")
    task_binding = selection.get("taskBinding")
    policy_fingerprint = selection.get("policyFingerprint")
    if (
        not isinstance(task_binding, Mapping)
        or set(task_binding) != set(SELECTION_BINDING_KEYS)
        or policy_fingerprint != expected_policy_fingerprint
        or expected_binding.get("policyFingerprint") != expected_policy_fingerprint
        or any(task_binding.get(key) != expected_binding.get(key) for key in SELECTION_BINDING_KEYS)
    ):
        raise DispatchBoundaryError("selection does not match the trusted task/policy binding")
    assignment_bindings = selection.get("assignmentBindings")
    if not isinstance(assignment_bindings, list) or len(assignment_bindings) != len(assignments):
        raise DispatchBoundaryError("selection is missing one binding for each assignment")
    if (
        not isinstance(expected_assignment_bindings, list)
        or len(expected_assignment_bindings) != len(assignments)
    ):
        raise DispatchBoundaryError("dispatch requires complete trusted assignment bindings")
    assignment_ids: set[str] = set()
    assignment_statuses: set[str] = set()
    for index, binding in enumerate(assignment_bindings):
        if (
            not isinstance(binding, Mapping)
            or any(key not in binding for key in ASSIGNMENT_DISPATCH_BINDING_KEYS)
        ):
            raise DispatchBoundaryError("selection contains an incomplete assignment binding")
        expected_assignment_binding = expected_assignment_bindings[index]
        if (
            not isinstance(expected_assignment_binding, Mapping)
            or any(key not in expected_assignment_binding for key in ASSIGNMENT_DISPATCH_BINDING_KEYS)
            or any(
                binding.get(key) != expected_assignment_binding.get(key)
                for key in ASSIGNMENT_DISPATCH_BINDING_KEYS
            )
        ):
            raise DispatchBoundaryError("selection does not match trusted assignment bindings")
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            raise DispatchBoundaryError("selection assignments must contain objects")
        assignment_id = assignment.get("assignmentId")
        if not isinstance(assignment_id, str) or assignment_id in assignment_ids:
            raise DispatchBoundaryError("selection contains a missing or duplicate assignment identity")
        assignment_ids.add(assignment_id)
        assignment_statuses.add(str(assignment.get("status")))
    if status == "SELECTED" and assignment_statuses != {"SELECTED"}:
        raise DispatchBoundaryError("selected envelope contains a non-selected assignment")
    if status == "OVERRIDE_EVIDENCED" and (
        not assignment_statuses.issubset({"SELECTED", "OVERRIDE_EVIDENCED"})
        or "OVERRIDE_EVIDENCED" not in assignment_statuses
    ):
        raise DispatchBoundaryError("override envelope contains an invalid assignment status")
    if status == "FALLBACK_EVIDENCED" and (
        not assignment_statuses.issubset({"SELECTED", "FALLBACK_EVIDENCED"})
        or "FALLBACK_EVIDENCED" not in assignment_statuses
    ):
        raise DispatchBoundaryError("fallback envelope contains an invalid assignment status")
    prepared_calls = [
        host.preflight(
            assignment,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            expected_assignment_binding=expected_assignment_bindings[index],
        )
        for index, assignment in enumerate(assignments)
    ]
    host.commit(prepared_calls)
    return {"dispatched": bool(prepared_calls), "calls": prepared_calls}
