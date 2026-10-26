#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.worktree_host_adapter import provision_worktree


parser = argparse.ArgumentParser(description="Provision an approved Git worktree")
parser.add_argument("--request", required=True, type=Path)
args = parser.parse_args()
try:
    request = json.loads(args.request.read_text())
    result = provision_worktree(request)
except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
    print(json.dumps({"status": "BLOCKED", "error": str(error)}, indent=2, sort_keys=True))
    raise SystemExit(2)
print(json.dumps(result, indent=2, sort_keys=True))
