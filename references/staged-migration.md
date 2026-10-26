# Staged migration boundary

This reference defines how to evolve the unified skill safely.

## Current draft boundary

The draft combines workflow instructions and validated source-model concepts. It
does not install itself, alter active skills, invoke the external candidate's
controller, or change legacy state. The external project remains an independent
source-only candidate and archive.

## Before installation

Validate:

1. Frontmatter name and description follow skill conventions.
2. No forbidden frontmatter fields are present.
3. Trigger phrases are concrete and description length is within limits.
4. The main skill is under the body-size budget and references are linked.
5. The two controllers have one clear authority relationship.
6. Agent naming, scope, receipt, safety, and learning rules are consistent.
7. Web, backend, mobile, Flutter, and cross-platform paths are represented.
8. No instructions activate legacy state, create a competing state root, or
   bypass user approval for materially expanded authority.
9. Protected roots are runtime-configured and symlink-aware rather than tied to
   one developer home directory.
10. Receipt validation binds packet, task, plan, source or diff, evidence, host,
    role, terminal result, timestamp, and expiry.

## Shadow phase

After installation, compare the unified skill's route, risk tier, topology,
scope, and evidence requirements against existing behavior on a versioned,
redacted, production-like corpus covering simple, normal, coupled, web,
backend, mobile, Flutter, and cross-platform tasks. The comparison must be
differential: run both decision paths or replay equivalent inputs and record
disagreements. Do not mutate production state or silently redirect the legacy
workflow.

Record false positives, missed risks, unnecessary agent use, missing adapters,
receipt failures, and user corrections. Fix the draft or references, then repeat
the checks. Also test timeout, partial launch, stale receipt, host mismatch,
duplicate-controller, and rollback behavior.

## Activation phase

Activation requires explicit approval after shadow evidence, installed-versus-
active parity, disposable-Git lane proof where lanes are enabled, host-agent
compatibility, rollback, recovery, and exactly-one-controller checks. Use an
atomic feature switch and keep rollback as one reversible switch. Until then,
keep the external candidate source-only and the legacy runtime untouched.

The bundled `scripts/promotion_rehearsal.py` implements the safe part of this gate:
it validates archive paths, extracts only into a disposable temporary directory,
checks exact file parity, runs the extracted package tests, and records separate
candidate-validation, installation, active-configuration, activation, and rollback
receipts. Its default mode never activates. Temporary activation requires the
explicit rehearsal token, a named reviewer, and a passing host check; the live
Live host selection is never modified.

## Portability checks

Do not assume a specific home directory, shell, Python path, Git behavior, path
separator, drive format, or filesystem case-sensitivity. Where cross-platform
activation is required, test Linux, macOS, and Windows path forms, symlink or
junction resolution, filesystem capability checks, and Git worktree behavior.
