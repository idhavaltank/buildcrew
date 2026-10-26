#!/usr/bin/env python3
"""Validate a model-selection policy, assignment, or task fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.model_selection import (  # noqa: E402
    ASSIGNMENT_SCHEMA_VERSION,
    DECISION_SCHEMA_VERSION,
    MODEL_SELECTION_SCHEMA_VERSION,
    ModelSelectionError,
    validate_assignment,
    validate_host_catalog,
    validate_policy,
    validate_task_facts,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(value: Any, expected_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, dict) and "taskFacts" in value:
        task_result = validate_task_facts(value.get("taskFacts"))
        host_result = validate_host_catalog(value.get("hostCatalog"))
        errors = list(task_result["errors"]) + list(host_result["errors"])
        return {"valid": not errors, "kind": "fixture", "errors": errors}
    if isinstance(value, dict) and ("policyId" in value or "workerDefault" in value):
        result = validate_policy(value)
        return {"valid": result["valid"], "kind": "policy", **result}
    if isinstance(value, dict) and value.get("schemaVersion") == DECISION_SCHEMA_VERSION:
        return {"valid": False, "kind": "selection", "errors": ["selection decisions are explained, not standalone input"]}
    if isinstance(value, dict) and value.get("schemaVersion") == ASSIGNMENT_SCHEMA_VERSION:
        result = validate_assignment(value, expected_policy=expected_policy)
        return {"valid": result["valid"], "kind": "assignment", "errors": result["errors"]}
    result = validate_policy(value)
    return {"valid": result["valid"], "kind": "policy", **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an AE model-selection contract")
    parser.add_argument("path", type=Path, help="policy, assignment, or fixture JSON path")
    parser.add_argument("--policy", type=Path, help="policy JSON used to validate an assignment fingerprint")
    args = parser.parse_args(argv)
    try:
        value = _load(args.path)
        expected_policy = _load(args.policy) if args.policy else None
        result = _validate(value, expected_policy)
    except (OSError, json.JSONDecodeError, ModelSelectionError) as exc:
        result = {"valid": False, "kind": "unknown", "errors": [type(exc).__name__ + ": invalid input"]}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
