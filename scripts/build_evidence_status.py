#!/usr/bin/env python3
"""Build sanitized source-only evidence event and matrix status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.evidence_observability import (  # noqa: E402
    build_event,
    build_evidence_matrix,
    validate_evidence_event,
    validate_evidence_matrix,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("source_fingerprint")
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--static", action="append", default=[])
    parser.add_argument("--runtime", action="append", default=[])
    parser.add_argument("--device", action="append", default=[])
    parser.add_argument("--activation", action="append", default=[])
    parser.add_argument("--event-type", default="STATUS")
    parser.add_argument("--status", default="SOURCE_ONLY")
    parser.add_argument("--run-id")
    parser.add_argument("--sequence", type=int, default=0)
    parser.add_argument("--occurred-at")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        event = build_event(
            args.event_type,
            args.task_id,
            args.status,
            source_fingerprint=args.source_fingerprint,
            run_id=args.run_id,
            sequence=args.sequence,
            occurred_at=args.occurred_at,
        )
        matrix = build_evidence_matrix(
            args.requirement,
            static=args.static,
            runtime=args.runtime,
            device=args.device,
            activation=args.activation,
            task_id=args.task_id,
            source_fingerprint=args.source_fingerprint,
        )
        event_validation = validate_evidence_event(event)
        matrix_validation = validate_evidence_matrix(matrix)
        output = {
            "event": event,
            "matrix": matrix,
            "validation": {"event": event_validation, "matrix": matrix_validation},
        }
        rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    valid = event_validation["valid"] and matrix_validation["valid"]
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
