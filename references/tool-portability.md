# Portable tool adapters

This reference defines the cross-tool layer for
`buildcrew` (BuildCrew). The engineering policy remains one
controller, while Codex, Claude, Cursor, Windsurf, Gemini, Qodo, Antigravity, or
another host supplies a subordinate adapter.

All tools can share memory, learning, progress, and session storage through
`BUILDCREW_HOME`; see [storage configuration](storage-configuration.md).

For session continuity, each host that loads BuildCrew calls
`scripts/context_loop.py auto` on its sanitized intake and
`scripts/context_loop.py close` when actionable work reaches a terminal state.
Pass the original task identity and acceptance criteria when another tool
continues the task. The shared storage location enables cross-tool lookup;
static adapter fixtures do not prove that a host invoked these commands.
See [context continuity](context-continuity.md) for the call contract and
reviewed canonical memory promotion.

## Core and adapter split

The portable core owns:

- requirement normalization and route selection;
- the lightweight, normal, and full-safety effort decision;
- repository-first reuse and minimal-change rules;
- approval gates for destructive or external actions;
- evidence requirements, residual risk, and closure decisions.

An adapter only reports what its host can provide. It must not route work,
select a model, grant write authority, close the lifecycle, access secrets, or
claim that an action ran. Use the strict
[`tool-adapter-manifest-v1.schema.json`](../schemas/tool-adapter-manifest-v1.schema.json)
contract and validate a host-supplied manifest with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate_tool_adapter.py adapter.json
```

The required `authority.controller` value is `BuildCrew`. Recreate an existing
manifest with the inspector below and recheck its capability claims against the
current host before replacing it. Older manifests remain on disk until the
replacement passes validation. Reapply only verified capability settings;
older manifests are not accepted as live authority.

The read-only inspector can create a conservative baseline from observable
local facts:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/inspect_tool_capabilities.py \
  --tool-id my-ai-tool \
  --display-name "My AI Tool" \
  --repository /path/to/repository \
  --output host-adapter.json
```

Optional capabilities remain `UNVERIFIED` until a host-owned adapter supplies
bounded evidence. A host declaration is not execution proof. The safe baseline
marks external actions unavailable.

## Portable task packet

Pass normalized work between tools with
[`portable-task-packet-v1.schema.json`](../schemas/portable-task-packet-v1.schema.json).
The packet contains task facts and acceptance criteria, not a raw prompt or
authority transfer. It records required capabilities, required proof tiers,
allowed actions, scope, and the approval state.

Profile the smallest useful route and bounded effort budget with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/evaluate_portable_task.py task.json
```

The deterministic route is:

| Task shape | Route | Default budget |
| --- | --- | --- |
| Question, status, or conversation with no mutation | `DIRECT` | 0 workers, small inspection budget |
| Small low-risk source change | `FAST_DAILY` | lead-only, focused checks |
| Normal implementation or review | `NORMAL` | at most one bounded worker |
| Coupled or high-risk work | `FULL_SAFETY` | stronger checks and bounded parallelism |

The budget is a ceiling, not a requirement to spend it. The lead can stop
earlier when evidence is sufficient.

## Cross-tool compatibility

The fixtures in `tests/fixtures/tool-adapters/` cover all seven named tools,
including `windsurf.json`. They use a common example capability profile for
contract testing; actual host capabilities require local evidence.

Compare one task packet against multiple adapters without launching a tool or
performing a side effect:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/compare_tool_adapters.py task.json \
  --adapter tool-one=tool-one-adapter.json \
  --adapter tool-two=tool-two-adapter.json
```

The report uses
[`tool-compatibility-report-v1.schema.json`](../schemas/tool-compatibility-report-v1.schema.json)
and keeps `sourceOnly=true`, `executed=false`, and `sideEffects=[]`. A
`COMPATIBLE` result means the supplied manifests satisfy the same task
contract; it does not prove that the tools actually ran the task.

## Human approval

The task packet must enter the approval gate before production, deployment,
activation, deletion, migration, external messaging, or another destructive
side effect. `APPROVED` requires a non-empty approval identity. A missing or
rejected approval returns `BLOCKED`; the adapter cannot override it.

## Proof boundary

Keep these states separate:

- `AVAILABLE`: the adapter has a bounded capability declaration;
- `UNVERIFIED`: the capability may exist, but no acceptable evidence was supplied;
- `MISSING_COVERAGE`: the task needs a capability whose proof is incomplete;
- `BLOCKED`: the task cannot safely continue, commonly because a capability is
  unavailable or approval is missing.

Source-only compatibility fixtures and manifests are useful contract tests,
not live host integrations. Real browser, device, deployment, provider, and
agent evidence still requires a matching host-owned adapter and a fresh,
task-bound receipt.
