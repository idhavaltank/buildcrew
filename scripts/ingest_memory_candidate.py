#!/usr/bin/env python3
"""Promote one reviewed BuildCrew candidate into scoped canonical project memory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_memory import ingest_reviewed_candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--store", type=Path)
    args = parser.parse_args(argv)
    try:
        review = json.loads(args.review_json.read_text(encoding="utf-8"))
        result = ingest_reviewed_candidate(
            review,
            memory_root=args.memory_root,
            source_root=args.source_root,
            store_path=args.store,
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        result = {"outcome": "BLOCKED", "canonicalFilesChanged": False, "errors": [f"{type(error).__name__}: {error}"]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["outcome"] in {"INGESTED", "ALREADY_INGESTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
