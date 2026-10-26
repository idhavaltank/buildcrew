# Agent dispatch and evidence

## Functional roles

Use only the roles needed by the task. Common roles include:

- Knowledge researcher: scoped prior lessons and source citations.
- Explorer: entry points, callers, dependencies, and execution path.
- Root-cause finder: causal trace and direct evidence.
- Regression analyst: affected flows and coverage boundary.
- Specialist: approved implementation within a narrow write scope.
- Code reviewer: correctness, minimality, architecture, security, and needless edits.

Named reviewers are supplemental to the generic assignment contract. Preserve
the complete generic task/project/repository/tenant/packet/plan/wave/attempt/
source/policy/scope/host/expiry binding, then add the named-role profile, exact
model/effort, overall diff, and shard. Preflight a complete named wave before
claiming any identity. A source fixture, recording adapter, copied expected
model, or `live=false` receipt cannot satisfy a required live review.

Review coverage is split into stable `work`, `architecture`, `security`,
`runtime`, and `visual` shards. Reuse requires the same task, a fully valid
receipt with `terminal is True`, and an unchanged shard fingerprint. A changed
overall diff alone does not invalidate an unaffected shard; a separate reuse
decision binds the immutable prior receipt to the current diff. Invalidation is
pure/report-only and never grants repair authority.
- Testing agent: focused commands, real behavior, and failure interpretation.
- Verification agent: requirement-to-evidence matrix and residual risk.

These are responsibility contracts, not decorative personas. Do not duplicate
the same scan across multiple agents.

## Wave rules

Independent knowledge and exploration may run in parallel when their scopes and
outputs are independent. Root-cause analysis normally follows enough evidence
from exploration. Regression and coupled data-flow analysis must be serialized
when persistence, queues, sync, migrations, dependency injection, or device
lifecycle can make conclusions interdependent.

The lead continues non-overlapping local work while agents run and synchronizes
only when a result is required for the next critical-path decision.

Every dispatch declares a bounded wave before launch:

- `waveId`, purpose, dependency predecessor, and terminal deadline are recorded
  in the task packet.
- `maxParallel` is the lower of the host limit and task limit. Normally use
  two or three active lanes; four is the hard default ceiling and is used only
  when independent scope and measured host capacity justify it. A wave may not
  exceed this bound or launch work after its deadline.
- Each slot names one canonical role, one packet, one source or diff fingerprint,
  and one handoff destination. A slot counts only after the runtime confirms a
  real start; queued or failed launches do not create coverage.
  - The model boundary is complexity-driven: lightweight workers use
  `model-fast` at `low`, small workers use FastModel at `medium`, normal workers
  use `model-balanced` at `high`, and coupled/high-risk workers use
  `model-advanced` at `max`. Normal coordination remains BalancedModel/high; coupled,
  high-risk, and final-audit coordination remains AdvancedModel/max. Final-audit evidence
  workers may use FastModel/max. Only the fingerprinted fallback transitions in the
  policy may run; they are `FALLBACK_EVIDENCED` with exactly one matching
  `fallback_policy:<ruleId>` marker. High-risk fallback is
  `OVERRIDE_EVIDENCED`; final-audit fallback is prohibited.
- Read-only workers may share one immutable source fingerprint. Reviewers may
  share one immutable diff fingerprint. A changed fingerprint invalidates the
  wave and requires a new packet and attempt.
- There is exactly one writer owner for a task and one writer owner for each
  write scope. Write scopes must be disjoint before parallel launch; otherwise
  writers are serialized or the lead splits and rebinds the scopes explicitly.
  No reviewer, analyst, or retry may write the writer's scope.

If a role cannot be assigned a disjoint scope, useful output, and a bounded stop
condition, keep the work with the lead or serialize it.

## Spawn contract

Before every real spawn:

1. Reserve an unused canonical name from the bundled
   `agent-naming-registry.md`.
2. Put “Agent Role: NAME” as the first private prompt line.
3. State objective, request context, dependencies, current status, risk,
   blockers, exact file boundary, write permission, expected output, and
   completion criteria.
4. State that the agent must not revert others' edits.
5. Resolve the task-class policy: FastModel/low for lightweight work, FastModel/medium
   for small work, BalancedModel/high for normal work, and AdvancedModel/max for coupled or
   high-risk work. Record explicit unavailable or host-inherited state when the
   catalog lacks the requested model; only a declared fallback may change it.

After a successful spawn, show only the canonical name, selected model, and
reasoning effort. Keep runtime IDs, automatic nicknames, and raw prompts
internal. On synchronization, show canonical names only.

## Bounded packet

For normal or coupled work, attach this minimum packet to the private prompt:

```text
Objective: one decision or deliverable
Allowed scope: files, symbols, repository, and read/write authority
Excluded scope: unrelated modules, generated files, or external systems
Budget: bounded commands, search depth, and wait/checkpoint window
Checkpoint: phase, last command/tool, inspected scope, partial findings, blockers
Required receipt: scope, evidence, commands/results, confidence, blockers, risk
Stop conditions: what ends the assignment or requires escalation
```

The dispatch record additionally binds the selection envelope and each assignment
to the same trusted `policyFingerprint`, `taskId`, `packetVersion`, `waveId`,
`attempt`, `sourceFingerprint` or `diffFingerprint`, `writerOwner` (or
`writer=none`), `maxParallel`, deadline, validation time, and the host capability
result. The host boundary must receive the expected task/policy binding from the
AE controller or host context; it must not derive trust from the selection being
dispatched. It preflights every assignment, rejects duplicate or previously
consumed identities and invalidated envelopes, atomically claims all assignment
IDs before recording calls, and records calls only after the complete selection
passes. The production host must provide a durable claim store; the V2 receipt
adapter provides an in-memory implementation for deterministic tests. A
checkpoint is evidence of progress only; it is never a completion receipt.

Prefer several narrow packets over one broad repository scan. Separate static
execution-path tracing from persistence/schema work, implementation, and
device/runtime verification when they can be reasoned about independently.

## Progress and termination

The lead should continue non-overlapping work while a worker runs. When a
worker has not returned a terminal receipt by the end of a wait window:

1. Request one bounded checkpoint or final receipt.
2. Record the worker's reported state, last activity, and any partial evidence.
3. Use one status/close operation if the runtime supports it; do not repeat
   blind waits.
4. Classify the outcome as `completed`, `in_progress`, `timed_out`,
   `interrupted`, `crashed`, or `shutdown` using runtime evidence only.
5. Treat every non-completed outcome as `MISSING_COVERAGE` for the assigned
   scope. Retry at most once only after the original attempt is terminal, with a
   narrower packet, a fresh attempt binding, and a fresh canonical name. Never
   retry a still-running worker or blindly replay the same packet.
6. If the bounded retry also fails, transition the task to `BLOCKED` (or
   `INVALIDATED` when its fingerprint changed) and stop dispatch for that scope.

If the runtime says `running` after a close request, retain `in_progress` until
a later runtime event proves a terminal transition. Never claim that a close
request itself produced a terminal receipt.

Minimum checkpoint fields:

- current phase;
- last command or tool;
- files or symbols inspected;
- partial findings and confidence;
- blockers, residual risk, and next action.

The lead records the checkpoint timestamp and whether it was `checkpoint-only`,
`terminal`, or `unavailable`. A missing checkpoint is an observability gap, not
evidence that the worker made no progress.

## Failure states and operator handling

Use these states consistently in dispatch, status output, and closure reports:

| State | Meaning | Closure effect | Next action |
| --- | --- | --- | --- |
| `BLOCKED` | Required authority, capability, decision, or evidence is unavailable | Cannot close | Resolve the blocker or report the task blocked |
| `MISSING_COVERAGE` | Required role, test, runtime, or receipt did not complete | Cannot close | Reassign a bounded scope or escalate |
| `INVALIDATED` | Packet, acceptance, plan, source, or diff fingerprint changed | Old receipts unusable | Create a new plan/attempt binding |
| `INTERRUPTED` | Work stopped before a terminal receipt | Cannot pass | Recover from the last checkpoint or rerun safely |
| `TIMED_OUT` | Deadline expired without valid terminal evidence | Cannot pass | Inspect/close once, then bounded recovery |
| `CRASHED` | Worker or command failed without valid completion evidence | Cannot pass | Preserve failure evidence and diagnose |
| `SHUTDOWN` | Host/runtime ended the assignment | Cannot pass | Confirm host state before any retry |

All failure states remain visible in operator status and the worklog. No failure
state may be converted to `completed` by interpretation, and no failure state
may transition directly to `CLOSED`.

## Evidence contract

| Role | Minimum evidence |
| --- | --- |
| Knowledge researcher | Scoped query, source citations, freshness or conflict note |
| Explorer | Entry point, callers, execution path, relevant files and symbols |
| Root-cause finder | Causal trace, direct evidence, confidence, unresolved questions |
| Regression analyst | Affected flows, edge cases, existing coverage, gaps |
| Specialist | Changed paths, rationale, focused tests, residual risk |
| Reviewer | Findings ordered by severity, file references, unnecessary edits |
| Testing agent | Exact commands, results, failed checks, residual risk |
| Verification agent | Requirement, observable artifact, evidence, pass or limitation |

Every result must be terminal and within scope. A missing or malformed receipt
keeps that stage incomplete. A terminal receipt is valid only when its task,
packet, wave/attempt, host, writer binding, source/diff fingerprint, role,
terminal status, timestamps, commands/results, and required coverage all match
the dispatch record.

At minimum, the receipt must distinguish:

```text
receiptVersion, receiptId, taskId, packetVersion, waveId, attempt,
role, host, sourceFingerprint|diffFingerprint, lane, writerOwner,
startedAt, finishedAt, status, terminal, coverage, findings, commands,
artifacts, blockers, confidence
```

`status=completed` is accepted only for schema-valid, terminal, task-bound,
fingerprint-bound, coverage-complete output. `failed`, `timed_out`,
`interrupted`, `crashed`, `shutdown`, `in_progress`, or `missing` are evidence
of non-completion and remain visible as such. Command output may be recorded,
but an exit code alone does not prove the requested requirement.

If the host does not expose a real spawn tool, perform the assignment as the
lead agent and report the capability limitation. Never invent a worker, model,
receipt, or delegated result.
