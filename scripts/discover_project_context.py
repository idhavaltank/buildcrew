#!/usr/bin/env python3
"""Discover project context and resolve a policy bundle without mutation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.context_receipts import validate_context_map, validate_manifest, validate_policy_bundle, validate_receipt  # noqa: E402
from engine.context_state import persist_context  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402
from engine.project_context import MAX_CANDIDATE_LIMIT, ContextDiscoveryError, discover_project_context  # noqa: E402


def _max_files(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 50000") from exc
    if not 1 <= parsed <= MAX_CANDIDATE_LIMIT:
        raise argparse.ArgumentTypeError(f"must be an integer from 1 to {MAX_CANDIDATE_LIMIT}")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only general project-context discovery and policy resolution")
    parser.add_argument("root", help="repository root, target directory, or file")
    parser.add_argument("--target", action="append", default=[], help="repository-relative target path (repeatable)")
    parser.add_argument("--task-type", default="NEW_TASK", help="normalized task type")
    parser.add_argument("--objective", default="", help="normalized task objective")
    parser.add_argument("--acceptance-criterion", action="append", default=[], help="task acceptance criterion (repeatable)")
    parser.add_argument("--profile", action="append", default=[], help="explicit verified profile (repeatable)")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--repository-id", default=None)
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--protected-root", action="append", default=[], help="protected path that discovery must reject")
    parser.add_argument("--state-root", default=None, help="explicit host-controlled external state root")
    parser.add_argument("--persist", action="store_true", help="persist map and receipt below --state-root only")
    parser.add_argument("--fast-mobile", action="store_true", help="use target-scoped V1-compatible mobile discovery")
    parser.add_argument("--max-files", type=_max_files, default=None, help="full-repository candidate bound, 1–50000 (default: 5000; incompatible with --fast-mobile)")
    return parser


def build_output(args: argparse.Namespace) -> dict[str, Any]:
    if args.persist and not args.state_root:
        raise ContextDiscoveryError("--persist requires an explicit --state-root")
    result = discover_project_context(
        args.root,
        task_type=args.task_type,
        targets=args.target,
        acceptance_criteria=args.acceptance_criterion,
        project_id=args.project_id,
        repository_id=args.repository_id,
        source_ref=args.source_ref,
        explicit_profiles=args.profile,
        protected_roots=args.protected_root,
        fast_path=args.fast_mobile,
        max_candidates=args.max_files,
    )
    manifest = result.get("manifest")
    manifest_check = validate_manifest(manifest) if manifest is not None else {"valid": True, "errors": []}
    intake = {
        "requestType": args.task_type,
        "normalizedObjective": args.objective,
        "targetPaths": result["contextMap"]["fingerprints"].get("targetPaths", args.target or ["."]),
        "targets": args.target or ["."],
        "acceptanceCriteria": args.acceptance_criterion,
    }
    policy = resolve_policy(
        intake,
        result["contextMap"],
        manifest=manifest if manifest_check["valid"] else None,
        manifest_path=result.get("manifestPath"),
        repository_root=result["root"],
        explicit_profiles=args.profile,
    )

    if args.persist:
        result["contextMap"]["persistence"] = "external"
        result["receipt"]["persistence"] = "external"
        persistence = persist_context(
            result["contextMap"],
            result["receipt"],
            state_root=args.state_root,
            repository_root=result["root"],
            protected_roots=args.protected_root,
        )
        if persistence["persistence"] != "external":
            result["contextMap"]["persistence"] = "unavailable"
            result["receipt"]["persistence"] = "unavailable"

    checks = {
        "contextMap": validate_context_map(result["contextMap"]),
        "receipt": validate_receipt(result["receipt"]),
        "policyBundle": validate_policy_bundle(policy),
    }
    if manifest is not None:
        checks["manifest"] = manifest_check
    if not all(value["valid"] for value in checks.values()):
        errors = [error for value in checks.values() for error in value["errors"]]
        raise ContextDiscoveryError("generated contract failed validation: " + "; ".join(errors))
    return {
        "contextMap": result["contextMap"],
        "receipt": result["receipt"],
        "policyBundle": policy,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = build_output(args)
    except (ContextDiscoveryError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
