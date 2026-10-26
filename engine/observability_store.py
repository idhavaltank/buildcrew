"""Redacted, idempotent evidence persistence boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence

from .evidence_observability import redact, validate_evidence_event, validate_event_sequence


RECEIPT_SCHEMA_VERSION = "observability-persistence-receipt-v1"


class ObservabilityError(ValueError):
    """Raised when evidence cannot safely cross the persistence boundary."""


class EvidenceSink(Protocol):
    def append(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        """Persist one sanitized event or return an explicit status."""

    def read(self) -> Sequence[Mapping[str, Any]]:
        """Read persisted sanitized events for recovery verification."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_root(root: str | Path, protected_roots: Sequence[str | Path]) -> Path:
    try:
        resolved = Path(root).expanduser().resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        raise ObservabilityError(f"state root is not resolvable: {type(exc).__name__}") from exc
    if resolved == Path("/"):
        raise ObservabilityError("state root cannot be filesystem root")
    for raw in protected_roots:
        try:
            protected = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, TypeError):
            continue
        if resolved == protected or protected in resolved.parents or resolved in protected.parents:
            raise ObservabilityError("observability state root overlaps a protected source root")
    return resolved


def validate_persistence_receipt(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["persistenceReceipt: expected object"]}
    required = {"schemaVersion", "receiptId", "runId", "status", "eventId", "persisted", "deduplicated", "redacted", "durable", "blockers", "sideEffects", "generatedAt"}
    errors.extend(f"persistenceReceipt: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"persistenceReceipt: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"persistenceReceipt.schemaVersion: expected {RECEIPT_SCHEMA_VERSION}")
    for key in ("receiptId", "runId", "eventId", "generatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"persistenceReceipt.{key}: expected non-empty string")
    if value.get("status") not in {"PERSISTED", "DEDUPLICATED", "CAPABILITY_UNAVAILABLE", "REJECTED", "FAILED"}:
        errors.append("persistenceReceipt.status: unsupported value")
    for key in ("persisted", "deduplicated", "redacted", "durable"):
        if not isinstance(value.get(key), bool):
            errors.append(f"persistenceReceipt.{key}: expected boolean")
    if not isinstance(value.get("blockers"), list) or not all(isinstance(item, str) and item.strip() for item in value["blockers"]):
        errors.append("persistenceReceipt.blockers: expected string array")
    if value.get("sideEffects") != []:
        errors.append("persistenceReceipt.sideEffects: must be empty")
    return {"valid": not errors, "errors": errors}


class JsonlEvidenceSink:
    """Small host-controlled durable sink for sanitized JSONL events."""

    def __init__(self, state_root: str | Path, *, protected_roots: Sequence[str | Path] = ()):
        self.root = _safe_root(state_root, protected_roots)
        self.path = self.root / "events.jsonl"
        self._lock = RLock()

    def _existing(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ObservabilityError("event store contains a non-object record")
                    events.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ObservabilityError(f"event store is unreadable: {type(exc).__name__}") from exc
        return events

    def append(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        validation = validate_evidence_event(event)
        if not validation["valid"]:
            return {"status": "REJECTED", "eventId": event.get("eventId"), "blockers": validation["errors"], "persisted": False, "deduplicated": False, "redacted": False, "durable": False}
        sanitized = redact(dict(event))
        event_id = str(sanitized.get("eventId"))
        with self._lock:
            existing = self._existing()
            if any(item.get("eventId") == event_id for item in existing):
                return {"status": "DEDUPLICATED", "eventId": event_id, "persisted": True, "deduplicated": True, "redacted": sanitized != event, "durable": True, "blockers": []}
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sanitized, sort_keys=True, separators=(",", ":")) + "\n")
        return {"status": "PERSISTED", "eventId": event_id, "persisted": True, "deduplicated": False, "redacted": sanitized != event, "durable": True, "blockers": []}

    def read(self) -> Sequence[Mapping[str, Any]]:
        with self._lock:
            return self._existing()


class ObservabilityController:
    """Publish events through one explicit sink and expose recovery state."""

    def __init__(self, sink: EvidenceSink | None = None):
        self.sink = sink

    def publish(self, event: Mapping[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
        event_id = str(event.get("eventId", "unknown-event")) if isinstance(event, Mapping) else "unknown-event"
        selected_run = str(run_id or (event.get("runId", "unknown-run") if isinstance(event, Mapping) else "unknown-run"))
        if self.sink is None:
            receipt = {"schemaVersion": RECEIPT_SCHEMA_VERSION, "receiptId": f"{selected_run}:{event_id}:unavailable", "runId": selected_run, "status": "CAPABILITY_UNAVAILABLE", "eventId": event_id, "persisted": False, "deduplicated": False, "redacted": False, "durable": False, "blockers": ["approved durable evidence sink is not configured"], "sideEffects": [], "generatedAt": _now()}
            return {"status": receipt["status"], "receipt": receipt}
        result = self.sink.append(event)
        receipt = {"schemaVersion": RECEIPT_SCHEMA_VERSION, "receiptId": f"{selected_run}:{event_id}:persistence", "runId": selected_run, "status": result.get("status", "FAILED"), "eventId": event_id, "persisted": bool(result.get("persisted")), "deduplicated": bool(result.get("deduplicated")), "redacted": bool(result.get("redacted")), "durable": bool(result.get("durable")), "blockers": [str(item) for item in result.get("blockers", []) if isinstance(item, str)], "sideEffects": [], "generatedAt": _now()}
        validation = validate_persistence_receipt(receipt)
        if not validation["valid"]:
            raise ObservabilityError("persistence receipt failed validation: " + "; ".join(validation["errors"]))
        return {"status": receipt["status"], "receipt": receipt}

    def recovery(self) -> dict[str, Any]:
        if self.sink is None:
            return {"status": "CAPABILITY_UNAVAILABLE", "events": [], "sequence": {"valid": False, "errors": ["approved durable evidence sink is not configured"]}, "sideEffects": []}
        try:
            events = list(self.sink.read())
        except ObservabilityError as exc:
            return {"status": "FAILED", "events": [], "sequence": {"valid": False, "errors": [str(exc)]}, "sideEffects": []}
        sequence = validate_event_sequence(events)
        return {"status": "PASS" if sequence["valid"] else "FAILED", "events": events, "sequence": sequence, "sideEffects": []}
