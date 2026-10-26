# Project Context Bootstrap

This reference describes the implemented first slice of
`general-V3-PROJECT-CONTEXT-POLICY-DESIGN.md`.

## Memory-first input to project context

Every input first runs `scripts/lookup_memory_context.py` against the approved
memory root resolved by [shared storage configuration](storage-configuration.md).
The resulting `memory-context-lookup-v1` receipt
selects, when available, the narrow repository/application/module/feature
boundary used as the initial project-context target. The memory receipt is
navigation evidence only:

- `matched` narrows discovery but still requires current-source revalidation;
- `no_relevant_memory`, stale/incomplete selection, `unavailable`, `failed`, or
  `invalid` sends discovery to the current repository and code path;
- optional provider cards may supplement the local result but cannot replace
  the current-source context map;
- the memory helper is read-only and never recursively scans the full memory
  tree.

Project context and memory have separate receipts. The context map proves what
source/instructions were discovered; the memory receipt proves only which
bounded prior notes were considered. Neither receipt proves runtime behavior.

## What is implemented

- `engine/project_context.py` resolves a repository boundary, enumerates a
  bounded candidate set, applies nested instruction scope, detects platform and
  product signals, records plan/command sources, computes source/context/task
  fingerprints, and emits a read-only context map plus receipt. Full-repository
  enumeration defaults to 5,000 files and accepts an explicit `max_candidates`
  bound from 1 through 50,000. The receipt reports the actual limit and number
  of files enumerated; reaching the limit records missing coverage.
- `engine/policy_catalog.py` contains explicit V3 common, shared-application,
  web, mobile, Flutter, backend, and optional media rules.
- `engine/policy_resolver.py` selects verified profiles, adds project-manifest
  and explicit normative rules from authoritative `AGENTS.md` files, adds task
  requirements, detects conflicts, unions capabilities/evidence, and raises
  risk monotonically.
- `engine/context_receipts.py` rejects missing and unknown fields without
  requiring a third-party JSON-schema package.
- `engine/context_state.py` permits atomic map/receipt persistence only below an
  explicit external host root and reports failures as unavailable state.

## Safe operating boundary

Discovery reads manifests and recognized documentation but never runs package
scripts, follows symlinks outside the repository, reads excluded runtime
directories, writes repository files, or promotes arbitrary Markdown prose to a
blocking rule. The active skill directory and host-configured protected roots
are rejected as discovery boundaries, and a bounded scan reports
`INCOMPLETE`/`MISSING_COVERAGE` when its file cap is reached. A discovered map is navigation metadata. A policy bundle states
what must be checked. Neither proves implementation correctness or runtime,
browser, device, deployment, or reviewer behavior.

Set `BUILDCREW_CONTEXT_EXCLUDED_DIRS` to an OS-path-separated list of
directory names when a host keeps private runtime files inside a repository.
The exclusion applies to full and target-scoped discovery, including explicit
targets. Keep project instruction directories visible; this setting is for
private runtime data. `BUILDCREW_PROTECTED_ROOTS` adds external
host-control boundaries; older `AUTONOMOUS_ENGINEERING_PROTECTED_ROOTS` values
remain enforced when configured.

The default CLI is session-only:

```bash
python3 -B scripts/discover_project_context.py /path/to/repository \
  --target apps/frontend \
  --task-type NEW_TASK \
  --objective "change the contact search flow"
```

For a large repository, `--max-files` raises or lowers the full-repository
enumeration bound within 1–50,000 files (default 5,000):

```bash
python3 -B scripts/discover_project_context.py /path/to/repository \
  --max-files 12000
```

The explicit full-repository bound cannot be combined with `--fast-mobile`,
whose target-scoped discovery keeps its own fixed limit.

For routine mobile or Flutter source work, pass `--fast-mobile` with an
explicit target. The fast form reads only that target and its nearest
instructions/manifests; it does not enumerate unrelated repository sources.
It is navigation evidence for the V1-compatible daily route, not a runtime or
completion claim. Use the full form for coupled, high-risk, shared-boundary,
or explicit runtime work.

The package protects skill and host-control roots from self-discovery. During a
skill self-update, use its deterministic source manifest plus direct bounded
inspection and record the context-sidecar limitation; never weaken the
protected-root rule merely to obtain a context receipt.

External persistence is opt-in and requires an explicit host-controlled root:

```bash
python3 -B scripts/discover_project_context.py /path/to/repository \
  --state-root /path/to/host/state \
  --persist
```

The state root must not be the repository, a child/parent of the repository,
or a protected skill, launcher, hook, receipt, worktree, or runtime root.

## Contract lifecycle

1. Run the bounded local-memory lookup and inspect only its selected paths.
2. Revalidate matches or fill memory gaps from current source.
3. Discover and inspect the context map and receipt for substantive work.
4. Validate both context contracts and the policy bundle.
5. Treat `CONFLICT`, blocking gaps, invalid fingerprints, and missing terminal
   evidence as review/blocking states for the affected task.
6. Keep implementation, review, tests, runtime, QA, publication, activation,
   worklog, and learning claims separate.

Sidecar attachment to the canonical controller packet, freshness-cache reuse,
shadow mode, enforced preflight, activation, and rollback remain follow-up
slices. Do not infer that this foundation is active merely because its CLI or
schemas exist.
