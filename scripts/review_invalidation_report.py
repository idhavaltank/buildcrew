#!/usr/bin/env python3
"""Produce a deterministic review invalidation report without mutation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from engine.review_invalidation import ReviewInvalidationError, plan_review_invalidation  # noqa: E402


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--changes", type=Path, required=True)
    parser.add_argument("--finding-ledger", type=Path)
    parser.add_argument("--risk-authorizations", type=Path)
    parser.add_argument("--resolution-receipts", type=Path)
    parser.add_argument("--current-diff-fingerprint", required=True)
    parser.add_argument("--producer-identity", required=True)
    parser.add_argument(
        "--producer-authority",
        choices=("review-host", "controller"),
        required=True,
    )
    parser.add_argument("--issued-at", required=True)
    parser.add_argument("--expires-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = plan_review_invalidation(
            _read(args.manifest),
            _read(args.receipts),
            _read(args.changes),
            current_overall_diff_fingerprint=args.current_diff_fingerprint,
            producer_identity=args.producer_identity,
            producer_authority=args.producer_authority,
            issued_at=args.issued_at,
            expires_at=args.expires_at,
            finding_ledger=_read(args.finding_ledger) if args.finding_ledger else None,
            trusted_risk_authorizations=(
                _read(args.risk_authorizations) if args.risk_authorizations else None
            ),
            trusted_resolution_receipts=(
                _read(args.resolution_receipts) if args.resolution_receipts else None
            ),
        )
    except (OSError, json.JSONDecodeError, ReviewInvalidationError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
