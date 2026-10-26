"""Host-controlled runtime enforcement for bounded agent execution.

The package remains provider- and runtime-neutral.  This module supplies the
small enforcement boundary a host can call before each step or tool action:
budgets, repeated-action detection, task-packet permissions, context limits,
and redacted execution events.  It never creates a provider, credential, or
external transport by itself.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Callable, Deque, Mapping, Sequence

from .evidence_observability import build_event, redact
from .observability_store import ObservabilityController
from .safety_boundaries import validate_untrusted_capability
from .task_packet import validate_task_packet


RUNTIME_CONTROL_SCHEMA_VERSION = "runtime-control-receipt-v1"
ACTION_TYPES = {"read", "write", "execute", "external"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "TIMED_OUT"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class RuntimeControlError(ValueError):
    """Raised when the host attempts an unsafe or exhausted runtime action."""


class BudgetExceeded(RuntimeControlError):
    """Raised when a runtime limit would be exceeded."""


class ToolDenied(RuntimeControlError):
    """Raised when the task packet does not authorize a tool action."""


class LoopDetected(RuntimeControlError):
    """Raised when a repeated action crosses the configured threshold."""


class ContextBudgetExceeded(RuntimeControlError):
    """Raised when the goal and acceptance criteria cannot fit the budget."""


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise RuntimeControlError(f"{label} must be a safe non-empty identifier")
    return value.strip()


def _estimate_tokens(value: str) -> int:
    return (len(value) + 3) // 4


def validate_runtime_snapshot(value: Any) -> dict[str, Any]:
    """Validate a control-plane snapshot without claiming live execution."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["runtimeSnapshot: expected object"]}
    required = {
        "schemaVersion", "runId", "taskId", "status", "sourceFingerprint", "limits", "usage",
        "elapsedSeconds", "terminationReason", "loop", "eventCount", "observabilityReceipts",
        "sourceOnly", "liveExecution", "sideEffects",
    }
    errors.extend(f"runtimeSnapshot: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"runtimeSnapshot: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RUNTIME_CONTROL_SCHEMA_VERSION:
        errors.append(f"runtimeSnapshot.schemaVersion: expected {RUNTIME_CONTROL_SCHEMA_VERSION}")
    for key in ("runId", "taskId", "sourceFingerprint"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"runtimeSnapshot.{key}: expected non-empty string")
    if value.get("status") not in {"READY", "RUNNING", *TERMINAL_STATUSES}:
        errors.append("runtimeSnapshot.status: unsupported value")
    if value.get("terminationReason") is not None and not isinstance(value.get("terminationReason"), str):
        errors.append("runtimeSnapshot.terminationReason: expected string or null")
    if not isinstance(value.get("elapsedSeconds"), (int, float)) or isinstance(value.get("elapsedSeconds"), bool) or value.get("elapsedSeconds") < 0:
        errors.append("runtimeSnapshot.elapsedSeconds: expected non-negative number")
    for section, fields in {
        "limits": {"maxWallSeconds", "maxSteps", "maxToolCalls", "maxWorkerCalls", "maxEstimatedTokens", "maxCostMicros", "maxRepeatActions", "repeatWindow", "maxContextTokens"},
        "usage": {"steps", "toolCalls", "workerCalls", "estimatedTokens", "costMicros"},
    }.items():
        nested = value.get(section)
        if not isinstance(nested, Mapping):
            errors.append(f"runtimeSnapshot.{section}: expected object")
        else:
            errors.extend(f"runtimeSnapshot.{section}: missing required field {key}" for key in sorted(fields - set(nested)))
            errors.extend(f"runtimeSnapshot.{section}: unknown field {key}" for key in sorted(set(nested) - fields))
            for key in fields:
                if key in nested and (not isinstance(nested[key], (int, float)) or isinstance(nested[key], bool) or nested[key] < 0):
                    errors.append(f"runtimeSnapshot.{section}.{key}: expected non-negative number")
    loop = value.get("loop")
    if not isinstance(loop, Mapping) or set(loop) != {"recentFingerprints", "uniqueActions", "windowSize", "maxRepeats"}:
        errors.append("runtimeSnapshot.loop: expected exact loop summary")
    elif not isinstance(loop.get("recentFingerprints"), list) or not isinstance(loop.get("uniqueActions"), int) or not isinstance(loop.get("windowSize"), int) or not isinstance(loop.get("maxRepeats"), int):
        errors.append("runtimeSnapshot.loop: invalid summary values")
    if not isinstance(value.get("eventCount"), int) or isinstance(value.get("eventCount"), bool) or value["eventCount"] < 0:
        errors.append("runtimeSnapshot.eventCount: expected integer >= 0")
    if not isinstance(value.get("observabilityReceipts"), list):
        errors.append("runtimeSnapshot.observabilityReceipts: expected array")
    if value.get("sourceOnly") is not True:
        errors.append("runtimeSnapshot.sourceOnly: must be true")
    if value.get("liveExecution") is not False:
        errors.append("runtimeSnapshot.liveExecution: must be false")
    if value.get("sideEffects") != []:
        errors.append("runtimeSnapshot.sideEffects: must be empty")
    return {"valid": not errors, "errors": errors}


@dataclass(frozen=True)
class RuntimeLimits:
    """Explicit limits applied by :class:`RuntimeControlPlane`."""

    max_wall_seconds: float
    max_steps: int
    max_tool_calls: int
    max_worker_calls: int
    max_estimated_tokens: int
    max_cost_micros: int
    max_repeat_actions: int = 3
    repeat_window: int = 12
    max_context_tokens: int = 8_000

    def __post_init__(self) -> None:
        if self.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be positive")
        for field_name in (
            "max_steps",
            "max_tool_calls",
            "max_worker_calls",
            "max_estimated_tokens",
            "max_cost_micros",
            "max_repeat_actions",
            "repeat_window",
            "max_context_tokens",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.max_repeat_actions > self.repeat_window:
            raise ValueError("max_repeat_actions cannot exceed repeat_window")

    @classmethod
    def for_complexity(cls, complexity: str) -> "RuntimeLimits":
        """Return conservative defaults for a validated task complexity."""

        profiles = {
            "LIGHTWEIGHT": cls(60, 20, 10, 1, 20_000, 100_000, max_context_tokens=4_000),
            "NORMAL": cls(900, 80, 40, 2, 100_000, 1_000_000, max_context_tokens=8_000),
            "COUPLED": cls(1_800, 160, 80, 4, 250_000, 5_000_000, max_context_tokens=14_000),
        }
        try:
            return profiles[str(complexity).upper()]
        except KeyError as exc:
            raise ValueError("complexity must be LIGHTWEIGHT, NORMAL, or COUPLED") from exc

    def as_dict(self) -> dict[str, int | float]:
        return {
            "maxWallSeconds": self.max_wall_seconds,
            "maxSteps": self.max_steps,
            "maxToolCalls": self.max_tool_calls,
            "maxWorkerCalls": self.max_worker_calls,
            "maxEstimatedTokens": self.max_estimated_tokens,
            "maxCostMicros": self.max_cost_micros,
            "maxRepeatActions": self.max_repeat_actions,
            "repeatWindow": self.repeat_window,
            "maxContextTokens": self.max_context_tokens,
        }


class LoopDetector:
    """Detect repeated tool/action fingerprints inside a bounded window."""

    def __init__(self, *, max_repeats: int = 3, window: int = 12):
        if max_repeats <= 0 or window <= 0 or max_repeats > window:
            raise ValueError("loop detector limits are invalid")
        self.max_repeats = max_repeats
        self.window = window
        self._recent: Deque[str] = deque(maxlen=window)
        self._counts: Counter[str] = Counter()

    def observe(self, action: Mapping[str, Any]) -> dict[str, Any]:
        safe_action = redact(dict(action))
        fingerprint = _fingerprint(safe_action)
        if len(self._recent) == self.window:
            expired = self._recent.popleft()
            self._counts[expired] -= 1
            if self._counts[expired] <= 0:
                del self._counts[expired]
        self._recent.append(fingerprint)
        self._counts[fingerprint] += 1
        count = self._counts[fingerprint]
        return {
            "fingerprint": fingerprint,
            "repeatCount": count,
            "threshold": self.max_repeats,
            "blocked": count > self.max_repeats,
            "windowSize": len(self._recent),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "recentFingerprints": list(self._recent),
            "uniqueActions": len(self._counts),
            "windowSize": self.window,
            "maxRepeats": self.max_repeats,
        }


class ToolPermissionBroker:
    """Authorize tool actions from the trusted task packet only."""

    def __init__(self, task_packet: Mapping[str, Any]):
        validation = validate_task_packet(task_packet)
        if not validation["valid"]:
            raise RuntimeControlError("invalid task packet: " + "; ".join(validation["errors"]))
        self.packet = dict(task_packet)

    def authorize(
        self,
        tool_name: str,
        action: str,
        *,
        target: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        untrusted_context: Any | None = None,
    ) -> dict[str, Any]:
        tool = _safe_id(tool_name, "tool_name")
        normalized_action = str(action).strip().lower()
        if normalized_action not in ACTION_TYPES:
            raise ToolDenied("unsupported tool action")

        if untrusted_context is not None:
            untrusted = validate_untrusted_capability(untrusted_context, path="untrustedContext")
            if not untrusted["valid"]:
                raise ToolDenied("untrusted content contains authority-bearing instructions")

        allowed = self.packet["allowedActions"]
        if allowed.get(normalized_action) is not True:
            raise ToolDenied(f"task packet does not allow {normalized_action} actions")
        scope = self.packet["scope"]
        approval = self.packet["approval"]
        if normalized_action == "write" and scope.get("writable") is not True:
            raise ToolDenied("write action is outside the task scope")
        if normalized_action == "external":
            if scope.get("externalSideEffects") is not True:
                raise ToolDenied("external action is outside the task scope")
            if approval.get("status") != "APPROVED" or not approval.get("approvalId"):
                raise ToolDenied("external action requires explicit approval")

        safe_arguments = redact(dict(arguments or {}))
        return {
            "status": "ALLOWED",
            "tool": tool,
            "action": normalized_action,
            "target": redact(target) if target is not None else None,
            "argumentsFingerprint": _fingerprint(safe_arguments),
            "approvalId": approval.get("approvalId") if normalized_action == "external" else None,
            "authoritySource": "trusted-task-packet",
        }


def compact_context(
    objective: str,
    acceptance_criteria: Sequence[str],
    references: Sequence[str | Mapping[str, Any]],
    *,
    max_tokens: int,
    token_counter: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """Keep the goal and criteria while dropping lower-priority references."""

    if not isinstance(objective, str) or not objective.strip():
        raise ContextBudgetExceeded("objective is required")
    if not isinstance(acceptance_criteria, Sequence) or not acceptance_criteria:
        raise ContextBudgetExceeded("acceptance criteria are required")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ContextBudgetExceeded("max_tokens must be positive")
    counter = token_counter or _estimate_tokens

    goal = f"Objective: {objective.strip()}"
    criteria = "Acceptance criteria:\n" + "\n".join(f"- {str(item).strip()}" for item in acceptance_criteria)
    base = f"{goal}\n\n{criteria}"
    if counter(base) > max_tokens:
        raise ContextBudgetExceeded("objective and acceptance criteria exceed the context budget")

    included: list[str] = []
    dropped: list[str] = []
    current = base
    for index, reference in enumerate(references):
        if isinstance(reference, Mapping):
            label = str(reference.get("label") or reference.get("path") or f"reference-{index + 1}")
            text = str(reference.get("text") or reference.get("summary") or "")
        else:
            label = f"reference-{index + 1}"
            text = str(reference)
        if not text.strip():
            continue
        candidate = f"{current}\n\n[{label}]\n{text.strip()}"
        if counter(candidate) <= max_tokens:
            current = candidate
            included.append(label)
        else:
            dropped.append(label)

    return {
        "schemaVersion": "runtime-context-window-v1",
        "context": redact(current),
        "estimatedTokens": counter(current),
        "maxTokens": max_tokens,
        "includedReferences": included,
        "droppedReferences": dropped,
        "status": "BOUNDED" if dropped else "WITHIN_LIMIT",
        "sourceOnly": True,
    }


@dataclass
class _Usage:
    steps: int = 0
    tool_calls: int = 0
    worker_calls: int = 0
    estimated_tokens: int = 0
    cost_micros: int = 0


class RuntimeControlPlane:
    """A host-called gate around steps, tools, budgets, and evidence events."""

    def __init__(
        self,
        task_packet: Mapping[str, Any],
        *,
        source_fingerprint: str,
        run_id: str,
        limits: RuntimeLimits | None = None,
        clock: Callable[[], float] | None = None,
        observability: ObservabilityController | None = None,
    ):
        validation = validate_task_packet(task_packet)
        if not validation["valid"]:
            raise RuntimeControlError("invalid task packet: " + "; ".join(validation["errors"]))
        self.packet = dict(task_packet)
        self.task_id = _safe_id(self.packet["taskId"], "task_id")
        self.source_fingerprint = str(source_fingerprint).strip()
        if not self.source_fingerprint:
            raise RuntimeControlError("source_fingerprint is required")
        self.run_id = _safe_id(run_id, "run_id")
        self.limits = limits or RuntimeLimits.for_complexity(self.packet["complexity"])
        self._clock = clock or time.monotonic
        self._observability = observability
        self._broker = ToolPermissionBroker(self.packet)
        self._loop_detector = LoopDetector(
            max_repeats=self.limits.max_repeat_actions,
            window=self.limits.repeat_window,
        )
        self._usage = _Usage()
        self._events: list[dict[str, Any]] = []
        self._observability_receipts: list[dict[str, Any]] = []
        self._sequence = 0
        self._started_at: float | None = None
        self._status = "READY"
        self._termination_reason: str | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def _emit(self, event_type: str, status: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
        event = build_event(
            event_type,
            self.task_id,
            status,
            source_fingerprint=self.source_fingerprint,
            run_id=self.run_id,
            sequence=self._sequence,
            details={"executionMode": "HOST_CONTROLLED", **dict(details or {})},
        )
        self._sequence += 1
        self._events.append(event)
        if self._observability is not None:
            result = self._observability.publish(event, run_id=self.run_id)
            self._observability_receipts.append(result)
        return event

    def start(self) -> dict[str, Any]:
        if self._status != "READY":
            raise RuntimeControlError("runtime control plane can start only once")
        self._started_at = self._clock()
        self._status = "RUNNING"
        self._emit("TASK", "STARTED", {"objective": self.packet["objective"]})
        return self.snapshot()

    def _require_running(self) -> None:
        if self._status == "READY":
            raise RuntimeControlError("runtime control plane has not started")
        if self._status in TERMINAL_STATUSES:
            raise RuntimeControlError(f"runtime is already terminal: {self._status}")

    def _elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def _terminate(self, status: str, reason: str, *, event_type: str = "STATUS") -> None:
        if self._status in TERMINAL_STATUSES:
            return
        self._status = status
        self._termination_reason = reason
        event_status = "TIMED_OUT" if status == "TIMED_OUT" else "BLOCKED" if status == "BLOCKED" else status
        self._emit(event_type, event_status, {"reason": reason})

    def _check_budget(
        self,
        *,
        steps: int = 0,
        tool_calls: int = 0,
        worker_calls: int = 0,
        estimated_tokens: int = 0,
        cost_micros: int = 0,
    ) -> None:
        self._require_running()
        if self._elapsed() > self.limits.max_wall_seconds:
            self._terminate("TIMED_OUT", "maximum wall-clock budget exceeded", event_type="TIMEOUT")
            raise BudgetExceeded("maximum wall-clock budget exceeded")
        projected = {
            "steps": self._usage.steps + steps,
            "toolCalls": self._usage.tool_calls + tool_calls,
            "workerCalls": self._usage.worker_calls + worker_calls,
            "estimatedTokens": self._usage.estimated_tokens + estimated_tokens,
            "costMicros": self._usage.cost_micros + cost_micros,
        }
        limits = {
            "steps": self.limits.max_steps,
            "toolCalls": self.limits.max_tool_calls,
            "workerCalls": self.limits.max_worker_calls,
            "estimatedTokens": self.limits.max_estimated_tokens,
            "costMicros": self.limits.max_cost_micros,
        }
        exceeded = [name for name, value in projected.items() if value > limits[name]]
        if exceeded:
            reason = "runtime budget exceeded: " + ", ".join(exceeded)
            self._terminate("TIMED_OUT", reason, event_type="TIMEOUT")
            raise BudgetExceeded(reason)

    def begin_step(self, label: str) -> dict[str, Any]:
        self._check_budget(steps=1)
        self._usage.steps += 1
        self._emit("CHECKPOINT", "IN_PROGRESS", {"phase": str(label)[:200], "usage": self._usage_dict()})
        return self.snapshot()

    def authorize_worker(self, role: str) -> dict[str, Any]:
        self._check_budget(worker_calls=1)
        self._usage.worker_calls += 1
        result = {"status": "ALLOWED", "role": _safe_id(role, "role"), "authoritySource": "runtime-budget"}
        self._emit("AGENT", "IN_PROGRESS", result)
        return result

    def execute_tool(
        self,
        tool_name: str,
        action: str,
        executor: Callable[[Mapping[str, Any]], Any] | None,
        *,
        arguments: Mapping[str, Any] | None = None,
        target: str | None = None,
        untrusted_context: Any | None = None,
        estimated_tokens: int = 0,
        cost_micros: int = 0,
    ) -> Any:
        if isinstance(estimated_tokens, bool) or not isinstance(estimated_tokens, int) or estimated_tokens < 0:
            raise RuntimeControlError("estimated_tokens must be a non-negative integer")
        if isinstance(cost_micros, bool) or not isinstance(cost_micros, int) or cost_micros < 0:
            raise RuntimeControlError("cost_micros must be a non-negative integer")
        self._check_budget(tool_calls=1, estimated_tokens=estimated_tokens, cost_micros=cost_micros)
        self._usage.tool_calls += 1
        self._usage.estimated_tokens += estimated_tokens
        self._usage.cost_micros += cost_micros
        try:
            decision = self._broker.authorize(
                tool_name,
                action,
                target=target,
                arguments=arguments,
                untrusted_context=untrusted_context,
            )
        except ToolDenied as exc:
            self._emit("TOOL", "BLOCKED", {"tool": str(tool_name), "action": str(action), "reason": str(exc)})
            raise

        loop = self._loop_detector.observe(
            {"tool": decision["tool"], "action": decision["action"], "target": decision["target"], "argumentsFingerprint": decision["argumentsFingerprint"]}
        )
        if loop["blocked"]:
            reason = f"repeated tool action exceeded threshold ({loop['threshold']})"
            self._terminate("BLOCKED", reason)
            raise LoopDetected(reason)
        if executor is None:
            reason = "host tool executor is unavailable"
            self._emit("TOOL", "BLOCKED", {"tool": decision["tool"], "action": decision["action"], "reason": reason})
            raise RuntimeControlError(reason)

        self._emit("TOOL", "IN_PROGRESS", {"tool": decision["tool"], "action": decision["action"], "loop": loop})
        try:
            result = executor(dict(arguments or {}))
        except Exception as exc:  # host errors must become explicit evidence
            self._emit("TOOL", "FAILED", {"tool": decision["tool"], "action": decision["action"], "errorType": type(exc).__name__})
            self._status = "FAILED"
            self._termination_reason = f"tool executor failed: {type(exc).__name__}"
            raise
        self._emit("TOOL", "PASS", {"tool": decision["tool"], "action": decision["action"], "loop": loop, "resultType": type(result).__name__})
        return result

    def checkpoint(self, phase: str, *, next_action: str = "") -> dict[str, Any]:
        self._check_budget()
        self._emit("CHECKPOINT", "IN_PROGRESS", {"phase": str(phase)[:200], "nextAction": str(next_action)[:500], "usage": self._usage_dict()})
        return self.snapshot()

    def cancel(self, reason: str = "cancellation requested") -> dict[str, Any]:
        self._require_running()
        self._terminate("CANCELLED", str(reason)[:500], event_type="CANCELLATION")
        return self.snapshot()

    def complete(self, summary: str = "") -> dict[str, Any]:
        self._check_budget()
        self._status = "COMPLETED"
        self._emit("TASK", "CLOSED", {"summary": str(summary)[:500], "usage": self._usage_dict()})
        return self.snapshot()

    def fail(self, reason: str) -> dict[str, Any]:
        self._require_running()
        self._terminate("FAILED", str(reason)[:500])
        return self.snapshot()

    def _usage_dict(self) -> dict[str, int]:
        return {
            "steps": self._usage.steps,
            "toolCalls": self._usage.tool_calls,
            "workerCalls": self._usage.worker_calls,
            "estimatedTokens": self._usage.estimated_tokens,
            "costMicros": self._usage.cost_micros,
        }

    def snapshot(self) -> dict[str, Any]:
        snapshot = {
            "schemaVersion": RUNTIME_CONTROL_SCHEMA_VERSION,
            "runId": self.run_id,
            "taskId": self.task_id,
            "status": self._status,
            "sourceFingerprint": self.source_fingerprint,
            "limits": self.limits.as_dict(),
            "usage": self._usage_dict(),
            "elapsedSeconds": round(self._elapsed(), 3) if self._started_at is not None else 0.0,
            "terminationReason": self._termination_reason,
            "loop": self._loop_detector.snapshot(),
            "eventCount": len(self._events),
            "observabilityReceipts": list(self._observability_receipts),
            "sourceOnly": True,
            "liveExecution": False,
            "sideEffects": [],
        }
        validation = validate_runtime_snapshot(snapshot)
        if not validation["valid"]:
            raise RuntimeControlError("runtime snapshot failed validation: " + "; ".join(validation["errors"]))
        return snapshot


__all__ = [
    "BudgetExceeded",
    "ContextBudgetExceeded",
    "LoopDetected",
    "LoopDetector",
    "RuntimeControlError",
    "RuntimeControlPlane",
    "RuntimeLimits",
    "ToolDenied",
    "ToolPermissionBroker",
    "compact_context",
    "validate_runtime_snapshot",
]
