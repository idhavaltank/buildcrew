# Memory-first continuity, automatic learning, and phase progress

This reference defines the every-input local-memory lookup, the durable
learning policy, and the terminal checklist used by the standalone skill. It
keeps routine questions bounded while making new source-backed knowledge
reusable and observable.

## Every-input read-only lookup

Before source inspection, every input performs one bounded lookup against the
approved memory root—even simple questions and status checks. Resolve shared
paths through `BUILDCREW_HOME` or the application-data default described
in [storage configuration](storage-configuration.md). Use a sanitized intake packet:

```bash
python3 -B scripts/lookup_memory_context.py \
  --input-json /absolute/path/to/sanitized-intake.json
```

The `memory-context-lookup-v1` receipt reads only the global index and the
configured autonomous-engineering namespace, including a narrow
`route-map.json` selection when that index exists. It returns content hashes,
not document bodies or the raw request. `matched` still requires current-source
revalidation. `no_relevant_memory`, `unavailable`, `failed`, or `invalid`
requires source-first investigation. An optional provider may add scoped cards
only after this local lookup.

When a canonical memory root exists, lookup holds a shared generation lock for
the complete read and fails closed if it cannot acquire a safe lock. A publisher
that replaces that generation must use the same lock exclusively. The lock file
lives beside the memory root and may be created during lookup; canonical memory
content is not changed by the read. Exact registered unit, package, and
repository-context routes may select their nearest index without widening the
repository/application/module boundary.

Explicit repository/application/module fields are hard route constraints.
Malformed registries/routes, symlinked files or path components, resolved
escapes, and excluded archive/dynamic-store paths fail closed before reading.
`backend_expansion_paths` are selected only when the query contains a declared
`expand_only_if` signal.

## Learning decision

Classify the durable-write decision after the read-only lookup:

- `simple_question`, `informational`, `explanation`, `status`, `confirmation`,
  and ordinary conversation are `LOOKUP_ONLY` by default. Do not create or
  update durable learning merely because the input occurred.
- A lightweight input that produces a new sanitized, source-backed reusable
  fact may set `reusableLearning=true` and use the normal terminal write-back
  gates. Otherwise its learning outcome is explicit `none`.
- Issue, bug, debug, investigation, complex, feature, implementation, review,
  test, migration, incident, runtime, cross-repository, actionable, and
  mutating work are `REQUIRED` at a terminal outcome (`completed`, `blocked`,
  or `paused`). Active work remains `pending` until it reaches one of those
  outcomes. The adapter requires the terminal status explicitly; omission does
  not default to completed.

The learning decision is independent of model selection. A small bug still
requires a reusable lesson. A simple question always checks existing memory,
but writes only when it produced useful verified context.

## Durable local capture

Before write-back, retrieve related records using the same project and
repository plus exact canonical keys/fingerprints. Revalidate every match
against current source, tests, runtime evidence, or explicit user correction.
Then use the bundled adapter:

```bash
python3 -B scripts/record_task_learning.py \
  --input-json /absolute/path/to/sanitized-closure.json
```

The default store is
`<memory-root>/autonomous-engineering/learning-store.json`; `--store` can point
to a different approved local store. An explicit `--memory-root` also relocates
the default learning store. Legacy environment overrides remain supported when
the shared home is unset; see the storage configuration reference. The adapter
writes JSON atomically under a lock, re-reads the persisted record, and returns
both the learning-store receipt and a module/feature write-back receipt. It:

- updates an existing lesson when the canonical key and claim still agree;
- expands a related lesson when new evidence or scope is additive;
- supersedes the old record and creates a new record when the claim changed;
- durably saves evidence-backed lessons as candidates, keeps blocked/paused
  lessons as candidates, and reports `blocked` when evidence, privacy,
  validation, or storage fails; explicit reviewer authority is still required
  before a lesson becomes `verified`;
- persists only sanitized, scoped, evidence-backed fields; raw prompts,
  transcripts, secrets, tokens, and arbitrary patch text are not stored.
- requires identical project, repository, branch/tree, tenant, access scope,
  memory tier, and lesson scope before updating or expanding an existing
  record; evidence from a different tree or access boundary never merges;
- normalizes merge, expansion, and supersession to public outcome `updated`
  and reports the exact operation in `updateKind`;
- queues one immutable candidate note under
  `extensions/ad_hoc/notes/<timestamp>-<feature>-<digest>.md` using atomic
  no-clobber publication. It never directly edits canonical feature files,
  indexes, `route-map.json`, or `module-registry.json`. The timestamp is the
  lesson's stable observation time, so an identical retry resolves to the same
  path and returns `already_queued`;
- binds the candidate to the exact project, repository, branch/tree, tenant,
  and access scope. Only a non-tenant record with the single exact scope
  `project:<projectId>:read` may enter the shared Markdown queue; unknown,
  qualified, mixed, or restricted scopes return `scope_restricted` and remain
  in the scoped JSON store;
- returns top-level `partial` with actual side effects when the store or note
  was created but a later durability or required note step failed. A pre-write
  failure remains `blocked`.

The closure packet needs the following minimum fields. `evidence` entries must
be repository/tree/path/symbol/check records with a `sourceType` of `code`,
`test`, `runtime`, `user`, or `review`:

```json
{
  "taskId": "task-123",
  "status": "completed",
  "intent": "bug",
  "projectId": "general",
  "repository": "backend",
  "application": "api",
  "module": "sync",
  "feature": "replay-acknowledgement",
  "branchOrTree": "tree-current",
  "accessScope": ["project:general:read"],
  "claim": "The replay acknowledgement is persisted after the side effect.",
  "kind": "failure_pattern",
  "canonicalKey": "replay-acknowledgement",
  "fingerprints": {"path": "apps/api/sync.py", "symbol": "ReplayWorker"},
  "evidence": [{
    "repository": "backend",
    "commitOrTree": "tree-current",
    "path": "apps/api/sync.py",
    "symbol": "ReplayWorker",
    "check": "python3 -m unittest tests.test_replay",
    "sourceType": "test"
  }]
}
```

For a JSON packet already on standard input, use `--input-json -`.

The local JSON adapter is the immediately retrievable dynamic record when no
external provider is available. The ad-hoc note is a non-canonical candidate
for later repository/application/module/feature consolidation. A concurrent
collision never overwrites an existing note; it returns `conflict`. An external
provider remains optional and must not be claimed as written when the host did
not acknowledge it.

## Visible phase checklist

Actionable work starts one durable checklist. Its user-facing phases must be
derived from the actual task; the canonical AE lifecycle remains internal for
governance and closure:

```bash
python3 -B scripts/progress_checklist.py start --task-id TASK_ID \
  --display-phase 'understand=Understand the requested outcome' \
  --display-phase 'inspect=Inspect the affected implementation' \
  --display-phase 'implement=Implement the bounded change' \
  --display-phase 'verify=Verify behavior and compatibility' --color
python3 -B scripts/progress_checklist.py update \
  --task-id TASK_ID --phase context --status PASS \
  --detail "Current source inspected" --display-phase inspect \
  --next-display-phase implement --evidence "source and tests reviewed" \
  --evidence-tier test --color
python3 -B scripts/progress_checklist.py show --task-id TASK_ID --color
python3 -B scripts/progress_checklist.py complete --task-id TASK_ID --color
```

The visible markers are `🟩` completed with evidence, `🟨` active, `⬜`
pending, `🟥` blocked, and `🟦` informational/not required. Plain output uses
`[DONE]`, `[NOW]`, `[ ]`, `[BLOCKED]`, and `[INFO]`. Each meaningful
transition emits a compact `-`/`+` diff. A complete snapshot is emitted at
start, every configured number of transitions (three by default), whenever
blocked, on `show`, and at completion.

Marking `PASS` requires concrete `--evidence` (or a canonical `--detail`
when no task projection exists) and an explicit `--evidence-tier`. Marking
`BLOCKED` requires `--blocker`, at least one `--checked`, and
`--next-action`. The state file is the recovery
authority: after interruption or context compaction, run `show` before
continuing. Task ID and the task-phase fingerprint are bound to the state;
callers may use `--expected-revision` for stale-write rejection. Existing
`progress-checklist-v1` state is migrated atomically without discarding
lifecycle status, detail text, or event history, and a mode-`0600`
`.v1-backup` is retained for rollback. Legacy `PASS` text remains visibly
unverified and cannot close an open task until current evidence is attached.

Real delegated work is represented with `lane`. Creating a lane requires its
named role, purpose, one or more exact boundaries, deliverable, and verification
command. The stored lane contract always records that unrelated changes must be
preserved:

```bash
python3 -B scripts/progress_checklist.py lane --task-id TASK_ID \
  --lane-id reviewer --status IN_PROGRESS \
  --label "Independent Reviewer — compatibility reviewer" \
  --purpose "Review migration and host compatibility" \
  --boundary "Read-only progress helper and host loader" \
  --deliverable "Source-backed review verdict" \
  --verification "Run the focused progress and loader tests" --color
```

Completion is fail-closed: `BLOCKED` or unfinished phases cannot be reported as
closed; active or pending parallel lanes also block closure. `--quiet` persists
state without output and is reserved for an explicitly silent request or test
harness. Lightweight simple questions are exempt unless the user explicitly
asks for a tracked workflow.
