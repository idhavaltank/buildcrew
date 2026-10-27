# Day-to-day context continuity

The continuity loop keeps a stable task identity across sessions, including
same-day sessions and interrupted work. It is provider-neutral:

```text
input -> bounded memory lookup -> compact context -> work -> terminal closure
      -> learning store + no-clobber reference + worklog -> later resume
```

## Every-input entrypoint

After the global host integration loads BuildCrew for an input, pass a sanitized,
normalized intake to `auto`. The host supplies `requestType` and may set
`actionable` explicitly. The supported wrapper is
`scripts/global_input_adapter.py`; it removes raw prompts and authority-bearing
fields before invoking the continuity loop.
Informational inputs run bounded memory lookup without creating a session;
actionable inputs start or resume by exact task identity:

```bash
python3 -B scripts/global_input_adapter.py \
  --input-json /absolute/path/intake.json
```

For continuation, reuse the original `taskId`, objective, acceptance criteria,
project, repository, and source binding. Put the new request in the current
workflow, not in those identity fields. A conflicting identity fails closed.
The adapter and `auto` command do not install a host hook or run on their own:
configure the AI tool's supported global instruction or lifecycle hook to invoke
the adapter on each input. Keep `BUILDCREW_HOME` shared across repositories for
one Codex installation.
Keep `BUILDCREW_HOME` the same for tools sharing one project.

## Explicit start or resume

Create a sanitized input JSON object. The helper derives a portable task packet,
performs the normal bounded memory lookup, records selected source hashes, and
writes one state file below the configured continuity root. Set
`BUILDCREW_HOME` once for every tool that should share these sessions;
see [storage configuration](storage-configuration.md). Explicit roots remain
available as per-command overrides:

```bash
python3 -B scripts/context_loop.py start \
  --input-json /absolute/path/intake.json

python3 -B scripts/context_loop.py resume \
  --input-json /absolute/path/intake.json
```

`resume` requires the same task ID, project, repository, objective, acceptance
criteria, and source binding. A changed task is a conflict, not an automatic
overwrite. A resumed terminal task becomes `IN_PROGRESS` again, while its
previous summary and next action remain available for continuity.

## Close and store the session

At completed, blocked, or paused closure, provide a sanitized closure packet:

```bash
python3 -B scripts/context_loop.py close \
  --task-id TASK_ID \
  --closure-json /absolute/path/closure.json
```

The close operation updates the durable learning store, queues a sanitized
no-clobber reference under `extensions/ad_hoc/notes/`, and updates the date-wise
worklog. Canonical `MEMORY.md`, feature notes, route maps, and registries are
not silently rewritten. A later same-day closure with changed verified facts
gets a distinct candidate note; retrying identical content is idempotent.

## Promote reviewed knowledge

The task-owned candidate note is available to an exact task resume before
canonical promotion. To make a verified, reusable lesson discoverable for
later project tasks, a trusted host reviewer supplies a
`canonical-ingestion-review-v1` packet. It binds the candidate note SHA-256,
durable learning record, project/repository, source revision, and SHA-256 of
every evidence file. The promotion command checks those bytes again, rejects
restricted or conflicting knowledge, and writes a bounded, project-scoped
`MEMORY.md` atomically:

```bash
python3 -B scripts/ingest_memory_candidate.py \
  --review-json /absolute/path/approved-review.json \
  --memory-root /absolute/path/buildcrew-memory \
  --source-root /absolute/path/project
```

See `schemas/canonical-ingestion-review-v1.schema.json` for the exact packet.
The packet records the reviewer and approval identity but does not authenticate
them; the host must establish that authority. A task closure alone never
promotes a candidate. Promotion does not rewrite existing route maps or
unrelated feature notes. Exact project/repository lookup can read the new
project memory even when a route map has not yet been created. Current source
still takes precedence over that memory.

## Context reduction

Use `measure` to inspect the bounded context without writing state:

```bash
python3 -B scripts/context_loop.py measure \
  --input-json /absolute/path/intake.json
```

The report includes baseline and selected character counts, estimated tokens,
and a target status for 70% reduction. The estimate uses four characters per
token and is not a provider-token measurement. Small inputs may not reach the
target; the helper reports `NOT_MET` honestly instead of fabricating a result.
All selected references are bounded, privacy-scanned, hash-recorded, and marked
for current-source revalidation.
