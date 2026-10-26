# Safe cross-session handoff

A session handoff is a small, redacted evidence packet for continuing paused or
blocked work. It is not a transcript and transfers no authority.

## What it may preserve

- task identity, objective, route, risk, exact acceptance criteria, and their
  canonical fingerprint;
- project, repository, worktree, branch, HEAD, and source reference;
- source, overall-diff, dirty-state, and changed-path fingerprints;
- current/next phase, completed work, durable decisions, findings, blockers,
  residual risk, verification references, and a bounded next action;
- source session, creation/expiry timestamps, and
  `requiresRevalidation=true`.

## What it must never preserve or transfer

- raw prompts, transcripts, commands, tokens, cookies, credentials, or secrets;
- writer leases, model assignments, lifecycle ownership, reviewer authority,
  activation approval, host authority, or a previous verification result as
  current truth;
- ambient access to a handoff directory. Discovery always requires an explicit
  caller-supplied external root.

## Storage boundary

The state root must be outside the repository, active skill, worktree, hooks,
receipt, and other protected roots. `engine/session_handoff.py` creates private
`0700` directories and atomically replaces a `0600` JSON file. The default TTL
is 72 hours and the hard maximum is 168 hours. A source validator can exercise
these rules locally; it cannot certify a production host filesystem against all
race conditions.

## Attachment decision

`AUTO_ATTACH_EVIDENCE` requires exact equality for every binding:

- task ID, exact ordered acceptance criteria, and their canonical fingerprint;
- project/repository and worktree identity;
- branch and HEAD;
- source and overall-diff fingerprints;
- dirty boolean, dirty fingerprint, and sorted changed paths;
- plan version and `requiresRevalidation=true`.

A repository mismatch or expired packet is `REJECTED`. A related packet with
any other mismatch is only a `CANDIDATE`. Similarity scoring ranks candidates;
it never overrides an exact mismatch. Even an auto-attached packet remains
evidence only and must be revalidated against current source, tests, current
instructions, and current user direction before mutation.

Packet construction itself validates these rules. It rejects an acceptance
fingerprint that does not match the exact criteria. Construction requires
explicit caller redaction provenance and embeds a separate privacy receipt bound
to the exact packet content. That receipt records the redaction policy and
scanner version plus `PASS` scan results; it deliberately does not claim that a
finite scanner can prove every possible secret format is absent. Recognized
credential formats, high-entropy credential-like tokens, and affirmative
authority-transfer instructions are rejected before any state directory or file
can be created.

## Commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/session_handoff.py validate packet.json --now 2026-09-16T09:00:00Z
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/session_handoff.py write packet.json \
  --state-root /approved/external/state --repository-root /path/to/repository
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/session_handoff.py discover context.json \
  --state-root /approved/external/state --repository-root /path/to/repository
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/session_handoff.py render packet.json
```

The CLI never chooses a state root, installs a hook, resumes a session, grants
write access, or changes controller state.
