#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.git_source_context import GitSourceError, inspect_source_context


parser = argparse.ArgumentParser(description="Read-only Git source inspection")
parser.add_argument("repository")
parser.add_argument("--repository-id")
args = parser.parse_args()
try:
    result = inspect_source_context(args.repository, repository_id=args.repository_id)
except (GitSourceError, OSError, ValueError) as error:
    print(json.dumps({"status": "BLOCKED", "error": str(error)}, indent=2, sort_keys=True))
    raise SystemExit(2)
print(json.dumps(result, indent=2, sort_keys=True))
