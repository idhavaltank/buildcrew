---
name: buildcrew
description:
  Run a standalone, evidence-driven engineering workflow across web, backend,
  mobile, Flutter, and cross-platform repositories. Use when implementing,
  debugging, reviewing, testing, planning, investigating, or validating
  engineering work. Includes always-on intake, a ten-question requirement gate,
  formal role contracts, risk-based agent planning, tiered model selection,
  named agent delegation, strict evidence checks, regression analysis, worklogs,
  and learning capture.
---

# BuildCrew

BuildCrew is an installable plugin package containing the BuildCrew skill. The
BuildCrew skill is the first and single engineering controller and sole
lifecycle owner; it is the self-contained engineering controller for substantive work
across web, backend, mobile, Flutter, and cross-platform repositories. It owns
classification, requirements, checklist state, routing, delegation,
synchronization, verification, worklogs, learning, and closure. A host may
supply a pre-classified intake packet, but no companion skill is required. The
external autonomous-engineering project is an optional source-only reference for
contract ideas; this skill does not require that project or another installed
skill to operate.

The goal is adaptive execution: simple requests stay lightweight, while coupled
or high-risk work receives stronger planning, bounded delegation, review, and
verification.

## Activation and host integration

This package is standalone. Load this skill before beginning engineering work.
For actionable work, build the intake packet and start the task-derived
checklist before repository mutation or delegated execution. If a host already
owns classification, worklogs, or another lifecycle boundary, consume only the
explicit handoff fields the host provides and keep exactly one authoritative
controller. Do not require a product-specific loader, memory tree, model name,
or companion skill.

### One-time project onboarding

After installation, the host should call the bounded onboarding adapter for the
first input in each `(installation, host, project)` scope. Use
`engine/project_onboarding.py` or `scripts/project_onboarding.py` to classify
the project as `NEW_PROJECT`, `EXISTING_WITHOUT_DOCUMENTATION`, or
`EXISTING_WITH_DOCUMENTATION`. The first inspection returns the professional
introductory message and writes a small `PENDING` marker below an explicit
external state root. Once the host has gathered or validated sufficient context,
`complete_onboarding` stores only sanitized summary, decisions, references, and
next action, then marks the scope `COMPLETED`.

Later inputs in the same scope must not repeat the introductory message. They
continue with the normal every-input bounded memory lookup and the ordinary
direct, review, plan, or execution route. A `PENDING` marker means context
gathering is incomplete, not that the introduction should be shown again. If the
host cannot persist the external marker, report `persistence=unavailable`; only
session-level suppression is then possible. Read
[project-onboarding.md](references/project-onboarding.md) before wiring this
host boundary.

## Authority and boundaries

1. Use this skill as the single engineering lifecycle controller for its own
   inputs. Perform classification and worklog handling directly unless an
   explicit host handoff already supplies them. Revalidate any host
   classification when evidence requires it, then own checklist state, routing,
   execution coordination, delegation, synchronization, review, verification,
   learning, and closure end to end.
2. Do not create a second controller, competing lifecycle, or parallel state
   root. If a host workspace has another orchestration policy, integrate with it
   only when explicitly required; never silently run two controllers.
3. Own live agent spawning decisions, canonical agent naming, lead ownership,
   implementation coordination, final judgment, and learning write-back. Use the
   local registry and references bundled with this skill.
4. Use the external autonomous-engineering candidate only through the optional,
   source-only projection adapter for deterministic contracts, risk rules,
   diagnosis planning, reviewer capabilities, and evidence requirements. Do not
   activate it, import its controller, or require it for daily use.
5. Never activate, retire, migrate, delete, or redirect a legacy controller,
   installed skill, hook, receipt store, branch, worktree, or runtime state
   without explicit approval and a separately verified rollout plan.
6. Installation of this skill must be inert. At most one controller may be
   authoritative at a time; activation requires an explicit feature switch,
   parity evidence, and a reversible rollback switch.
7. Protected roots must come from active host configuration, be canonicalized
   with symlink awareness, and reject overlap with active skills, launchers,
   state, hooks, receipts, worktrees, or other protected runtime paths.
8. Current source, tests, runtime evidence, and explicit user direction take
   precedence over memory, plans, summaries, and agent opinions.

### Unified capability boundary

`buildcrew` is the only lifecycle authority. The other source capability may
contribute deterministic projections, but it is never a second skill invocation,
controller, state root, task store, lane owner, writer, receipt store, reviewer
closure, worklog owner, or activation authority.

The integration boundary is provider-neutral and side-effect free:

```text
project(normalized_intake) -> {
  schemaVersion,
  status: AVAILABLE | CANDIDATE_UNAVAILABLE | INVALID,
  sourceFingerprint?,
  routeProposal?, complexitySignals?, riskTier?, riskReasons?,
  requiredCapabilities?, requiredCoverage?
}
```

Only route, complexity, risk, requirements, and coverage proposals may cross
this boundary. The general controller keeps the raw request, normalized intake,
projection status, conflicts, and reconciliation result in one canonical packet;
only that packet is used for routing, execution, evidence, and closure.

### Portable tool adapters and task packets

The portable core can be used from different AI tools through subordinate
adapters. Read [references/tool-portability.md](references/tool-portability.md)
for every host integration, when work will move between tools, or when a task
needs a capability check before execution.

Each host adapter must supply a validated
`schemas/tool-adapter-manifest-v1.schema.json` manifest. The adapter reports
capabilities and proof boundaries only; it cannot route work, select a model,
grant write authority, access secrets, close the lifecycle, or claim execution.
Use `scripts/inspect_tool_capabilities.py` for a conservative read-only local
baseline and `scripts/validate_tool_adapter.py` before accepting a supplied
manifest. `AVAILABLE` is a capability declaration, not proof that the task ran;
`UNVERIFIED` must remain missing coverage until a matching host receipt exists.

Use `schemas/portable-task-packet-v1.schema.json` to pass normalized task facts
between tools without transferring raw prompts or authority. Profile it with
`scripts/evaluate_portable_task.py`: direct informational work stays
lightweight, small low-risk source work uses the lead-only fast path, normal
work receives a bounded budget, and coupled or high-risk work uses the full
safety route. The budget is a ceiling, not a quota.

Use `scripts/compare_tool_adapters.py` with the same packet and multiple
manifests for source-only compatibility checks. A `COMPATIBLE` report proves
contract coverage only; it never proves that a tool, browser, device, provider,
agent, or deployment executed.

### Explicit host execution gateway

The package remains non-activating by default, but its native-agent and provider
contracts can cross a host boundary through the opt-in
`engine/host_execution.py` gateway. A host supplies a validated
`schemas/host-execution-manifest-v1.schema.json` manifest with a pinned
executable, fixed arguments, approved working root, explicit environment-key
allowlist, timeout, and output limit. The gateway sends one JSON request over
stdin and accepts one JSON response over stdout without shell expansion.

Use `scripts/run_host_adapter.py` only when the host has deliberately supplied
that manifest. The existing bridge remains the lifecycle authority: it keeps the
task, scope, model, source fingerprint, receipt, and closure checks. A missing,
stale, or unavailable manifest returns an unavailable or failed receipt; it must
never be promoted to live success. The adapter owns credentials and external
effects, and the gateway never copies the host environment wholesale.

For actual runtime coordination, use `engine/runtime_coordinator.py` with a
validated `schemas/runtime-coordinator-config-v1.schema.json` configuration. The
coordinator constructs the native-agent and provider bridges, probes their
availability, spawns configured agent adapters, and selects a registered
provider that supports the assignment's already-resolved model and effort. It
does not choose a model, bundle credentials, or simulate an absent provider.

### Automatic day-to-day continuity

For every input after BuildCrew is loaded, call `scripts/context_loop.py auto`
with a sanitized intake. Informational inputs are lookup only; actionable inputs
start or resume an exact task. The adapter connects the existing bounded memory
lookup, portable task packet, learning store, no-clobber memory queue, and
date-wise worklog into one loop:

```text
start -> lookup -> compact context -> work -> close -> store references
  ^                                                        |
  +------------------------- resume -----------------------+
```

`auto` chooses `start` or `resume` and creates no session for informational
inputs. `start` creates a stable task identity and writes only sanitized bounded
state. `resume` requires an exact task identity and acceptance-criteria match,
repeats the memory lookup, reads the prior task-owned continuity reference, and
marks a terminal session `IN_PROGRESS` again. `close` is required for completed,
blocked, or paused work; it writes the existing durable learning record, queues
a no-clobber candidate reference, and updates the daily worklog. Read
`references/context-continuity.md` for the command contract.

The continuity state is a recovery index, not a second lifecycle authority.
Ordinary closure never edits canonical memory. A host-reviewed candidate can be
promoted into scoped canonical project memory through
`scripts/ingest_memory_candidate.py` after exact scope, candidate hash, and
current-source hash checks. The review packet does not authenticate its own
reviewer; the host owns that authority. Existing route maps and unrelated
feature notes remain untouched. `scripts/context_loop.py measure` reports the
bounded baseline, selected context, estimated reduction, and whether the 70%
target was met. The measurement is a character-count estimate using four
characters per token; it does not claim provider-token accuracy or guarantee
70–80% for every small input.

Reconciliation is controller-owned. Explicit user requirements and current
repository, test, runtime, and device evidence retain precedence. The final
route remains a general decision; complexity and risk are monotonic (use the
higher verified tier and union reasons); required capabilities and coverage are
unioned; and unresolved safety conflicts fail closed. Candidate output can raise
a floor but cannot lower a verified general risk or bypass constraints.

If the provider is unavailable, record
`candidateProjection.status=CANDIDATE_UNAVAILABLE` and the reason in the
canonical packet, then continue only on the supported general path. Do not
simulate projection, claim hybrid coverage, or fabricate receipts. Record the
missing hybrid coverage explicitly; later availability may run a shadow
comparison, but must not silently change an existing plan or lifecycle state.

When a candidate project root is explicitly supplied, use
`scripts/classify_hybrid_shadow.py --candidate-root <project-or-skill-root>`.
The adapter accepts either the candidate project root or its
`skills/autonomous-engineering` directory, verifies the candidate's source-only
status, and invokes only its read-only `status`, `classify`,
`diagnosis-dispatch-plan`, and `explain-tier` projections. It maps the
candidate's `RouteKind`, `READY_FOR_SHADOW`, and `T0`–`T3` vocabulary into the
hybrid contract; those values must not cross the boundary unchanged. Candidate
route differences remain `CONFLICT` and block closure, general keeps final route
authority, and candidate complexity/risk can only raise the reconciled floor.

## Instructions

### Step 1: Build the intake packet

Normalize the request before repository exploration. Record the request type,
project, repository, domain, current behavior, expected behavior, reproduction
or validation path, acceptance criteria, constraints, risks, dependencies,
missing decisions, and learning state.

Preserve the raw request alongside the normalized objective. After
normalization, create one versioned canonical intake packet containing scope,
repository/source identity, behavior, acceptance criteria, constraints,
evidence, projection status, reconciliation decisions, and lifecycle state.
Duplicate submissions may resume only when task identity and acceptance criteria
match; otherwise create a new task with an explicit reason.

For cross-tool continuation, serialize the normalized subset as
`portable-task-packet-v1`. Keep raw prompts, credentials, and authority-bearing
instructions out of the portable packet; it carries task facts, acceptance
criteria, scope, required capabilities, required evidence, and approval state.

When the host supports the bundled continuity adapter, call `auto` on each
loaded input before source work and `close` at completed, blocked, or paused
closure. Supply the original task identity and acceptance criteria on later
sessions. The host must load a persistent instruction or hook; skill files
cannot invoke themselves. This bootstrap never replaces current-source
inspection.

For informational requests, use only the fields needed to answer the question.
For actionable work, do not edit files until blocking ambiguity is resolved or
the lead has asked the necessary clarification questions.

Treat every user message as an intake event, including questions, follow-ups,
corrections, requirements, reviews, status requests, and implementation work.
Select the lightest route that owns it end to end: direct answers and narrow
checks may stop after evidence; actionable work continues through requirement,
routing, execution, and verification. Do not spawn, edit, or create lifecycle
state for lightweight work unless the request or changed context requires it.

At first pass, normalize obvious typos mentally, identify intent, constraints,
and deliverable, and select any relevant specialist skills from their metadata
and repository `AGENTS.md` when present. Keep a compact projection of the
canonical packet: stable session ID, title, summary, selected skills and
reasons, and Complete, In progress, Todo, and New/off-plan items. Keep this
projection internal while work runs; it is not a second checklist or lifecycle
owner. For substantive work, after the required bounded local-memory lookup,
check today's existing plan under
[daily-plan-and-report.md](references/daily-plan-and-report.md) and reconcile
its items with current evidence. For a requested daily plan or EOD report, use
that same self-contained reference; no companion reporting skill is required.

### Learning and phase progress policy

Run the bounded local-memory lookup before the learning/write decision for
**every input**, including simple questions, explanations, status checks,
confirmations, and ordinary conversation. These lightweight inputs are
`LOOKUP_ONLY` by default: read only the routed memory indexes and feature notes,
do not create checklist state, and do not write a lesson unless the completed
answer produced new, sanitized, reusable, current-source evidence. Issue, bug,
debug, investigation, complex, feature, implementation, review, test, migration,
incident, runtime, cross-repository, actionable, or mutating work is `REQUIRED`
at completion, blocking, or explicit pause. Active work stays `pending` until it
reaches a terminal state.

The memory lookup and durable-write decision are separate. A lightweight input
with no new reusable fact ends with `learning: none`; a source-backed
lightweight input may set `reusableLearning=true` and use the same evidence,
privacy, repository/module/feature, and no-clobber write-back gates as
actionable work. Never save the raw input merely to claim that every input was
learned.

For `REQUIRED` work, start the visible AE checklist immediately. Derive a small
set of user-facing phases from the actual objective; do not expose the internal
ten-phase lifecycle as generic filler. Keep the internal lifecycle phase
synchronized through `--phase`, and keep the task projection synchronized
through `--display-phase`. Use the bundled dependency-free helper without
`--quiet` during ordinary execution. Pass `--color` so friendly emoji markers
remain visible even when the host captures command output:

```bash
python3 -B scripts/progress_checklist.py start --task-id TASK_ID \
  --display-phase 'understand=Understand the requested outcome' \
  --display-phase 'inspect=Inspect the affected implementation' \
  --display-phase 'implement=Implement the bounded change' \
  --display-phase 'verify=Verify behavior and compatibility' --color
python3 -B scripts/progress_checklist.py update --task-id TASK_ID \
  --phase context --status PASS --detail "Current source inspected" \
  --display-phase inspect --next-display-phase implement \
  --evidence "source and focused tests reviewed" --evidence-tier source --color
```

The user-facing contract is `🟩` completed with evidence, `🟨` in progress, `⬜`
pending, `🟥` blocked, and `🟦` informational/not required. Without `--color`,
the helper emits semantic ASCII markers. A meaningful transition prints a
concise `-`/`+` diff; a complete snapshot is printed at start, periodically,
when blocked, on `show`, and at completion. Never mark `PASS` without evidence
and an explicit proof tier (`source`, `test`, `review`, `runtime`, `browser`,
`device`, `deployment`, or `user`). A blocked transition must record the exact
blocker, what was checked, and the safest next action. The JSON state is the
durable source of truth; after interruption or compaction, run `show` and
restore the projection.

Use the `lane` command for real parallel work. A new lane must name its role,
purpose, exact boundary, deliverable, and verification command; lane completion
also requires evidence. The coordinator still owns integration and closure.
`--quiet` is reserved for an explicitly silent user request or an internal test
harness. Lightweight questions remain quiet and do not create checklist state.
At terminal closure, pass a sanitized closure packet to
`scripts/record_task_learning.py`; it retrieves related local knowledge,
revalidates scope and evidence, then updates, expands, supersedes, or saves one
durable lesson and, only for project-wide non-tenant scope, queues an idempotent
module/feature ingestion note. The closure packet must contain an explicit
terminal status. Never persist raw prompts, transcripts, secrets, or unverified
opinions. See
[references/learning-and-progress.md](references/learning-and-progress.md).

### Mobile daily fast path (V1 compatibility)

For a routine mobile or Flutter source change, classify the intake before
repository-wide sidecars:

```bash
python3 -B scripts/classify_mobile_workflow.py \
  --platform "React Native" \
  --target apps/mobile/src/profile \
  --objective "Add an empty state to the profile screen"
```

`FAST_DAILY` restores the practical V1 flow: inspect the target and nearest
`AGENTS.md`/manifest, trace screen or event → state → repository/client → local
storage or API → sync/lifecycle boundary when relevant → rendered result,
complete the bounded repository-first reuse check when source mutation is
requested, make the smallest coherent change, and run focused checks. Keep the
lead agent lead-only. If a bounded independent worker materially helps, return
to strict model selection before creating that worker. Use
`scripts/discover_project_context.py --fast-mobile` only when a context map is
needed; it reads the explicit target and nearby context instead of walking the
whole repository.

For `FAST_DAILY`, do not automatically run full project-context enumeration, the
expanded repository-wide reusable-abstraction assessment, a QA execution packet,
the enforced context gate, or new worktree provisioning. These are not deleted;
they are opt-in for explicit runtime, shared-boundary, coupled, or high-risk
work. The bounded reuse assessment remains required for code/source mutation;
runtime-only validation does not invent one. This fast path remains source-only
and must never claim device or browser evidence.

`FULL_SAFETY` is mandatory for security, authorization, privacy, release,
production, data-loss, offline/state-sync, queue/persistence, migration, native,
device, lifecycle, conflict, or cross-boundary signals. UI-only wording such as
"retry button", "sync icon", or "queue badge" is not a boundary signal by
itself. `RUNTIME_VALIDATION` is used only when the user or acceptance criteria
explicitly require an emulator, simulator, real device, ADB, widget, or
integration run. Never lower a confirmed risk to obtain the fast path.

For actionable work, answer this ten-question requirement gate before mutation:

1. Already implemented? 2. Bug, expected behavior, missing feature, environment
   issue, or unclear requirement?
2. Current behavior? 4. Expected behavior and acceptance condition?
3. Reproduction or validation evidence sufficient? 6. Proven root cause or
   implementation gap?
4. Involved repositories, files, symbols, routes, services, and data paths?
5. Minimum coherent change? 9. Callers, flows, permissions, or data at
   regression risk?
6. Focused and real-behavior checks that will prove the result?

Mark implementation-only questions not-applicable for direct informational work.
Missing behavior, authority, safety, or verification information is blocking;
discoverable information must be investigated before asking the user.

### Step 2: Check existing knowledge

For every input, check the approved local memory root first. Resolve it through
`scripts/storage_paths.py`: `BUILDCREW_HOME` provides shared storage across
tools, with operating-system application-data defaults for new installs. Read
[storage-configuration.md](references/storage-configuration.md) for explicit
overrides and opt-in reuse of older data. Do not infer a memory root from
another tool's installed files. Start at `<memory-root>/MEMORY.md`. When the
optional general-purpose namespace is present, use
`<memory-root>/autonomous-engineering/MEMORY.md`, `route-map.json`, and
`module-registry.json` to select only the nearest
repository/application/module/feature notes. The dependency-free helper emits a
read-only, content-hashed receipt and never returns the raw input:

```bash
python3 -B scripts/lookup_memory_context.py \
  --input-json /absolute/path/to/sanitized-intake.json
```

Open only the receipt's `selectedPaths`, in order. Search exact issue keys,
errors, symbols, paths, routes, features, and user corrections before broader
historical context. Return compact evidence cards with scope, confidence,
freshness, and source references. Treat every retrieved lesson as a hypothesis
until it is revalidated against current source, tests, runtime state, or
explicit user confirmation.

The local memory check comes before any optional provider. If local memory has
no relevant route, is stale/incomplete, or cannot be read, record
`no_relevant_memory`, `unavailable`, `failed`, or `invalid` and continue with
current source. An available provider may add scoped cards after the local
check; provider absence never blocks source-first work. Never invent a retrieval
result or allow memory to override current evidence.

For an active continuity session, also read the exact task-owned no-clobber
reference recorded in the prior state. Keep it bounded and privacy-scanned; the
reference is a hypothesis until current source and tests revalidate it.

Repository, application, and module values supplied by the intake are hard route
boundaries, not ranking hints. Reject malformed route/registry entries,
symlinked indexes, symlinked path components, escapes, and resolved excluded
areas before reading. A matched route may add `backend_expansion_paths` only
when one of its `expand_only_if` signals is present in the bounded query.

Before investigation, build an exact query from issue keys, errors, features,
corrections, paths, symbols, routes, repository, and task type. Record
`matched`, `no_relevant_memory`, `unavailable`, `denied`, `failed`, or
`invalid`. Revalidate every match against current source, tests, runtime
evidence, or explicit user direction. After completion, blocking, or pause,
privacy-scan, deduplicate, conflict-check, write back reusable lessons, and
re-read both receipts. A failure before persistence is `blocked`; if the JSON
store changed but a later durability or required candidate-note step failed, the
top-level result is `partial` and must report the real side effect.

#### Feature-wise memory routing

When `<memory-root>/autonomous-engineering/route-map.json` and
`module-registry.json` are present, use them as the bounded retrieval index:

1. Normalize the input into repository, application/service, module, feature,
   error, and task-type signals.
2. Select the narrowest matching route in `route-map.json`; open the root
   `MEMORY.md`, the selected feature/workflow files, and the nearest module
   `INDEX.md` only.
3. Use `module-registry.json` to confirm the live repository and module boundary
   before reading source. The registry is an inventory, not a reason to scan
   every listed module.
4. Treat task IDs and old filenames as search triggers/history references, not
   canonical memory targets. `_archive/**`, staging trees, compatibility
   redirects, `learning-store.json`, and its lock are excluded from normal
   retrieval.
5. Follow `expand_only_if` and `backend_expansion_paths` only when the input or
   current evidence contains the corresponding boundary signal (for example API,
   save, database, permission, schema, or network behavior).
6. If no route matches, use the bounded fallback in `route-map.json` and record
   `no_relevant_memory` when nothing relevant is found; never recursively scan
   the complete memory root by default.
7. At learning closure, keep the locked JSON store as the atomic dynamic record.
   Also queue one unique, no-clobber note under
   `<memory-root>/extensions/ad_hoc/notes/`, carrying repository, application,
   module, feature, route, evidence, record identity, and exact
   project/repository/tree/access-scope binding. Tenant-bound or non-project
   access scopes remain only in the scoped JSON store and return
   `scope_restricted`; they never enter the shared Markdown queue. Do not edit
   `MEMORY.md`, an existing feature file, `route-map.json`, or
   `module-registry.json` directly while another session may be generating
   context. The memory ingester performs the later deduplicated canonical merge.
   A collision with different content is `conflict` and must never overwrite
   either writer. Task IDs remain metadata, not canonical feature filenames.

### Retrieval, context, and cost controls

Use the bounded defaults in `references/knowledge-rag.md`: lightweight `0–3`
cards/1,200 tokens, normal `3–5`/2,000, and complex `5–8`/3,500. Filter scope
before retrieval, search exact fingerprints first, expand at most once, return
cards rather than documents, fetch detail only when needed, and cache normalized
query/scope/index-version briefly when supported. Track retrieval p50/p95
latency, injected tokens, embedding/rerank cost, cache hit rate, and
source-first fallback; never widen scope or lower evidence quality to meet a
speed or cost target.

### Project context bootstrap (read-only foundation)

The memory-first receipt precedes source context discovery. Use its routed
repository/application/module/feature as the initial target. When the receipt is
`no_relevant_memory`, stale, unavailable, failed, or invalid, derive the target
from the current repository and code instead. In both cases, current source
inspection is mandatory for behavioral claims and source mutation.

The package now includes the first inert slice of the Project Context and Policy
Bundle design. Use the full form before substantive repository mutation when the
request is normal, coupled, or high-risk. Routine mobile source work may use the
target-scoped fast form described above:

```bash
python3 -B scripts/discover_project_context.py <repository-root> \
  --target <repository-relative-target> \
  --fast-mobile \
  --task-type NEW_TASK \
  --objective "normalized objective"
```

The command returns a `project-context-map-v1`, a terminal
`context-discovery-receipt-v1`, and a `policy-bundle-v1`. It is read-only by
default: it does not execute project commands, create or edit repository
documents, invoke another controller, create agents, or claim browser/device
evidence. It records paths, scopes, hashes, profiles, gaps, conflicts, and
required evidence rather than copying full documents into the packet.

Use `--persist --state-root <host-controlled-external-root>` only when the host
explicitly supplies an approved external state root. The repository, its
host-private configuration directories, skill roots, worktrees, and runtime
folders are never valid persistence fallbacks. A failed external write remains
explicit as `persistence=unavailable` or session-only state.

Validate sidecars before using them:

```bash
python3 -B scripts/validate_project_context.py <map-or-receipt.json>
python3 -B scripts/validate_policy_bundle.py <policy-bundle.json>
python3 -B scripts/explain_policy_rule.py V3-CONTEXT-001
```

The current slice provides the versioned contracts, bounded discovery,
fingerprints, privacy and protected-boundary checks, platform/product signal
selection, common/shared/platform rule catalogs, deterministic policy
resolution, external-state safety, and report-only project-context shadow
binding. Use `scripts/bind_project_context_shadow.py` to compare a canonical
intake projection with its context map, receipt, and policy bundle. Shadow
binding may report `BLOCKED` or `MISSING_COVERAGE`, but it never enforces a
mutation gate, routes work, selects a model, selects an agent, or closes a task.
An enforced controller gate remains a separate follow-up slice. Read
[references/project-context.md](references/project-context.md) when this
bootstrap is relevant. Current source, tests, and real runtime evidence still
outrank the map, policy bundle, memory, and historical documents.

### Step 3: Select the route and risk tier

Apply deterministic routing after canonical-packet reconciliation and before
choosing agents or editing files. A capability provider may propose a route, but
this controller selects the final route using the precedence and risk rules
above. Use the smallest route that satisfies the request:

- DIRECT for a simple answer with no engineering state.
- DIRECT_VALIDATION for a focused validation or inspection request.
- REVIEW_ONLY for source, diff, plan, or architecture review.
- PLAN_ONLY when planning is requested without execution.
- NEW_TASK for a new implementation or investigation.
- CONTINUE_TASK for matching caller-supplied task context.
- EXECUTE_READY_PLAN only when requirements, lane, and write authority are
  ready.
- RESUME_RUN only for validated recoverable state.
- DIRECT_WORK for explicit local work that does not publish, deploy, merge,
  push, release, or activate a workflow.
- BLOCKED when the requested action violates safety or authority boundaries.

Classify complexity as lightweight, normal, or coupled. Raise the tier for
authentication, authorization, data loss, migrations, payments, security,
offline sync, queues, persistence, device lifecycle, deployment, or other
verified high-risk boundaries. Caller hints may add evidence or raise a floor;
they may not downgrade a verified risk.

When a portable task packet is available, profile it before topology selection
with `scripts/evaluate_portable_task.py`. Use its bounded effort budget to keep
direct questions lead-only, small low-risk work on the fast path, normal work
bounded, and coupled or high-risk work on the full-safety route. The profile
does not lower a verified risk tier.

When the capability provider is unavailable, select the route from the general
intake and current evidence, while retaining the explicit
`CANDIDATE_UNAVAILABLE` status and missing hybrid-coverage note.

### Step 4: Plan the smallest useful topology

Use lead-only handling for simple work. For normal work, add one independent
explorer or reviewer when it materially reduces risk. For coupled work, select
functional roles and bounded waves such as knowledge researcher, explorer,
root-cause finder, regression analyst, specialist, code reviewer, testing agent,
and verification agent.

Run independent evidence collection in parallel when scopes do not overlap.
Serialize persistence, sync, queue, migration, dependency-injection, device, and
other coupled boundaries. Do not spawn one agent per listed role by default.

Before every real spawn, reserve an unused canonical agent name from the
registry, put the exact name as the first line of the private prompt, select the
smallest suitable model and reasoning effort, and define objective, context,
file boundary, write permission, risks, expected evidence, and completion
criteria. Never simulate an agent or expose runtime UUIDs.

Model selection is executable and complexity-driven. For mobile intake, first
use `engine/mobile_workflow.py` (or its CLI) to normalize platform, explicit
runtimes, and verified risk signals without scanning the repository. The source
of truth is `config/model-selection-v1.json`, validated by
`scripts/validate_model_selection.py`; the host catalog only reports what can
actually be invoked. The selector uses the already-normalized `taskClass`; it
does not rescan the repository or inspect raw prompts while choosing a model.
Workers and coordinators are distinct assignments.

For a validated, lead-only `FAST_DAILY` mobile decision, use the
`FAST_DAILY_DIRECT` result from `resolve_mobile_model_selection()` (or the
`selection` object printed by `scripts/classify_mobile_workflow.py`) directly
for the current lead session. Do not call `select_model_assignments()` or
construct strict `taskFacts`, source/policy fingerprints, host catalogs, expiry
bindings, coordinator scopes, or fallback evidence for this path. The direct
V1-compatible rules are FastModel/low for lightweight, FastModel/medium for
small, and BalancedModel/high for explicitly normal source work. This bypasses
only assignment selection overhead: target context, minimal-change, focused
tests, source-only honesty, and the no-runtime-claim boundary still apply. The
direct envelope is session-only, has no worker assignment, and is not
dispatchable. If the classifier returns `FULL_SAFETY` or `RUNTIME_VALIDATION`,
or intake requests a worker/coordinator/native action, the result is
`STRICT_REQUIRED` and the existing policy selector remains mandatory.

The model and worker assignment rules are:

- **Question, lookup, or narrow read-only validation**
  - Coordinator: None
  - Worker assignment: None; lead-only evidence
- **One small change where delegation adds no material safety**
  - Coordinator: None
  - Worker assignment: None unless focused independent review is useful
- **Routine mobile/Flutter screen, widget, state, or local source change with no
  risk signal**
  - Coordinator: None
  - Worker assignment: Direct lead selection: FastModel/low for lightweight,
    FastModel/medium for small, BalancedModel/high for explicitly normal; no
    worker assignment
- **Explicit mobile emulator/simulator/device validation without a coupled
  boundary**
  - Coordinator: `model-balanced` / `normal-coordinator` when coordination helps
  - Worker assignment: `model-balanced` at `high` with platform-correct runtime
    evidence
- **Lightweight lookup or narrow validation**
  - Coordinator: None
  - Worker assignment: `model-fast` at `low` for a bounded worker when
    delegation is useful
- **Small bounded change**
  - Coordinator: None unless coordination materially helps
  - Worker assignment: `model-fast` at `medium`
- **Normal defined work with independent steps**
  - Coordinator: `model-balanced` / `normal-coordinator` when coordination helps
  - Worker assignment: `model-balanced` at `high`
- **Coupled, cross-repository, architecture, security, or difficult root cause**
  - Coordinator: `model-advanced` / `high-risk-coordinator` when coordination
    helps
  - Worker assignment: `model-advanced` at `max` for bounded exploration,
    implementation, review, and testing
- **Final high-risk audit**
  - Coordinator: `model-advanced` / `final-audit` or trusted host reviewer
  - Worker assignment: `model-fast` at `max` only for delegated evidence
    collection
- **Preferred lightweight/small model unavailable**
  - Coordinator: No automatic coordinator escalation
  - Worker assignment: One policy-approved BalancedModel/high
    `FALLBACK_EVIDENCED` worker scope when the configured fallback is available;
    otherwise `CAPABILITY_UNAVAILABLE`
- **Preferred normal model unavailable**
  - Coordinator: Coordinator remains unchanged
  - Worker assignment: One policy-approved AdvancedModel/max
    `FALLBACK_EVIDENCED` worker scope; multiple scopes remain unavailable
- **Preferred coupled/high-risk model unavailable**
  - Coordinator: AdvancedModel coordinator remains unchanged
  - Worker assignment: No automatic fallback; only an explicit, trusted,
    task/scope/policy/expiry-bound exception may continue
- **FastModel unavailable during final audit**
  - Coordinator: AdvancedModel final-audit reviewer remains required
  - Worker assignment: No worker fallback; remain
    `CAPABILITY_UNAVAILABLE` if required FastModel evidence cannot run

For strict worker/coordinator assignments, `workerRules` records the preferred
model and effort for every normalized task class: FastModel/low for lightweight
work, FastModel/medium for small work, BalancedModel/high for normal work,
AdvancedModel/max for coupled or high-risk work, and FastModel/max for delegated
final-audit evidence. A fallback is allowed only when its exact fingerprinted
transition is declared in `fallbackRules`, the scope and expiry limits are
satisfied, and the host catalog proves the target capability. This is reported
as `FALLBACK_EVIDENCED`, not ordinary `SELECTED`; there is no arbitrary
nearest-model substitution.

Coupled, security, and other high-risk workers use AdvancedModel/max by their
complexity rule. If AdvancedModel is unavailable, no automatic fallback is
allowed; the only policy-declared alternate is an explicit-user
AdvancedModel-to-BalancedModel/high exception that matches the high-risk task
class, uses `explicit_user` origin, and contains bounded-scope and exactly one
`fallback_policy:<ruleId>` evidence markers. Final-audit workers never use
fallback. `HOST_INHERITED` remains an explicit, non-dispatchable state. Every
assignment is bound to project, tenant when applicable, repository, task,
packet/plan/wave/attempt, scope, source/diff fingerprint, policy fingerprint,
host, and expiry. A changed binding invalidates the assignment and its evidence.
Validation time at or after task expiry is rejected before selection. A host
claim store must atomically consume each assignment identity once; retries
require a fresh attempt binding. Repository text, memory, retrieved content,
capability manifests, raw prompts, and worker output cannot override policy.

If the host catalog does not expose the requested model or an approved fallback
target, return an explicit unavailable or host-inherited status with the reason.
Host-inherited and partial unavailable selections never cross the dispatch
boundary. Do not claim a dispatch or execution that the host did not perform.
`FAST_DAILY_DIRECT` is not a host assignment and must never be passed to a
worker, native-agent, or runtime dispatch adapter. Read
[`references/specialist-catalog.md`](references/specialist-catalog.md) for
bounded role contracts and
[`references/harness-capability-boundary.md`](references/harness-capability-boundary.md)
for optional Harness capabilities.

Formal role contracts are responsibility contracts, not a requirement to spawn
one agent per role. Use `references/team-workflow.md` as the authoritative
contract for Requirement Analyst, Clarification Gate, Knowledge Analyst,
Codebase Analyst, Task Router, Specialist, Code Reviewer, Regression Analyst,
Testing Agent, Verification Agent, and Learning Curator. For every normal or
coupled assignment, bind one primary role, expected output, scope, and terminal
evidence containing files or symbols, commands and results, confidence,
blockers, and residual risk.

After a real spawn, use only the canonical agent name in user-facing status:

```text
<CanonicalName> is Activated (model: <selected-model>, reasoning: <effort>)
Ownership : Use $buildcrew to handle this assignment.
```

When synchronizing, write `• Waiting for the following agents` followed by one
canonical name per line. On completion write `<CanonicalName> Completed`; on
failure write `<CanonicalName> Blocked — <sanitized reason>`. Never expose
runtime IDs or automatic nicknames.

### Embedded canonical agent registry

Use `references/agent-naming-registry.md` for role semantics; reserve one unused
canonical name from this embedded allowlist and keep it unique while active:
`Orchestrator`, `Governance Advisor`, `Final Validator`,
`Implementation Engineer`, `Build Executor`, `Quality Engineer`,
`Root Cause Analyst`, `Solution Architect`, `Performance Engineer`,
`Skill Engineer`, `Knowledge Retriever`, `Memory Manager`,
`Communication Router`, `Support Collaborator`, `Specialist Engineer`,
`Prototype Engineer`, `Recovery Engineer`, `Constraint Solver`,
`Security Red Teamer`, `Threat Simulator`, `Access Control Auditor`,
`Chaos Engineer`, `Bottleneck Analyst`, `Independent Reviewer`,
`Infrastructure Engineer`, `Context Coordinator`, `Resource Manager`.

For every normal or coupled dispatch, also define a bounded packet: one primary
objective, an explicit allowlist and exclusion list, a maximum exploration
boundary, a checkpoint expectation, required receipt fields, and stop
conditions. Split broad repository exploration into independent packets; keep
static tracing, persistence/schema analysis, implementation, and device/runtime
verification separate when their evidence or failure modes differ. Read
`references/agent-dispatch-and-evidence.md` for the checkpoint and termination
protocol.

When the runtime supports progress telemetry, require a heartbeat or checkpoint
before each wait window. A useful checkpoint records the current phase, last
command or tool, files or symbols inspected, partial findings, blockers, and
next action. A checkpoint is progress evidence, not a terminal receipt. Keep the
lead agent working on non-overlapping work while a worker runs; synchronize only
when its result is required.

### Step 5: Verify the executing path

For non-trivial behavior, trace the narrowest real path:

user action → UI or entry point → client or hook → route/controller → service or
business logic → database/cache/queue/external integration → response or
rendered state.

Identify callers, dependencies, transformations, side effects, relevant files,
symbols, existing tests, and coverage gaps. Prove a bug root cause or a feature
extension point before implementation. If evidence contradicts the intake
packet, return to the requirement gate instead of patching around symptoms.

### Repository-first reusable abstraction discovery

For every actionable code or source mutation, complete a bounded
repository-first reuse check before editing. This gate is always active; it is
not limited to UI work or to tasks already described as reusable. Read
`references/reusable-component-discovery.md` for the detailed procedure. Search
the target repository and nearest approved package/module for exact and semantic
equivalents: components, hooks, clients, services, contracts, generated types,
validators, repositories, utilities, scripts, configuration, schemas, tests, and
established local patterns. Inspect a plausible candidate's public boundary and
implementation/data-flow plus at least one representative usage, test, or
sibling pattern. A direct answer or read-only investigation has no mutation to
gate; if it turns into a source edit, complete this assessment first.
Runtime-only validation and documentation-only edits do not require this
code-reuse gate; documentation still follows the nearest established style.

This adds reuse evidence across frontend, backend, mobile, Flutter, scripts,
configuration, schemas, and tests. It does not replace contract-first,
authorization, validation, rate-limit, persistence, migration, runtime, or
platform-specific gates.

For `FAST_DAILY` mobile work, this is a bounded compatibility check, not a
repository-wide gate: search the explicit target and nearest module boundary and
inspect the nearest public API/pattern plus one nearby usage or test when a
candidate exists. If the executing-path trace already proved the compatible
local widget, hook, repository, client, helper, or pattern, cite that evidence
and record `REUSE`. Run the expanded procedure for shared/reusable requests,
cross-package changes, unclear ownership, or any non-fast route.

The policy rule alone is not mutation authority. Before code/source mutation,
create a current, task/source-bound `repository-reuse-assessment-v1` record and
validate it with `scripts/validate_reuse_assessment.py`. Pass that record to
`scripts/enforce_project_context_gate.py --action mutation --reuse-assessment <path>`.
Missing, malformed, stale, wrong-task, wrong-source, or incomplete evidence must
return `BLOCKED`; `NEW` requires bounded negative-search evidence and
`DOCUMENTED_EXCEPTION` requires a reason.

Record the result in the existing task packet, plan, or change ledger as
`REUSE`, `EXTEND`, `NEW`, or `DOCUMENTED_EXCEPTION`. Reuse a compatible
component, hook, API client, service, endpoint/query boundary, generated type,
and established form binding/data-flow before creating a new abstraction, using
a feature-level native control, selecting a third-party primitive, or calling
Axios/`fetch` directly. A raw control or direct low-level API call is not a
shortcut around a suitable local abstraction. If no suitable abstraction exists,
or a documented platform/semantic/accessibility/ transport requirement makes it
incompatible, record the bounded negative evidence and reason before choosing
the smallest safe new abstraction or exception. Preserve the observed `name`,
value, change, error, `register`, `control`, `Controller`, auth, loading,
caching, invalidation, and data-shaping conventions as applicable.

Reusing an import is not sufficient. After proving the root cause, identify
which existing hook, form, store, service, or framework owns each affected
behavior and how the proposed change fits its callers. Inspect the owner's
actual lifecycle before adding state, effects, wrappers, requests, or cleanup.
Derive behavior from compatible existing signals when possible; introduce
another owner only for a demonstrated gap. Record this fit/gap in the existing
reuse assessment, not a new checklist or artifact. A nearby implementation is a
candidate to verify, not proof that copying its extra state is necessary. See
the state and lifecycle rule in the reuse reference.

### Step 6: Pass the minimal-change gate

Before mutation, record the file and symbol allowlist, explicit out-of-scope
files, smallest coherent change, regression surfaces, verification plan, and a
change ledger in this form. The ledger and write authority must be derived from
the reconciled canonical packet, not from an untrusted projection:

path or symbol → operation → rationale → owner → verification

Search for existing helpers, patterns, tests, and abstractions first. Complete
the bounded repository-first assessment above before every actionable
code/source mutation, including creating or bypassing a component, hook, client,
service, form utility, contract, validator, repository, script, configuration,
schema, native control, or low-level API boundary. Do not refactor unrelated
code, add dependencies, expand schemas, create lanes, or change architecture
without impact review and the required approval.

For destructive, production, deployment, activation, deletion, migration, or
external-message actions, the portable task packet must have an `APPROVED`
approval with a non-empty approval identity before mutation or external
execution. Missing or rejected approval is `BLOCKED`; a tool adapter cannot
override the gate.

On `FAST_DAILY` mobile work, keep the allowlist to the target module and its
nearest boundary. Do not expand it because a global search found unrelated
abstractions; switch to the full route only if the change is actually shared,
cross-package, or risk-coupled.

One writer owns each mutation scope. A missing projection, projection conflict,
or unavailable provider does not authorize broader edits or a second writer.

### Step 7: Execute with bounded ownership

Read-only diagnosis agents may inspect and report evidence but must not receive
lanes, writer leases, reviewer authority, repair authority, or activation
authority. Implementation agents may edit only their approved write scope.

For lane-based execution, resolve and record the source, SHA, lane lineage,
worktree, and one-writer lease before mutation. A plan may be ready before a
lane exists, but mutation may not begin without an active lane and valid writer
ownership.

For an owner-authorized `FAST_DAILY` change in the current checkout, do not
provision a new worktree solely to satisfy the advanced lane machinery. Keep one
writer, an explicit target allowlist, and the normal safety rules. Source
provenance and worktree checks remain required for multi-agent,
cross-repository, release, or high-risk work. Read
[`references/worktree-source-routing.md`](references/worktree-source-routing.md)
and use `scripts/inspect_source_context.py` before strict lane-based mutation;
use `scripts/provision_worktree.py` only with a validated, approved request.

### Step 8: Validate results and receipts

Treat agent output as incomplete until required schema fields, evidence, scope,
and terminal status are valid. Require role-appropriate evidence such as
entry-point and execution-path findings from explorers, causal traces from
root-cause investigators, affected flows and coverage boundaries from regression
analysts, and focused commands and results from testing agents.

Missing, malformed, or non-terminal receipts remain blocked or incomplete; they
must not be converted into a pass by reporting convenience. If the host does not
provide structured receipt validation, require a terminal evidence block
containing scope, files or symbols, commands and results, confidence, blockers,
and residual risk. Do not claim schema validation in that fallback mode.

Where structured receipts are used, bind them to the packet, task, plan,
repository tree or source SHA, role, host/runtime identity, evidence hashes or
locations, terminal status, timestamps, and expiry. Never infer capability
coverage from a non-empty receipt ID alone.

Classify worker termination explicitly: `completed` requires a valid terminal
receipt; `in_progress` means the runtime still reports active execution;
`timed_out` means the bounded wait budget expired without a terminal receipt;
`interrupted` means cancellation was requested; `crashed` requires an explicit
runtime error; and `shutdown` means execution stopped without a valid terminal
receipt. Use `MISSING_COVERAGE / <state>` for a non-completed state and never
convert it to success because the worker produced partial output or because the
controller needs to close the task.

After a wait budget expires, make at most one bounded request for a final
receipt or checkpoint, then use the runtime's status/close mechanism if
available. Do not repeat blind waits. If the runtime still reports `running`,
preserve that fact and report the observability limitation rather than claiming
the worker was closed or completed. Preserve all partial evidence for triage.
Retry only with a narrower packet, a fresh canonical name, and the uncovered
scope stated explicitly; do not duplicate the same broad dispatch by default.

### Step 9: Review, test, and verify real behavior

After implementation, perform code review, regression analysis, focused tests,
and real-behavior verification proportionate to risk. Prefer targeted commands
over whole-workspace fan-out. For APIs, trace client-to-controller-to-service
behavior. For UI, verify rendered behavior. For mobile, verify lifecycle,
offline, sync, device, and platform-specific behavior where relevant.

For `FAST_DAILY` mobile source work, verify the changed screen/widget/state path
and focused unit or component tests. Runtime/device checks are not implied by a
source-only request. When runtime validation is explicit, use only a configured
platform-correct adapter; this package supports browser, Android, iOS Simulator,
and Flutter host adapters when the matching host runtime and target are
configured.

For every actionable code/source mutation, review the recorded assessment and
final diff for unexplained duplicate components, hooks, clients, services,
contracts, validators, repositories, utilities, scripts, configuration, schemas,
or local patterns. Also reject unexplained feature-level raw controls, direct
low-level API calls, incorrect form bindings, and bypassed
auth/loading/cache/error or data-shaping conventions. Return to the discovery
gate when a suitable local abstraction or pattern was bypassed without a
documented exception.

Review each added state variable, effect, wrapper, and request against the
existing owner: what required behavior would fail without this addition? Remove
additions that only duplicate existing behavior. Tests must preserve the
relevant lifecycle of reused dependencies; a mock that fixes a hook's loading
flag to false cannot establish that extra local loading state is needed. See the
reuse reference for the lifecycle test boundary and justified exceptions.

Map every material requirement to observable evidence and residual risk. A
helper-level test is insufficient when the requirement concerns end-to-end
behavior.

Record `schemas/verification-matrix-v1.schema.json` with one row per material
requirement:
`requirement → artifact/behavior → evidence/check → status → residual risk`. Use
`pass`, `not_applicable`, `not_run`, `fail`, or `blocked`; required `not_run`,
`fail`, `blocked`, `MISSING_COVERAGE`, or unverified rows prevent closure.

### Human-readable code gate

For every actionable source mutation, run the bounded readability assessment
from `engine/readability_gate.py` or `scripts/validate_readability.py` on the
changed files before closure. It checks formatting, Python syntax and function
shape where supported, generic control-flow complexity, nesting, unfinished
placeholders, missing explanations for non-obvious logic, and language coverage.
A `PASS` is required for direct closure; `NEEDS_REVIEW` requires a reviewer to
resolve or explicitly accept each finding, and `BLOCKED` prevents closure. Use
the repository's formatter, compiler, linter, and language-aware reviewer when
the generic check reports limited coverage. The assessment must remain redacted
and source-only. It always reports `humanAuthorship=NOT_DETERMINABLE`: the
workflow can require human-readable quality, but it cannot prove who authored
the code. Read [human-readable-code.md](references/human-readable-code.md).

### Step 10: Close the task cleanly

Report what changed or was found, verification commands and results, residual
risk, limitations, and next action. Close completed agents when supported;
update the daily worklog with the bundled `scripts/update_daily_log.py` after
completion, blocking, or explicit pause, passing the full current progress state
and stable session ID; complete the visible checklist with
`scripts/progress_checklist.py`; and for reusable verified work invoke
`scripts/record_task_learning.py` with a sanitized closure packet. When the user
requests a plan or EOD report, prepare it from the verified plan and worklog
context under [daily-plan-and-report.md](references/daily-plan-and-report.md),
without requiring another skill. Produce a learning outcome: saved, updated,
candidate, needs review, rejected, redacted, blocked, or none. Simple inputs
still perform the bounded memory read first; they report
`learning: none (no_new_reusable_knowledge)` unless their source-backed result
sets `reusableLearning=true` and passes the normal durable-write gates.

For a continuity-managed task, `scripts/context_loop.py close` is the single
bounded close adapter: it performs the terminal learning write, creates the
task-owned memory reference, and then upserts the date-wise worklog. A worklog
failure or learning/reference failure is reported as `PARTIAL`; it is never
hidden behind a successful-looking closure.

Before closure, verify requirement, current/expected behavior, validation,
knowledge, scope, implementation, review, regression, tests, receipts, worklog,
and learning. Any required `not_run`, `fail`, `blocked`, `MISSING_COVERAGE`, or
unverified state prevents `CLOSED`; report the limitation and next action
instead.

## Operating modes

| Mode        | Behavior                                                                                                        |
| ----------- | --------------------------------------------------------------------------------------------------------------- |
| Lightweight | Direct answer or narrow check; no unnecessary agents or lifecycle state                                         |
| Normal      | Requirement gate, targeted source analysis, bounded implementation, review, and focused tests                   |
| Coupled     | Deterministic risk tier, evidence waves, serialized boundaries, strict receipts, and expanded regression checks |
| Shadow      | Compare proposed route, risk, topology, and receipts with existing behavior without activation or mutation      |

### Agent Fast Mode

The case-insensitive phrase `let's start the crew` selects focused
follow-through after the normal intake gate. It never bypasses safety, privacy,
authority, naming, minimal-change, review, testing, or closure rules. For normal
or coupled work, use one bounded explorer plus one regression/adversarial
reviewer when available; keep lead work moving and synchronize only when needed.
Keep lightweight/direct requests lightweight.

## Runtime capability boundary

This skill is self-contained at the policy and workflow level. The host runtime
still determines which capabilities are physically available:

- If real agent-spawn tools are available, use bounded multi-agent execution.
- If agent-spawn tools are unavailable, continue as the lead agent and report
  the limitation; do not simulate agent results.
- If a validated host-execution manifest and matching adapter are supplied,
  connect them through `SubprocessNativeAgentTransport` or
  `SubprocessProviderTransport`. Probe the manifest before dispatch and keep
  non-live results unavailable. The gateway supplies process bounds only; the
  host still owns credentials, cancellation, sandboxing, network policy, and
  external side effects.
- Use `RuntimeCoordinator` when more than one runtime capability is needed. It
  is the skill-owned registry and dispatch layer above those transports; invoke
  it only after the requirement, scope, permission, and runtime-control checks
  are satisfied.
- Use `scripts/host_integration_status.py` as a capability report, not an
  execution receipt. `AVAILABLE` means host prerequisites were detected;
  `executionReady: false` means task-specific configuration is still missing;
  `CONFIGURATION_REQUIRED` means an adapter or approval was not supplied; and
  `NOT_SUPPORTED` means no matching runtime adapter is bundled. Only a valid
  host-bound receipt can prove execution.
- If a model catalog is available, resolve the fingerprinted task-class policy:
  FastModel/low for lightweight work, FastModel/medium for small work,
  BalancedModel/high for normal work, and AdvancedModel/max for
  coupled/high-risk work. Final-audit coordination remains AdvancedModel/max
  while delegated evidence may use FastModel/max. Use only the configured,
  bounded fallback transitions described above; otherwise return the explicit
  host-inherited or unavailable state and never silently change a worker's
  model.
- Always run the bounded approved local-memory lookup first. If an optional
  knowledge provider is available, use it only for additional scoped retrieval.
  Otherwise continue source-first with the bundled memory rules.
- The local learning and checklist helpers are always available as
  dependency-free fallbacks: `scripts/lookup_memory_context.py` performs
  read-only bounded routing; `scripts/record_task_learning.py` writes the
  approved local JSON learning store and, for an allowed project-wide scope, one
  idempotent no-clobber ad-hoc module/feature ingestion note without changing
  canonical memory files; and `scripts/progress_checklist.py` writes only its
  scoped progress state. An unavailable external RAG provider does not disable
  required local retrieval, learning, or phase progress.
- If the bundled worklog script cannot write its configured root, use its
  fallback or report the tracking limitation.
- If heartbeat, partial-output, cancellation-reason, or structured-shutdown
  telemetry is unavailable, apply the prompt-level checkpoint and receipt rules,
  label the missing observability, and do not infer the absent state.

Capability availability is not a companion-skill dependency and must never be
represented as proof that an agent, model, receipt, or test actually ran.

### Runtime control enforcement slice

When a host has a real executor, call `engine/runtime_control.py` before each
step and tool action. `RuntimeControlPlane` applies explicit wall-clock, step,
tool, worker, estimated-token, cost, context, and repeated-action limits;
authorizes actions from the trusted task packet; requires the packet's approval
for external side effects; and emits redacted ordered events. Use
`compact_context()` to preserve the objective and acceptance criteria while
dropping lower-priority references. The host must inject the executor and still
owns process cancellation, target allowlists, credentials, network policy,
sandboxing, transactions, rollback, and platform evidence. The control-plane
snapshot remains source-only and never claims live execution. The host-execution
gateway is subordinate to this control plane; it does not replace the
authorization or cancellation checks. Read
[runtime-control.md](references/runtime-control.md) before integrating a host
runtime.

### Bundled contract and shadow tools

The standalone package includes provider-neutral contracts under `schemas/` and
dependency-free validation tools under `scripts/`. Use
`scripts/validate_hybrid_contracts.py` to validate canonical packets and
task/fingerprint-bound receipts. `scripts/candidate_projection.py` provides the
optional source-only candidate adapter. Use `scripts/classify_hybrid_shadow.py`
to compare deterministic general route, complexity, and risk decisions against
optional candidate fixtures or an explicit candidate project root. The report
includes visible conflicts and a controller-owned reconciled decision; it writes
no repository, worktree, state, receipt, configuration, or activation data.
These tools report `CANDIDATE_UNAVAILABLE` when no candidate provider is
supplied; they must not simulate candidate output or claim hybrid coverage.

#### QA contract layer

The source-only QA contract layer is available through
`scripts/validate_qa_contracts.py` and the versioned schemas under `schemas/`.
It validates the request, workflow map, execution packet, finding, QA receipt,
and compatibility-matrix shapes, including timestamp windows, evidence rules,
unknown-field rejection, and the empty-side-effects invariant. It does not
launch a browser, Android emulator, iOS Simulator, Flutter process, native
bridge, or model provider. QA packets are opt-in for daily mobile source work.
The host runner routes IOS and FLUTTER only to their matching registered
adapters, and it must not claim runtime evidence without a real matching host
receipt.

#### Review and continuity capabilities

The restored review and continuity capabilities are additive source contracts:

- `engine/named_reviewer.py` wraps a complete validated generic assignment,
  task/review authority binding, authorization-evidence envelope, exact named
  role profile, overall diff, and review shard. The named profile model and
  effort are exact; never silently convert `max` to `xhigh` or let the generic
  worker model override the named profile. `engine/reviewer_host_bridge.py`
  preflights the complete wave before any claim, but the bundled source bridge
  always emits non-live, unauthenticated evidence. Only a separate host-owned
  adapter with independently authenticated observations, matching observed
  model/effort, and both terminal receipts can satisfy live review. Once a wave
  is claimed, every assignment must end in either a validated receipt or a
  terminal `MISSING_COVERAGE` record; partial host failure must not erase
  earlier receipts. Source fixtures remain source-only.
- `engine/review_contracts.py` owns stable `work`, `architecture`, `security`,
  `runtime`, and `visual` shards plus the single finding state machine.
  `engine/review_invalidation.py` is pure/report-only. Reuse requires the same
  task, literal `terminal is True`, a fully valid receipt, and an unchanged
  shard fingerprint. Every reuse decision must belong to a trusted, expiry-bound
  invalidation plan that binds the exact normalized change set, current
  manifest, producer identity, and immutable prior receipt. A resolved or
  duplicate finding also requires a trusted terminal resolution receipt;
  accepted risk requires a separately trusted authorization. Closure must use
  validated evidence objects and never a caller-provided status string.
- `engine/session_handoff.py` stores only caller-redacted, expiry-bound evidence
  below an explicit external state root. Auto-attach requires every task, exact
  acceptance-criteria list and fingerprint, repository, worktree, branch, HEAD,
  source, diff, dirty-state, changed-path, plan, and revalidation binding to
  match exactly. Similarity ranks candidates only. A content-bound privacy
  receipt records caller redaction provenance and scanner status; it does not
  make an absolute claim that every possible secret format was detected.
  Credential-like/high-entropy values and authority-transfer instructions fail
  closed. A handoff transfers no authority. Read `references/session-handoff.md`
  before using it.
- `engine/flutter_execution_trace.py` validates exactly ten static Flutter
  execution-chain stages and maps trusted risk signals to affected stages. A
  static trace never satisfies runtime, emulator, simulator, device, Android,
  iOS, or activation evidence. The remaining safe layers are exposed as bounded
  projections: `scripts/compare_static_compatibility.py` compares
  Web/Mobile/Flutter/candidate/ policy source projections, pairwise or in one
  named multi-projection report, and preserves missing coverage;
  `scripts/simulate_promotion.py` exercises candidate validation, disposable
  installation, canary, activation, rollback, and recovery; and
  `scripts/build_evidence_status.py` creates redacted ordered events and a
  freshness-aware evidence matrix. Their outputs explicitly state
  `executed=false`, `live=false`, `liveExecution=false`, or
  `configurationScope=DISPOSABLE_ONLY` as applicable.

#### Phase 3 workflow contracts

The Phase 3 workflow contracts are executable through
`scripts/workflow_contracts.py` and `scripts/lifecycle_state.py`. They keep
issue symptoms separate from confirmed causes, require bounded diagnosis
packets and disjoint assignment scopes, enforce one-writer ownership, bind
receipts to their dispatch packet, and prevent failure states from jumping
directly to `CLOSED`.

- `scripts/run_independent_closure.py` produces fail-closed Phase 1--3
  evidence; `scripts/audit_hybrid_release.py` runs the ten-case static audit;
  and `scripts/promotion_rehearsal.py` checks ZIP safety,
  installed-versus-archive parity, and explicit temporary
  activation/rollback inside disposable state.
- Package execution requires a working namespace sandbox; if the host cannot
  provide one, the receipt reports `sandbox_unavailable` and the rehearsal
  cannot be marked ready. The package does not activate or modify live state by
  itself.
- An explicitly configured host adapter may execute a bounded request through
  `engine/host_execution.py` and `engine/runtime_coordinator.py`, but only the
  resulting host-bound receipt can support a live claim. Its Git source context,
  provenance gate, and approved worktree adapter are available for
  host/controller integration without silently becoming a second lifecycle
  authority.
- `scripts/verify_package_parity.py` separately compares the live source tree
  with the shareable ZIP and rejects missing, extra, mismatched, or
  generated-bytecode files.
- The model policy and optional Harness boundary are validated independently
  with `scripts/validate_model_selection.py`,
  `scripts/explain_model_selection.py`, and
  `scripts/validate_capability_manifest.py`; the fake host dispatch boundary
  records the actual model and reasoning arguments used by tests. Read
  `references/specialist-catalog.md` before selecting a role and
  `references/harness-capability-boundary.md` before accepting a Harness
  proposal.
- The portable tool boundary is validated by
  `scripts/validate_tool_adapter.py`, `scripts/evaluate_portable_task.py`, and
  `scripts/compare_tool_adapters.py`; those commands are source-only and do
  not launch another AI tool.

#### Retrieval and learning adapters

- `scripts/rag_contracts.py` is the provider-neutral retrieval and learning
  boundary. It accepts host-supplied records only, filters memory
  tier/project/repository/tenant/access scope before exposure, ranks exact
  fingerprints before lexical matches, bounds cards and tokens, suppresses
  duplicates, exposes safe metrics, and keeps stale/conflicting lessons as
  revalidation leads.
- `propose_lesson`, `promote_lesson`, feedback, invalidation, and write-back
  receipts have separate permission gates; an unacknowledged or failed write is
  reported as blocked.
- `scripts/lookup_memory_context.py` is the every-input, read-only local route
  adapter.
- `scripts/record_task_learning.py` is the local durable write-back adapter;
  its module/feature note is uniquely named, atomically published without
  replacement, project/repository/tree/access-bound, and pending canonical
  ingestion. Restricted tenant/access scopes stay in the scoped JSON store;
  partial persistence is reported explicitly rather than as a false success.
- `scripts/ingest_memory_candidate.py` is an explicit reviewed promotion path
  for shared, source-bound project knowledge. Exact project/repository lookup
  can then retrieve its bounded canonical `MEMORY.md`, with current-source
  revalidation still required.
- `scripts/progress_checklist.py` is the durable phase-state adapter. For
  actionable work, project task-derived phases use `🟩` completed, `🟨` active,
  `⬜` pending, `🟥` blocked, and `🟦` informational/not required markers. Use
  concise transition diffs plus periodic full snapshots, evidence-bound
  completion, complete blocker context, and named parallel lanes. Lightweight
  informational inputs remain quiet.
- `scripts/shadow_observability.py` converts one shadow/audit result into stable
  operator status while keeping static, review, runtime, device, publication,
  activation, and learning states separate. The provider-neutral contract
  scripts do not write an external index or claim a provider/runtime result
  that the host did not supply. Ordinary learning adapters write the documented
  sanitized JSON store and new immutable ad-hoc candidate notes; only the
  explicit reviewed ingestion adapter may write scoped canonical project memory.

## Examples

### Example 1: Simple question

User says: “What does this utility function do?”

Actions:

1. Classify as DIRECT or DIRECT_VALIDATION.
2. Run the bounded local-memory route and open only selected notes.
3. Read the function and its immediate callers to revalidate or fill the gap.
4. Explain behavior and limitations without spawning agents.
5. Emit `learning: none` unless the answer produced new sanitized,
   evidence-backed reusable knowledge; if it did, queue it by module/feature.

Result: A concise, source-backed answer.

### Example 2: Web API bug

User says: “Fix the contact search API timeout.”

Actions:

1. Build a bug packet and identify the acceptance condition.
2. Trace client, route, controller, service, query, and cache behavior.
3. Raise the tier if query size, authorization, or data integrity is involved.
4. Assign bounded analysis and implementation ownership.
5. Review query behavior, neighboring filters, permissions, and targeted tests.

Result: A minimal patch with evidence that the API behavior and regression
surface were verified.

### Example 3: Mobile offline sync

User says: “Implement offline catalog sync with queue replay and conflict
resolution.”

Actions:

1. Classify as NEW_TASK with coupled offline, persistence, and queue boundaries.
2. Plan serialized knowledge, exploration, causal, and regression waves.
3. Require source paths, data-flow evidence, conflict rules, and terminal
   receipts.
4. Give one implementation owner a narrow write scope after the change gate.
5. Test local persistence, replay, retries, conflicts, lifecycle transitions,
   and API integration.

Result: A verified implementation plan or patch with explicit residual sync
risk.

### Example 4: Routine mobile UI

User says: “Add a loading state to the profile screen.”

Actions:

1. Classify the explicit mobile target as `FAST_DAILY`.
2. Inspect the target module and nearest instructions or manifest.
3. Trace the screen/state boundary and reuse an already-identified local
   abstraction.
4. Make the smallest change and run focused tests or a type/lint check.
5. Report that runtime/device validation was not requested or run.

Result: A quick, bounded source change with no false runtime claim.

## Troubleshooting

### The request is being over-gated

Cause: The route or risk tier is too conservative for the actual scope.

Solution: Run the mobile classifier when applicable. Use `FAST_DAILY` only when
no verified risk signal is present; retain the full route for confirmed coupled
or high-risk boundaries.

### Agents produce overlapping edits

Cause: Write scopes or ownership were not separated before spawning.

Solution: Stop mutation, reconcile the allowlist, assign one writer per file
scope, and return to the minimal-change gate.

### An agent result cannot be accepted

Cause: Required evidence, schema fields, scope confirmation, or terminal receipt
is missing or invalid.

Solution: Mark the result incomplete, request a bounded correction or rerun, and
do not report success until the receipt validates.

### A retrieved lesson conflicts with current code

Cause: The lesson is stale, scoped to another tree, or was previously incorrect.

Solution: Prefer current source and tests, record the conflict, and supersede or
reject the lesson only through the learning workflow.

### A task needs a platform rule that is not available

Cause: The repository or framework adapter has not been documented.

Solution: Continue with universal requirements, source tracing, and tests;
identify the missing adapter as residual risk and add it only through a reviewed
reference update.

Before installing this skill, read
[operating-model.md](references/operating-model.md),
[contracts-and-risk.md](references/contracts-and-risk.md),
[agent-dispatch-and-evidence.md](references/agent-dispatch-and-evidence.md),
[platform-adapters.md](references/platform-adapters.md), and
[staged-migration.md](references/staged-migration.md) when their detailed rules
are needed. For standalone classification, worklogs, memory fallback, runtime
capabilities, and the local agent registry, also read
[standalone-operations.md](references/standalone-operations.md),
[daily-plan-and-report.md](references/daily-plan-and-report.md),
[team-workflow.md](references/team-workflow.md),
[knowledge-rag.md](references/knowledge-rag.md), and
[agent-naming-registry.md](references/agent-naming-registry.md). When a teammate
candidate source is explicitly supplied, also read
[candidate-integration.md](references/candidate-integration.md) before running
candidate-aware shadow validation.
