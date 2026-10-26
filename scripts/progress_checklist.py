#!/usr/bin/env python3
"""Persist and render evidence-backed, task-derived progress for AE tasks."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Sequence, TextIO

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.safety_boundaries import redact_value  # noqa: E402

try:
    from storage_paths import resolve_storage_paths
except ImportError:  # pragma: no cover - package import fallback
    from .storage_paths import resolve_storage_paths


SCHEMA_VERSION = "progress-checklist-v2"
LEGACY_SCHEMA_VERSION = "progress-checklist-v1"
PHASES = (
    ("intake", "Intake"),
    ("knowledge", "Knowledge"),
    ("context", "Context"),
    ("plan", "Plan"),
    ("dispatch", "Dispatch"),
    ("implementation", "Implementation"),
    ("review", "Review"),
    ("verification", "Verification"),
    ("learning", "Learning"),
    ("closure", "Closure"),
)
PHASE_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "PASS", "BLOCKED", "NOT_REQUIRED"}
TERMINAL_SUCCESS = {"PASS", "NOT_REQUIRED"}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
MAX_DETAIL = 240
MAX_LABEL = 160
MAX_EVENTS = 100
MAX_DISPLAY_PHASES = 20
MAX_LANES = 4
DEFAULT_FULL_EVERY = 3
EVIDENCE_TIERS = {
    "source",
    "test",
    "review",
    "runtime",
    "browser",
    "device",
    "deployment",
    "user",
}
FINGERPRINT_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ANSI_CONTROL_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OTHER_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization\s*:\s*(?:bearer|basic)\s+)\S+"
)
URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
SENSITIVE_VALUE_PATTERN = re.compile(
    r"""(?ix)
    (
      [\"']?
      (?:[A-Z0-9]+_)*
      (?:API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|TOKEN|PASSWORD|SECRET|COOKIE|DSN)
      [\"']?
      \s*[:=]\s*
      [\"']?
    )
    [^\"'\s,;}]+
    ([\"']?)
    """
)
EMOJI_MARKERS = {
    "PASS": "🟩",
    "IN_PROGRESS": "🟨",
    "NOT_STARTED": "⬜",
    "BLOCKED": "🟥",
    "NOT_REQUIRED": "🟦",
}
ASCII_MARKERS = {
    "PASS": "[DONE]",
    "IN_PROGRESS": "[NOW]",
    "NOT_STARTED": "[ ]",
    "BLOCKED": "[BLOCKED]",
    "NOT_REQUIRED": "[INFO]",
}


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_detail(value: Any, *, limit: int = MAX_DETAIL) -> str:
    text = str(redact_value(str(value)))
    text = PRIVATE_KEY_PATTERN.sub("[REDACTED PRIVATE KEY]", text)
    text = AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)
    text = URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", text)
    text = SENSITIVE_VALUE_PATTERN.sub(r"\1[REDACTED]\2", text)
    text = ANSI_CONTROL_PATTERN.sub("", text)
    text = OTHER_CONTROL_PATTERN.sub("", text)
    text = " ".join(text.split())
    return text[:limit]


def _safe_optional(value: Any, *, limit: int = MAX_DETAIL) -> str | None:
    if value is None:
        return None
    safe = _safe_detail(value, limit=limit)
    return safe or None


def _safe_list(values: Sequence[Any] | None) -> list[str]:
    if values is None:
        return []
    return [safe for value in values if (safe := _safe_optional(value)) is not None]


def _validate_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("taskId must be 1-128 safe characters")
    return task_id


def _validate_item_id(item_id: Any, *, label: str) -> str:
    if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
        raise ValueError(f"{label} must be 1-64 safe characters")
    return item_id


def _normalise_status(status: Any) -> str:
    normalized = str(status).strip().upper()
    if normalized not in PHASE_STATUSES:
        raise ValueError(f"phase status must be one of {sorted(PHASE_STATUSES)}")
    return normalized


def _normalise_full_every(value: int | None) -> int:
    if value is None:
        return DEFAULT_FULL_EVERY
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise ValueError("fullEvery must be an integer from 1 to 10")
    return value


def _normalise_evidence_tier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in EVIDENCE_TIERS:
        raise ValueError(
            "evidence tier must be one of " + ", ".join(sorted(EVIDENCE_TIERS))
        )
    return normalized


def _plan_fingerprint(display_phases: Sequence[tuple[str, str]] | Sequence[dict[str, Any]]) -> str:
    projection = [
        {
            "id": item[0] if not isinstance(item, dict) else item["id"],
            "label": item[1] if not isinstance(item, dict) else item["label"],
        }
        for item in display_phases
    ]
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_display_phases(
    values: Sequence[str | Sequence[str]] | None,
) -> list[tuple[str, str]]:
    if not values:
        return []
    if len(values) > MAX_DISPLAY_PHASES:
        raise ValueError(f"display phases cannot exceed {MAX_DISPLAY_PHASES}")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if isinstance(value, str):
            if "=" not in value:
                raise ValueError("display phase must use ID=Label")
            raw_id, raw_label = value.split("=", 1)
        elif isinstance(value, Sequence) and len(value) == 2:
            raw_id, raw_label = value
        else:
            raise ValueError(f"display phase {index + 1} must be an ID=Label string or pair")
        phase_id = _validate_item_id(str(raw_id).strip(), label="display phase id")
        phase_label = _safe_detail(raw_label, limit=MAX_LABEL)
        if not phase_label:
            raise ValueError("display phase label cannot be empty")
        if phase_id in seen:
            raise ValueError(f"duplicate display phase: {phase_id}")
        seen.add(phase_id)
        result.append((phase_id, phase_label))
    return result


def progress_state_path(
    task_id: str,
    *,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
) -> Path:
    task_id = _validate_task_id(task_id)
    if state_file is not None:
        path = Path(state_file).expanduser()
        if not path.is_absolute():
            raise ValueError("stateFile must be absolute")
        return path
    state_root = resolve_storage_paths(progress_root=root).progress_root
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
    return state_root / f"{digest}.json"


def _progress_item(item_id: str, label: str, status: str = "NOT_STARTED") -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "detail": None,
        "evidence": None,
        "evidenceTier": None,
        "legacyEvidenceUnverified": False,
        "blocker": None,
        "checked": [],
        "nextAction": None,
        "lastCommand": None,
    }


def _new_state(
    task_id: str,
    now: datetime,
    *,
    display_phases: Sequence[tuple[str, str]] = (),
    full_every: int = DEFAULT_FULL_EVERY,
) -> dict[str, Any]:
    canonical = [_progress_item(phase_id, label) for phase_id, label in PHASES]
    display = [_progress_item(phase_id, label) for phase_id, label in display_phases]
    if display:
        display[0]["status"] = "IN_PROGRESS"
    else:
        canonical[0]["status"] = "IN_PROGRESS"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "taskId": task_id,
        "status": "IN_PROGRESS",
        "createdAt": _iso(now),
        "updatedAt": _iso(now),
        "revision": 0,
        "planFingerprint": _plan_fingerprint(display_phases),
        "phases": canonical,
        "displayPhases": display,
        "lanes": [],
        "settings": {
            "fullEvery": full_every,
            "transitionCount": 0,
            "lastFullTransition": 0,
        },
        "events": [],
    }


def _migrate_v1(state: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(state)
    migrated["schemaVersion"] = SCHEMA_VERSION
    migrated["migratedFrom"] = LEGACY_SCHEMA_VERSION
    for phase in migrated.get("phases", []):
        if isinstance(phase, dict):
            phase.setdefault("detail", None)
            phase.setdefault("evidence", None)
            phase.setdefault("evidenceTier", None)
            phase["legacyEvidenceUnverified"] = phase.get("status") == "PASS"
            phase.setdefault("blocker", phase.get("detail") if phase.get("status") == "BLOCKED" else None)
            phase.setdefault("checked", [])
            phase.setdefault("nextAction", None)
            phase.setdefault("lastCommand", None)
    migrated["displayPhases"] = []
    migrated["lanes"] = []
    migrated["settings"] = {
        "fullEvery": DEFAULT_FULL_EVERY,
        "transitionCount": 0,
        "lastFullTransition": 0,
    }
    migrated.setdefault("events", [])
    for index, event in enumerate(migrated["events"], start=1):
        if isinstance(event, dict):
            event.setdefault("sequence", index)
    migrated["revision"] = len(migrated["events"])
    migrated["planFingerprint"] = _plan_fingerprint([])
    if migrated.get("status") == "CLOSED":
        migrated["legacyClosure"] = True
    return migrated


def _validate_item(item: Any, label: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label}: expected object")
        return
    try:
        _validate_item_id(item.get("id"), label=f"{label}.id")
    except ValueError as error:
        errors.append(str(error))
    if not isinstance(item.get("label"), str) or not item["label"].strip():
        errors.append(f"{label}.label: expected non-empty string")
    if item.get("status") not in PHASE_STATUSES:
        errors.append(f"{label}.status: invalid status")
    if item.get("evidenceTier") not in {None, *EVIDENCE_TIERS}:
        errors.append(f"{label}.evidenceTier: invalid evidence tier")
    if not isinstance(item.get("legacyEvidenceUnverified", False), bool):
        errors.append(f"{label}.legacyEvidenceUnverified: expected boolean")
    if not isinstance(item.get("checked", []), list):
        errors.append(f"{label}.checked: expected array")


def _validate_state(state: Any) -> tuple[bool, list[str]]:
    if not isinstance(state, dict):
        return False, ["progress state: expected object"]
    errors: list[str] = []
    if state.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"progress state.schemaVersion: expected {SCHEMA_VERSION}")
    try:
        _validate_task_id(state.get("taskId"))
    except ValueError as error:
        errors.append(str(error))
    phases = state.get("phases")
    if not isinstance(phases, list) or len(phases) != len(PHASES):
        errors.append(f"progress state.phases: expected {len(PHASES)} phases")
    else:
        expected_ids = [phase_id for phase_id, _ in PHASES]
        actual_ids = [item.get("id") if isinstance(item, dict) else None for item in phases]
        if actual_ids != expected_ids:
            errors.append("progress state.phases: phase order or identifiers are invalid")
        for index, phase in enumerate(phases):
            _validate_item(phase, f"progress state.phases[{index}]", errors)
    display = state.get("displayPhases")
    if not isinstance(display, list) or len(display) > MAX_DISPLAY_PHASES:
        errors.append(f"progress state.displayPhases: expected at most {MAX_DISPLAY_PHASES} items")
    else:
        display_ids: list[Any] = []
        for index, phase in enumerate(display):
            _validate_item(phase, f"progress state.displayPhases[{index}]", errors)
            display_ids.append(phase.get("id") if isinstance(phase, dict) else None)
        if len(display_ids) != len(set(display_ids)):
            errors.append("progress state.displayPhases: duplicate identifiers")
    lanes = state.get("lanes")
    if not isinstance(lanes, list) or len(lanes) > MAX_LANES:
        errors.append(f"progress state.lanes: expected at most {MAX_LANES} items")
    else:
        lane_ids: list[Any] = []
        for index, lane in enumerate(lanes):
            _validate_item(lane, f"progress state.lanes[{index}]", errors)
            lane_ids.append(lane.get("id") if isinstance(lane, dict) else None)
            if isinstance(lane, dict):
                for key in ("purpose", "deliverable", "verification"):
                    if not isinstance(lane.get(key), str) or not lane[key].strip():
                        errors.append(f"progress state.lanes[{index}].{key}: expected non-empty string")
                if not isinstance(lane.get("boundaries"), list) or not lane["boundaries"]:
                    errors.append(f"progress state.lanes[{index}].boundaries: expected non-empty array")
                if lane.get("preserveUnrelatedChanges") is not True:
                    errors.append(
                        f"progress state.lanes[{index}].preserveUnrelatedChanges: expected true"
                    )
        if len(lane_ids) != len(set(lane_ids)):
            errors.append("progress state.lanes: duplicate identifiers")
    settings = state.get("settings")
    if not isinstance(settings, dict):
        errors.append("progress state.settings: expected object")
    else:
        try:
            _normalise_full_every(settings.get("fullEvery"))
        except ValueError as error:
            errors.append(f"progress state.settings: {error}")
        for key in ("transitionCount", "lastFullTransition"):
            if not isinstance(settings.get(key), int) or settings[key] < 0:
                errors.append(f"progress state.settings.{key}: expected non-negative integer")
    if state.get("status") not in {"IN_PROGRESS", "BLOCKED", "CLOSED"}:
        errors.append("progress state.status: invalid status")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        errors.append("progress state.revision: expected non-negative integer")
    if not isinstance(state.get("planFingerprint"), str) or not FINGERPRINT_PATTERN.fullmatch(
        state["planFingerprint"]
    ):
        errors.append("progress state.planFingerprint: expected lowercase sha256")
    if not isinstance(state.get("events"), list):
        errors.append("progress state.events: expected array")
    return not errors, errors


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"progress state does not exist: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"progress state cannot be read: {type(error).__name__}") from error
    migrated = isinstance(state, dict) and state.get("schemaVersion") == LEGACY_SCHEMA_VERSION
    if migrated:
        state = _migrate_v1(state)
    valid, errors = _validate_state(state)
    if not valid:
        raise ValueError("; ".join(errors))
    return state


def _write_legacy_backup(path: Path) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
        decoded = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"legacy progress state cannot be backed up: {type(error).__name__}") from error
    if not isinstance(decoded, dict) or decoded.get("schemaVersion") != LEGACY_SCHEMA_VERSION:
        return
    backup = path.with_suffix(path.suffix + ".v1-backup")
    if backup.exists():
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=backup.parent,
        prefix=f".{backup.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        if not raw.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        try:
            os.link(temporary, backup)
        except FileExistsError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _write(path: Path, state: dict[str, Any]) -> None:
    if state.get("migratedFrom") == LEGACY_SCHEMA_VERSION and path.exists():
        _write_legacy_backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _assert_task_identity(state: dict[str, Any], task_id: str) -> None:
    if state["taskId"] != task_id:
        raise ValueError(
            f"progress state belongs to task {state['taskId']}; requested task was {task_id}"
        )


def _assert_revision(state: dict[str, Any], expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected revision must be a non-negative integer")
    if expected_revision < 0 or state["revision"] != expected_revision:
        raise ValueError(
            f"progress revision mismatch: expected {expected_revision}, current {state['revision']}"
        )


def _append_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    state["revision"] += 1
    state["events"] = [
        *state.get("events", []),
        {**event, "sequence": state["revision"]},
    ][-MAX_EVENTS:]


def _visible_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    display = state.get("displayPhases", [])
    return display if display else state["phases"]


def progress_summary(state: dict[str, Any]) -> dict[str, Any]:
    visible = _visible_items(state)
    lanes = state.get("lanes", [])
    completed = sum(item["status"] in TERMINAL_SUCCESS for item in visible)
    remaining = len(visible) - completed
    current_item = next((item for item in visible if item["status"] == "IN_PROGRESS"), None)
    if current_item is None:
        current_item = next((item for item in lanes if item["status"] == "IN_PROGRESS"), None)
    blockers = [
        item["label"]
        for item in [*visible, *lanes]
        if item["status"] == "BLOCKED"
    ]
    return {
        "total": len(visible),
        "completed": completed,
        "remaining": remaining,
        "current": current_item["label"] if current_item else None,
        "blockers": blockers,
        "status": state["status"],
        "canonicalRemaining": sum(
            phase["status"] not in TERMINAL_SUCCESS for phase in state["phases"]
        ),
        "laneRemaining": sum(lane["status"] not in TERMINAL_SUCCESS for lane in lanes),
    }


def _color_enabled(output: TextIO, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    if os.environ.get("NO_COLOR") is not None:
        return False
    return bool(getattr(output, "isatty", lambda: False)())


def _item_note(item: dict[str, Any]) -> str | None:
    if item.get("legacyEvidenceUnverified") and item["status"] == "PASS":
        detail = _safe_optional(item.get("detail"))
        return "Legacy completion; evidence unverified" + (
            f": {detail}" if detail else ""
        )
    if item["status"] == "BLOCKED":
        checked = "; ".join(_safe_list(item.get("checked")))
        parts = [
            f"Blocker: {_safe_detail(item.get('blocker'))}",
            f"Checked: {checked}",
            f"Next: {_safe_detail(item.get('nextAction'))}",
        ]
        return " | ".join(parts)
    if item["status"] == "PASS":
        evidence = _safe_optional(item.get("evidence") or item.get("detail"))
        tier = _normalise_evidence_tier(item.get("evidenceTier"))
        return f"{evidence} ({tier})" if evidence and tier else evidence
    return _safe_optional(item.get("detail") or item.get("evidence"))


def _render_item(item: dict[str, Any], *, use_color: bool) -> str:
    marker_status = (
        "NOT_REQUIRED"
        if item.get("legacyEvidenceUnverified") and item["status"] == "PASS"
        else item["status"]
    )
    marker = (EMOJI_MARKERS if use_color else ASCII_MARKERS)[marker_status]
    note = _item_note(item)
    return f"{marker} {_safe_detail(item['label'], limit=MAX_LABEL)}" + (
        f" — {note}" if note else ""
    )


def render_progress(
    state: dict[str, Any],
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> dict[str, Any]:
    """Print a complete friendly projection of the durable progress state."""

    output = stream or __import__("sys").stdout
    use_color = _color_enabled(output, color)
    summary = progress_summary(state)
    header = f"AE Progress — {summary['completed']}/{summary['total']} complete"
    if summary["current"]:
        header += f" · Current: {summary['current']}"
    elif state["status"] == "CLOSED":
        header += " · Closed"
    print(header, file=output)
    print(f"Task: {state['taskId']}", file=output)
    for item in _visible_items(state):
        print(_render_item(item, use_color=use_color), file=output)
    lanes = state.get("lanes", [])
    if lanes:
        print("Parallel work", file=output)
        for lane in lanes:
            print(_render_item(lane, use_color=use_color), file=output)
    return {"status": state["status"], "progress": summary, "stateFile": None}


def _render_transition(
    before: dict[str, Any] | None,
    after: Sequence[dict[str, Any]],
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> None:
    output = stream or __import__("sys").stdout
    use_color = _color_enabled(output, color)
    if before is not None:
        print(f"- {_render_item(before, use_color=use_color)}", file=output)
    for item in after:
        print(f"+ {_render_item(item, use_color=use_color)}", file=output)


def _find_item(items: Sequence[dict[str, Any]], item_id: str, *, kind: str) -> dict[str, Any]:
    item = next((candidate for candidate in items if candidate["id"] == item_id), None)
    if item is None:
        raise ValueError(f"unknown {kind}: {item_id}")
    return item


def _validate_transition_context(
    status: str,
    *,
    detail: str | None,
    evidence: str | None,
    evidence_tier: str | None,
    blocker: str | None,
    checked: Sequence[str] | None,
    next_action: str | None,
) -> None:
    if status == "PASS" and not _safe_optional(evidence or detail):
        raise ValueError("PASS requires evidence")
    if status == "PASS" and _normalise_evidence_tier(evidence_tier) is None:
        raise ValueError("PASS requires an evidence tier")
    if status == "NOT_REQUIRED" and not _safe_optional(detail or evidence):
        raise ValueError("NOT_REQUIRED requires a reason")
    if status == "BLOCKED":
        if not _safe_optional(blocker):
            raise ValueError("BLOCKED requires the exact blocker")
        if not _safe_list(checked):
            raise ValueError("BLOCKED requires at least one checked item")
        if not _safe_optional(next_action):
            raise ValueError("BLOCKED requires the safest next action")


def _apply_status(
    item: dict[str, Any],
    status: str,
    *,
    detail: str | None = None,
    evidence: str | None = None,
    evidence_tier: str | None = None,
    blocker: str | None = None,
    checked: Sequence[str] | None = None,
    next_action: str | None = None,
    last_command: str | None = None,
) -> None:
    item["status"] = status
    item["detail"] = _safe_optional(detail)
    item["lastCommand"] = _safe_optional(last_command)
    if status == "PASS":
        item["evidence"] = _safe_optional(evidence or detail)
        item["evidenceTier"] = _normalise_evidence_tier(evidence_tier)
        item["legacyEvidenceUnverified"] = False
        item["blocker"] = None
        item["checked"] = []
        item["nextAction"] = None
    elif status == "BLOCKED":
        item["evidence"] = _safe_optional(evidence)
        item["evidenceTier"] = _normalise_evidence_tier(evidence_tier)
        item["legacyEvidenceUnverified"] = False
        item["blocker"] = _safe_optional(blocker)
        item["checked"] = _safe_list(checked)
        item["nextAction"] = _safe_optional(next_action)
    else:
        item["evidence"] = _safe_optional(evidence)
        item["evidenceTier"] = _normalise_evidence_tier(evidence_tier)
        item["legacyEvidenceUnverified"] = False
        item["blocker"] = None
        item["checked"] = []
        item["nextAction"] = None


def _refresh_status(state: dict[str, Any]) -> None:
    if state.get("status") == "CLOSED":
        return
    items = [*state["phases"], *state.get("displayPhases", []), *state.get("lanes", [])]
    state["status"] = "BLOCKED" if any(item["status"] == "BLOCKED" for item in items) else "IN_PROGRESS"


def _record_transition(
    state: dict[str, Any],
    *,
    emit: bool,
    force_full: bool,
    blocked: bool,
) -> bool:
    settings = state["settings"]
    settings["transitionCount"] += 1
    due = (
        settings["transitionCount"] - settings["lastFullTransition"]
        >= settings["fullEvery"]
    )
    show_full = force_full or blocked or due
    if show_full and emit:
        settings["lastFullTransition"] = settings["transitionCount"]
    return show_full


def _result(state: dict[str, Any], path: Path) -> dict[str, Any]:
    return {**state, "progress": progress_summary(state), "stateFile": str(path)}


def start_progress(
    task_id: str,
    *,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
    display_phases: Sequence[str | Sequence[str]] | None = None,
    full_every: int | None = None,
    now: str | datetime | None = None,
    emit: bool = True,
    color: bool | None = None,
) -> dict[str, Any]:
    path = progress_state_path(task_id, state_file=state_file, root=root)
    requested_display = _normalise_display_phases(display_phases)
    if path.exists():
        state = _load(path)
        _assert_task_identity(state, task_id)
        migration_pending = state.get("migratedFrom") == LEGACY_SCHEMA_VERSION
        changed = migration_pending
        configuration_changed = False
        if requested_display:
            existing = [(item["id"], item["label"]) for item in state["displayPhases"]]
            if existing and existing != requested_display:
                raise ValueError("existing display phases do not match the requested plan")
            if not existing:
                state["displayPhases"] = [
                    _progress_item(phase_id, label, "IN_PROGRESS" if index == 0 else "NOT_STARTED")
                    for index, (phase_id, label) in enumerate(requested_display)
                ]
                state["planFingerprint"] = _plan_fingerprint(requested_display)
                changed = True
                configuration_changed = True
        if full_every is not None:
            normalized = _normalise_full_every(full_every)
            if state["settings"]["fullEvery"] != normalized:
                state["settings"]["fullEvery"] = normalized
                changed = True
                configuration_changed = True
        if changed:
            state["updatedAt"] = _iso(_now(now))
            if configuration_changed:
                _append_event(
                    state,
                    {
                    "kind": "display-plan",
                    "status": "CONFIGURED",
                    "at": state["updatedAt"],
                    },
                )
            _write(path, state)
    else:
        state = _new_state(
            task_id,
            _now(now),
            display_phases=requested_display,
            full_every=_normalise_full_every(full_every),
        )
        _write(path, state)
    result = _result(state, path)
    if emit:
        render_progress(result, color=color)
    return result


def update_progress(
    task_id: str,
    phase_id: str,
    status: str,
    *,
    detail: str | None = None,
    last_command: str | None = None,
    display_phase_id: str | None = None,
    next_display_phase_id: str | None = None,
    evidence: str | None = None,
    evidence_tier: str | None = None,
    blocker: str | None = None,
    checked: Sequence[str] | None = None,
    next_action: str | None = None,
    expected_revision: int | None = None,
    full: bool = False,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
    now: str | datetime | None = None,
    emit: bool = True,
    color: bool | None = None,
) -> dict[str, Any]:
    path = progress_state_path(task_id, state_file=state_file, root=root)
    state = _load(path)
    _assert_task_identity(state, task_id)
    _assert_revision(state, expected_revision)
    if state["status"] == "CLOSED":
        raise ValueError("closed progress cannot be updated")
    normalized_status = _normalise_status(status)
    _validate_transition_context(
        normalized_status,
        detail=detail,
        evidence=evidence,
        evidence_tier=evidence_tier,
        blocker=blocker,
        checked=checked,
        next_action=next_action,
    )
    phase = _find_item(state["phases"], phase_id, kind="phase")
    display = state.get("displayPhases", [])
    visible_target: dict[str, Any] | None = None
    next_visible: dict[str, Any] | None = None
    if display_phase_id is not None:
        display_phase_id = _validate_item_id(display_phase_id, label="display phase id")
        visible_target = _find_item(display, display_phase_id, kind="display phase")
    elif not display:
        visible_target = phase
    if next_display_phase_id is not None:
        if visible_target is None:
            raise ValueError("next display phase requires a current display phase")
        next_display_phase_id = _validate_item_id(
            next_display_phase_id, label="next display phase id"
        )
        if display_phase_id == next_display_phase_id:
            raise ValueError("next display phase must differ from the current display phase")
        if normalized_status == "BLOCKED":
            raise ValueError("a blocked transition cannot activate the next display phase")
        next_visible = _find_item(display, next_display_phase_id, kind="display phase")
        if next_visible["status"] in TERMINAL_SUCCESS:
            raise ValueError("a completed display phase cannot be reactivated")

    phase_before = deepcopy(phase)
    before = deepcopy(visible_target) if visible_target is not None else None
    next_before = deepcopy(next_visible) if next_visible is not None else None
    _apply_status(
        phase,
        normalized_status,
        detail=detail,
        evidence=evidence,
        evidence_tier=evidence_tier,
        blocker=blocker,
        checked=checked,
        next_action=next_action,
        last_command=last_command,
    )
    if visible_target is not None and visible_target is not phase:
        _apply_status(
            visible_target,
            normalized_status,
            detail=detail,
            evidence=evidence or detail,
            evidence_tier=evidence_tier,
            blocker=blocker,
            checked=checked,
            next_action=next_action,
            last_command=last_command,
        )
    if next_visible is not None:
        _apply_status(next_visible, "IN_PROGRESS")

    changed = phase != phase_before
    if visible_target is not None and visible_target is not phase:
        changed = changed or visible_target != before
    if next_visible is not None:
        changed = changed or next_visible != next_before
    if not changed:
        result = _result(state, path)
        if emit and full:
            render_progress(result, color=color)
        return result

    current = _now(now)
    state["updatedAt"] = _iso(current)
    state.pop("completionBlockers", None)
    _refresh_status(state)
    _append_event(
        state,
        {
            "kind": "phase",
            "phase": phase_id,
            "displayPhase": display_phase_id,
            "nextDisplayPhase": next_display_phase_id,
            "status": normalized_status,
            "detail": _safe_optional(detail),
            "evidence": _safe_optional(evidence),
            "evidenceTier": _normalise_evidence_tier(evidence_tier),
            "blocker": _safe_optional(blocker),
            "checked": _safe_list(checked),
            "nextAction": _safe_optional(next_action),
            "at": _iso(current),
        },
    )
    transition_items = (
        [deepcopy(visible_target), *([deepcopy(next_visible)] if next_visible else [])]
        if visible_target is not None
        else []
    )
    show_full = (
        _record_transition(
            state,
            emit=emit,
            force_full=full,
            blocked=normalized_status == "BLOCKED",
        )
        if visible_target is not None
        else True
    )
    _write(path, state)
    result = _result(state, path)
    if emit:
        if visible_target is not None:
            _render_transition(before, transition_items, color=color)
            if show_full:
                print()
                render_progress(result, color=color)
        else:
            render_progress(result, color=color)
    return result


def update_lane(
    task_id: str,
    lane_id: str,
    status: str,
    *,
    label: str | None = None,
    purpose: str | None = None,
    boundaries: Sequence[str] | None = None,
    deliverable: str | None = None,
    verification: str | None = None,
    detail: str | None = None,
    evidence: str | None = None,
    evidence_tier: str | None = None,
    blocker: str | None = None,
    checked: Sequence[str] | None = None,
    next_action: str | None = None,
    last_command: str | None = None,
    expected_revision: int | None = None,
    full: bool = False,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
    now: str | datetime | None = None,
    emit: bool = True,
    color: bool | None = None,
) -> dict[str, Any]:
    path = progress_state_path(task_id, state_file=state_file, root=root)
    state = _load(path)
    _assert_task_identity(state, task_id)
    _assert_revision(state, expected_revision)
    if state["status"] == "CLOSED":
        raise ValueError("closed progress cannot be updated")
    lane_id = _validate_item_id(lane_id, label="lane id")
    normalized_status = _normalise_status(status)
    _validate_transition_context(
        normalized_status,
        detail=detail,
        evidence=evidence,
        evidence_tier=evidence_tier,
        blocker=blocker,
        checked=checked,
        next_action=next_action,
    )
    lane = next((item for item in state["lanes"] if item["id"] == lane_id), None)
    before = deepcopy(lane) if lane is not None else None
    if lane is None:
        if len(state["lanes"]) >= MAX_LANES:
            raise ValueError(f"parallel lanes cannot exceed {MAX_LANES}")
        required = {
            "label": label,
            "purpose": purpose,
            "boundaries": boundaries,
            "deliverable": deliverable,
            "verification": verification,
        }
        for key, value in required.items():
            if key == "boundaries":
                if not _safe_list(value):
                    raise ValueError("new lane requires boundaries")
            elif not _safe_optional(value):
                raise ValueError(f"new lane requires {key}")
        lane = _progress_item(lane_id, _safe_detail(label, limit=MAX_LABEL))
        lane.update(
            {
                "purpose": _safe_detail(purpose),
                "boundaries": _safe_list(boundaries),
                "deliverable": _safe_detail(deliverable),
                "verification": _safe_detail(verification),
                "preserveUnrelatedChanges": True,
            }
        )
        state["lanes"].append(lane)
    else:
        if label is not None:
            safe_label = _safe_detail(label, limit=MAX_LABEL)
            if not safe_label:
                raise ValueError("lane label cannot be empty")
            lane["label"] = safe_label
        if purpose is not None:
            lane["purpose"] = _safe_detail(purpose)
        if boundaries is not None:
            safe_boundaries = _safe_list(boundaries)
            if not safe_boundaries:
                raise ValueError("lane boundaries cannot be empty")
            lane["boundaries"] = safe_boundaries
        if deliverable is not None:
            lane["deliverable"] = _safe_detail(deliverable)
        if verification is not None:
            lane["verification"] = _safe_detail(verification)

    _apply_status(
        lane,
        normalized_status,
        detail=detail,
        evidence=evidence,
        evidence_tier=evidence_tier,
        blocker=blocker,
        checked=checked,
        next_action=next_action,
        last_command=last_command,
    )
    if before is not None and lane == before:
        result = _result(state, path)
        if emit and full:
            render_progress(result, color=color)
        return result
    current = _now(now)
    state["updatedAt"] = _iso(current)
    state.pop("completionBlockers", None)
    _refresh_status(state)
    _append_event(
        state,
        {
            "kind": "lane",
            "lane": lane_id,
            "status": normalized_status,
            "evidence": _safe_optional(evidence),
            "evidenceTier": _normalise_evidence_tier(evidence_tier),
            "blocker": _safe_optional(blocker),
            "checked": _safe_list(checked),
            "nextAction": _safe_optional(next_action),
            "at": _iso(current),
        },
    )
    show_full = _record_transition(
        state,
        emit=emit,
        force_full=full,
        blocked=normalized_status == "BLOCKED",
    )
    _write(path, state)
    result = _result(state, path)
    if emit:
        _render_transition(before, [deepcopy(lane)], color=color)
        if show_full:
            print()
            render_progress(result, color=color)
    return result


def _completion_blockers(state: dict[str, Any]) -> list[str]:
    blockers = [
        f"Lifecycle phase: {phase['label']}"
        for phase in state["phases"]
        if phase["status"] not in TERMINAL_SUCCESS
    ]
    blockers.extend(
        f"Task phase: {phase['label']}"
        for phase in state.get("displayPhases", [])
        if phase["status"] not in TERMINAL_SUCCESS
    )
    blockers.extend(
        f"Parallel lane: {lane['label']}"
        for lane in state.get("lanes", [])
        if lane["status"] not in TERMINAL_SUCCESS
    )
    blockers.extend(
        f"Legacy evidence unverified: {item['label']}"
        for item in [
            *state["phases"],
            *state.get("displayPhases", []),
            *state.get("lanes", []),
        ]
        if item["status"] == "PASS" and item.get("legacyEvidenceUnverified")
    )
    return blockers


def complete_progress(
    task_id: str,
    *,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
    expected_revision: int | None = None,
    now: str | datetime | None = None,
    emit: bool = True,
    color: bool | None = None,
) -> dict[str, Any]:
    path = progress_state_path(task_id, state_file=state_file, root=root)
    state = _load(path)
    _assert_task_identity(state, task_id)
    _assert_revision(state, expected_revision)
    if state["status"] == "CLOSED" and state.get("legacyClosure") is True:
        result = _result(state, path)
        result["legacyClosure"] = True
        if emit:
            render_progress(result, color=color)
        return result
    blockers = _completion_blockers(state)
    if blockers:
        changed = state.get("completionBlockers") != blockers or state["status"] != "BLOCKED"
        state["status"] = "BLOCKED"
        state["completionBlockers"] = blockers
        state["updatedAt"] = _iso(_now(now))
        if changed:
            _append_event(
                state,
                {
                    "kind": "closure",
                    "status": "BLOCKED",
                    "blockers": blockers,
                    "at": state["updatedAt"],
                },
            )
            _write(path, state)
        summary = progress_summary(state)
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "taskId": task_id,
            "status": "BLOCKED",
            "blockers": blockers,
            "progress": summary,
            "stateFile": str(path),
        }
        if emit:
            render_progress(state, color=color)
        return result
    state["status"] = "CLOSED"
    state.pop("completionBlockers", None)
    state["updatedAt"] = _iso(_now(now))
    state["closedAt"] = state["updatedAt"]
    _append_event(
        state,
        {
            "kind": "closure",
            "phase": "closure",
            "status": "CLOSED",
            "at": state["closedAt"],
        },
    )
    _write(path, state)
    result = _result(state, path)
    if emit:
        render_progress(result, color=color)
    return result


def show_progress(
    task_id: str,
    *,
    state_file: str | Path | None = None,
    root: str | Path | None = None,
    color: bool | None = None,
) -> dict[str, Any]:
    path = progress_state_path(task_id, state_file=state_file, root=root)
    state = _load(path)
    _assert_task_identity(state, task_id)
    result = _result(state, path)
    render_progress(result, color=color)
    return result


def _add_storage_and_output_arguments(parser: argparse.ArgumentParser, *, quiet: bool) -> None:
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--root", type=Path)
    if quiet:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Persist state without rendering progress",
        )
    parser.add_argument(
        "--color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use friendly emoji status markers (default: auto-detect terminal)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--task-id", required=True)
    start.add_argument(
        "--display-phase",
        action="append",
        default=[],
        metavar="ID=LABEL",
        help="Task-derived user-facing phase (repeatable)",
    )
    start.add_argument(
        "--full-every",
        type=int,
        default=None,
        help="Show a full snapshot after this many visible transitions (1-10; default: 3)",
    )
    _add_storage_and_output_arguments(start, quiet=True)

    update = subparsers.add_parser("update")
    update.add_argument("--task-id", required=True)
    update.add_argument("--phase", choices=[phase_id for phase_id, _ in PHASES], required=True)
    update.add_argument("--status", choices=sorted(PHASE_STATUSES), required=True)
    update.add_argument("--detail")
    update.add_argument("--last-command")
    update.add_argument("--display-phase", dest="display_phase_id")
    update.add_argument("--next-display-phase", dest="next_display_phase_id")
    update.add_argument("--evidence")
    update.add_argument("--evidence-tier", choices=sorted(EVIDENCE_TIERS))
    update.add_argument("--blocker")
    update.add_argument("--checked", action="append", default=[])
    update.add_argument("--next-action")
    update.add_argument("--expected-revision", type=int)
    update.add_argument("--full", action="store_true")
    _add_storage_and_output_arguments(update, quiet=True)

    lane = subparsers.add_parser("lane")
    lane.add_argument("--task-id", required=True)
    lane.add_argument("--lane-id", required=True)
    lane.add_argument("--status", choices=sorted(PHASE_STATUSES), required=True)
    lane.add_argument("--label")
    lane.add_argument("--purpose")
    lane.add_argument("--boundary", action="append", default=None)
    lane.add_argument("--deliverable")
    lane.add_argument("--verification")
    lane.add_argument("--detail")
    lane.add_argument("--evidence")
    lane.add_argument("--evidence-tier", choices=sorted(EVIDENCE_TIERS))
    lane.add_argument("--blocker")
    lane.add_argument("--checked", action="append", default=[])
    lane.add_argument("--next-action")
    lane.add_argument("--last-command")
    lane.add_argument("--expected-revision", type=int)
    lane.add_argument("--full", action="store_true")
    _add_storage_and_output_arguments(lane, quiet=True)

    complete = subparsers.add_parser("complete")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--expected-revision", type=int)
    _add_storage_and_output_arguments(complete, quiet=True)

    show = subparsers.add_parser("show")
    show.add_argument("--task-id", required=True)
    _add_storage_and_output_arguments(show, quiet=False)

    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            result = start_progress(
                args.task_id,
                state_file=args.state_file,
                root=args.root,
                display_phases=args.display_phase,
                full_every=args.full_every,
                emit=not args.quiet,
                color=args.color,
            )
        elif args.command == "update":
            result = update_progress(
                args.task_id,
                args.phase,
                args.status,
                detail=args.detail,
                last_command=args.last_command,
                display_phase_id=args.display_phase_id,
                next_display_phase_id=args.next_display_phase_id,
                evidence=args.evidence,
                evidence_tier=args.evidence_tier,
                blocker=args.blocker,
                checked=args.checked,
                next_action=args.next_action,
                expected_revision=args.expected_revision,
                full=args.full,
                state_file=args.state_file,
                root=args.root,
                emit=not args.quiet,
                color=args.color,
            )
        elif args.command == "lane":
            result = update_lane(
                args.task_id,
                args.lane_id,
                args.status,
                label=args.label,
                purpose=args.purpose,
                boundaries=args.boundary,
                deliverable=args.deliverable,
                verification=args.verification,
                detail=args.detail,
                evidence=args.evidence,
                evidence_tier=args.evidence_tier,
                blocker=args.blocker,
                checked=args.checked,
                next_action=args.next_action,
                last_command=args.last_command,
                expected_revision=args.expected_revision,
                full=args.full,
                state_file=args.state_file,
                root=args.root,
                emit=not args.quiet,
                color=args.color,
            )
        elif args.command == "complete":
            result = complete_progress(
                args.task_id,
                state_file=args.state_file,
                root=args.root,
                expected_revision=args.expected_revision,
                emit=not args.quiet,
                color=args.color,
            )
        else:
            result = show_progress(
                args.task_id,
                state_file=args.state_file,
                root=args.root,
                color=args.color,
            )
    except (OSError, ValueError) as error:
        marker = "🟥" if getattr(args, "color", None) is not False else "[BLOCKED]"
        print(f"{marker} AE Progress blocked — {type(error).__name__}: {error}")
        return 2
    return 0 if result.get("status") != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
