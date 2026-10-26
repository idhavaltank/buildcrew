# Standalone operations

This reference makes `buildcrew` usable without
installing another workflow skill. It defines its own intake, routing,
worklog, memory, delegation, and fallback behavior.

## First-pass classification

For every input, classify the request before deciding whether repository state
or agents are needed:

- Informational: answer directly with targeted evidence.
- Conversational or clarification: preserve context and resolve the next
  decision.
- Review: inspect supplied source, diff, plan, or architecture.
- Bug: reproduce or trace current behavior, prove the cause, then fix if asked.
- Feature: define the extension point, acceptance criteria, risks, and plan.
- Task: perform the smallest scoped local operation.
- Status or continuation: recover the matching task context and verify state.

Normalize obvious typos mentally, identify the user's intent, constraints, and
deliverable, and route any relevant specialist skills using the available
skill metadata and repository `AGENTS.md` when present. If a host provides a
classification, revalidate it; this skill can classify without that packet.
Specialist skills are optional unless applicable host or repository policy
requires one. When absent, continue with this skill's bundled workflow rather
than failing solely because a companion skill is missing.

Simple questions and narrow text changes should remain lightweight. Do not
create lifecycle state or spawn agents when they cannot change the answer.

For actionable work, the operator status must distinguish `RECEIVED`,
`IN_PROGRESS`, `BLOCKED`, `MISSING_COVERAGE`, `INVALIDATED`, `INTERRUPTED`,
`TIMED_OUT`, `CRASHED`, `UNVERIFIED`, and `CLOSED`. `CLOSED` requires the
evidence and receipt checks described below; it is not a synonym for “command
finished.”

## Standalone task packet

For actionable work, capture a compact packet containing:

```text
session_id, title, summary, selected_skills, request, request_type,
project, repository, domain, environment,
current_behavior, expected_behavior, reproduction, acceptance_criteria,
constraints, risks, dependencies, missing_decisions, learning_state
```

Classify missing information as blocking, discoverable, or non-blocking. Ask
only about blocking information. Discover source, tests, configuration, and
runtime facts locally before asking the user for them.

Keep Complete, In progress, Todo, and New/off-plan lists as a compact
projection of this same canonical packet. Keep them internal during execution
rather than creating a second checklist or worklog owner. At substantive work
start, reconcile today's plan with current evidence using
[`daily-plan-and-report.md`](daily-plan-and-report.md). That reference also
provides a self-contained daily plan and EOD report path when requested.

## Worklog ownership

The bundled `scripts/update_daily_log.py` utility owns date-wise worklog
updates. Use it after a substantive task is completed, blocked, or explicitly
paused. Skip it for trivial answers unless the exchange changes a tracked plan,
decision, blocker, or next action.

Reuse the packet's stable session ID and pass the full current progress state
to each update. The script updates that session's section rather than creating
a competing lifecycle record. Check its reported path and result before
claiming the worklog was saved.

The default worklog root and fallback follow
[shared storage configuration](storage-configuration.md), including
`BUILDCREW_HOME` and explicit component settings. A teammate may pass an
explicit `--root` for their own workspace. Set
`BUILDCREW_WORKLOG_ALLOWED_ROOTS` to an OS-separated allowlist when
the host requires one. The script rejects roots overlapping the installed skill
or paths listed in `BUILDCREW_PROTECTED_ROOTS`. Legacy host restrictions
remain enforced when configured. File locking is advisory when the
host provides the standard locking module; atomic writes still protect each
individual update.

Example:

```text
python3 scripts/update_daily_log.py \
  --title "Short task title" \
  --session-id "stable-session-id" \
  --summary "Outcome summary" \
  --complete "Verified requested behavior"
```

Never put secrets, tokens, raw transcripts, customer data, unredacted logs, or
unverified agent opinions in a worklog.

Record the following status dimensions independently in the worklog or its
structured equivalent: implementation, static checks, review, runtime/device
proof, candidate projection, shadow comparison, activation, and learning. Each
dimension is `PASS`, `FAIL`, `BLOCKED`, `MISSING_COVERAGE`, `NOT_REQUIRED`,
`UNAVAILABLE`, or `UNVERIFIED`. A worklog write failure is itself an operational
blocker and must remain visible; it must not be silently retried or omitted.

## Memory ownership

For every input, search the approved local memory root first. Use
`scripts/lookup_memory_context.py` when a sanitized intake packet is available;
otherwise follow the same bounded root-index → route-map/module-registry →
nearest feature-note order manually. Then use an optional knowledge provider
when the host exposes one.

1. Search the configured local memory root for exact signals.
2. Revalidate any match against current source, tests, runtime state, or user
   confirmation.
3. Continue source-first when no relevant memory exists.
4. Save only sanitized, scoped, evidence-backed reusable lessons through the
   allowed local fallback.
5. Queue module/feature learning as a unique immutable ad-hoc note; never edit
   canonical indexes, route maps, registries, or feature files directly while
   another writer may be active. Reject symlinked memory boundaries, require
   exact repository/application/module routing, keep tenant/restricted records
   in the scoped JSON store, and report post-write failures as `partial`.

Memory is a hypothesis, never authority. Current source, tests, runtime
evidence, and explicit user corrections win over retrieved lessons.

## Standalone agent dispatch

The local `agent-naming-registry.md` is the source of truth for
canonical names. Before spawning a real agent:

1. Select the smallest useful topology.
2. Reserve an unused canonical name matching the responsibility.
3. Put `Agent Role: NAME` as the first line of the private prompt.
4. State objective, context, risks, blockers, exact file boundary, write
   permission, evidence requirements, and completion criteria.
5. Resolve the task-class policy: FastModel/low for lightweight work, FastModel/medium
   for small work, BalancedModel/high for normal work, and AdvancedModel/max for coupled/high-risk
   work. If the host cannot expose the requested model, record unavailable or
   host-inherited state; only a fingerprinted, scope-limited fallback declared
   by policy may continue work. Never use an arbitrary nearest model or
   silently change a worker's model.
6. Confirm that the runtime actually started the agent.

Keep runtime IDs and automatic nicknames session-local. If no spawn tool is
available, perform the work as lead and report that multi-agent execution was
not available. Never claim a simulated worker result.

For an available spawn tool, dispatch in explicit bounded waves. The task packet
must contain `waveId`, `maxParallel` (lower of host/task limits; normally two
or three active lanes, with four as the hard default ceiling),
deadline, source/diff fingerprint, role slots, and one writer owner. Read-only
roles can overlap only on an immutable fingerprint. There must be one writer for
each write scope; overlapping writers are serialized or split before launch.

After a wait window, request at most one checkpoint and one status/close action.
Record phase, last command, inspected scope, partial findings, blockers, and next
action. If the runtime still says `running`, keep the role `IN_PROGRESS`; do not
fabricate termination. Missing, malformed, stale, wrong-task, wrong-fingerprint,
or nonterminal output is `MISSING_COVERAGE`. Retry only after a terminal outcome,
at most once with a narrower packet and fresh attempt/name; otherwise mark the
scope `BLOCKED`.

## Standalone learning closure

At completion, classify learning as saved, updated, candidate, needs review,
rejected, redacted, blocked, or none. A lesson is durable only when it is
sanitized, scoped, deduplicated, evidence-backed, and successfully written.
If writing fails, report `learning: blocked` rather than claiming persistence.

The dependency-free local fallback is `scripts/record_task_learning.py`. It
must be called for issue, bug, debug, investigation, complex, actionable, and
other required terminal work after the closure packet has been sanitized. It
retrieves related knowledge in the same project/repository, then updates,
expands, supersedes, or saves the lesson in
`<memory-root>/autonomous-engineering/learning-store.json` (or an explicit
`--store` override). The memory root follows
[shared storage configuration](storage-configuration.md). It also queues a no-clobber
module/feature candidate in
`<memory-root>/extensions/ad_hoc/notes/`; that queue has no canonical
effect until a separate ingester consolidates it. Only project-wide non-tenant
records enter that shared queue; its note preserves the exact scope binding
and stable retry identity. Simple questions and
informational requests always perform the bounded read, then return
`skipped_simple` without a write unless a new source-backed reusable lesson is
explicitly marked `reusableLearning=true`.

For the same required work, use `scripts/progress_checklist.py` to maintain
durable internal lifecycle state and a compact task-derived visible projection.
Meaningful transitions print concise diffs; full snapshots appear at start,
periodically, when blocked, during recovery, and at completion. Completion is
evidence-bound and remains blocked while a lifecycle phase, task phase, or
parallel lane is unfinished. Use `--quiet` only for an explicitly silent
request or internal test harness. See
`references/learning-and-progress.md` for the exact phase, blocker, recovery,
and lane commands.
