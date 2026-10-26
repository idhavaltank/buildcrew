# AE specialist catalog

This catalog defines eight AE roles as responsibility contracts. A role is a
bounded output and evidence shape; it is a contract, not a real agent definition or a prompt,
or an instruction to spawn every listed role. AE remains the only authority
for intake, routing, model selection, scope, writer leases, evidence
reconciliation, worklogs, learning, activation, rollback, and task closure.

## Selection rules

- Select the smallest useful set of roles for the task. A direct question or a
  narrow read-only check normally needs no specialist assignment.
- Normally use two or three active lanes. Four is the hard default ceiling,
  reduced to the lower host or task limit and used only when independent scope
  and capacity justify it. Role presence never justifies exceeding that cap.
- All roles and optional capabilities use one canonical host/workspace
  registry. Duplicate role IDs, duplicate registries, disabled entries,
  stale fingerprints, and provider-owned lifecycle state are rejected before
  selection.
- A read-only role never receives a writer lease, repair authority, activation
  authority, reviewer closure authority, or a lane for mutation.
- `writer` is the only mutation role. Exactly one writer owns each mutation
  scope; overlapping scopes are serialized or split before work begins.
- Every assignment is task-, project-, repository-, and source/diff-bound. A
  changed acceptance condition or fingerprint invalidates the assignment.
- No role may dispatch another worker, change model policy, close a lifecycle,
  create a state root, use credentials, write a worklog, update memory, or
  activate a skill. Those actions remain AE/host-owned.

## Contract fields

Each selected role must return a terminal evidence record with the following
shape. Missing fields are missing coverage, not a pass.

| Field | Contract |
| --- | --- |
| Role and task | Canonical role ID, task ID, packet/plan/wave/attempt binding |
| Scope | Repository, lane or read-only boundary, files/symbols, and exclusions |
| Fingerprint | Source fingerprint for exploration or diff fingerprint for review/write |
| Findings | Facts, hypotheses, changes, or checks appropriate to the role |
| Commands and results | Exact focused commands and observable results; no raw secrets or transcripts |
| Confidence | `high`, `medium`, or `low`, with the reason for uncertainty |
| Blockers | Missing authority, capability, evidence, or an unresolved decision |
| Residual risk | Unverified behavior, affected flows, and the next safe check |
| Terminal status | `completed`, `in_progress`, `timed_out`, `interrupted`, `crashed`, or `shutdown` |

## The eight AE role contracts

### `knowledge`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `question`, `bug`, `feature`, `review`, `validation` |
| Default read/write authority | `READ_ONLY`; scoped retrieval only |
| Maximum scope | One task, project, repository, tenant, and current source/tree; expand only with an explicit requirement |
| Non-goals | No routing, model choice, memory promotion/invalidation, source edits, or authority from a retrieved card |
| Evidence format | Scoped evidence cards with record ID, source path/symbol, freshness, confidence, access scope, and a revalidation result |
| Validation expectations | Recheck every match against current source, tests, runtime evidence, or explicit user direction; label stale/conflicting knowledge as a lead only |
| Stop conditions | Provider unavailable, access boundary missing, stale/conflicting evidence unresolved, or the bounded query is answered |

`knowledge` supplies context, not instructions. If no provider is available it
returns an explicit unavailable result and AE continues source-first without
fabricating a card or receipt.

### `explorer`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `bug`, `feature`, `review`, `validation`, `root-cause` |
| Default read/write authority | `READ_ONLY`; source and test inspection |
| Maximum scope | The named repository and the smallest executing path from entry point through relevant dependencies |
| Non-goals | No patch, refactor, routing decision, worker dispatch, or claim that runtime behavior was observed without running it |
| Evidence format | Files, symbols, callers, transformations, side effects, focused commands, and known gaps |
| Validation expectations | Trace the actual path; distinguish source evidence from assumptions and identify existing tests and uncovered branches |
| Stop conditions | Entry path is traced, a protected boundary is reached, scope would expand, or required runtime access is unavailable |

### `root-cause`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `bug`, `incident`, `diagnosis`, `regression` |
| Default read/write authority | `READ_ONLY`; diagnosis evidence only |
| Maximum scope | One symptom, bounded reproduction, and one causal chain in the approved repository/task scope |
| Non-goals | No implementation, workaround, retry of an unproven hypothesis, closure, or conversion of a symptom into a confirmed cause |
| Evidence format | Symptom, reproduction, hypotheses, causal trace, disconfirming checks, confirmed cause or bounded hypothesis, and regression risk |
| Validation expectations | Require source/test/runtime evidence for a confirmed cause; keep hypotheses explicitly labeled when proof is incomplete |
| Stop conditions | Reproduction cannot be established, evidence contradicts the hypothesis, causal scope expands, or the cause is confirmed with a focused proof |

### `regression`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `bug`, `feature`, `refactor`, `review`, `validation` |
| Default read/write authority | `READ_ONLY`; affected-flow and coverage analysis |
| Maximum scope | Changed files/symbols, direct callers, neighboring flows, and the focused test boundary |
| Non-goals | No source repair, broad test-suite expansion, model escalation, or release/closure decision |
| Evidence format | Affected flows, edge cases, permission/data boundaries, existing coverage, missing coverage, and prioritized checks |
| Validation expectations | Compare current callers and tests with the proposed behavior; include negative and failure-path coverage where risk warrants it |
| Stop conditions | Impact boundary is mapped, a new dependency appears, scope crosses repositories without authorization, or required evidence is unavailable |

### `reviewer`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `review`, `security-review`, `architecture-review`, `validation` |
| Default read/write authority | `READ_ONLY`; immutable diff or contract review |
| Maximum scope | One task-bound source/diff fingerprint and its declared file/symbol allowlist |
| Non-goals | No repair, writer lease, approval of a missing receipt, lifecycle closure, activation, or replacement of current source evidence |
| Evidence format | Severity-ordered findings with file/symbol references, consequence, confidence, and a focused disposition |
| Validation expectations | Check authority boundaries, minimality, contracts, security, regression surfaces, and evidence quality against current source |
| Stop conditions | Diff fingerprint changes, review scope is incomplete, a critical safety conflict is found, or all declared review dimensions are covered |

### `testing`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `test`, `bug`, `feature`, `regression`, `validation` |
| Default read/write authority | `READ_ONLY` repository access; may execute only approved focused checks and collect bounded evidence |
| Maximum scope | Named test files/commands and their task-bound runtime or temporary outputs |
| Non-goals | No source edits, test deletion, dependency installation, service mutation, claim of runtime/device proof from a unit test, or closure |
| Evidence format | Command, exit status, meaningful assertions, relevant output summary, skipped/unavailable checks, and residual risk |
| Validation expectations | Use tests that fail when the behavior is broken; cover the requested behavior, negative cases, and real integration boundaries where applicable |
| Stop conditions | Command is outside the allowlist, environment is unavailable, output is non-terminal, or focused coverage is complete |

### `verification`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `validation`, `release-review`, `completion-review`, `audit` |
| Default read/write authority | `READ_ONLY`; evidence reconciliation |
| Maximum scope | One task and its requirement-to-evidence matrix, receipts, fingerprints, and declared verification surfaces |
| Non-goals | No new implementation, evidence fabrication, agent closure, activation, rollback, or promotion of an unavailable capability |
| Evidence format | Requirement, artifact/behavior, check, result (`pass`, `not_applicable`, `not_run`, `fail`, or `blocked`), and residual risk |
| Validation expectations | Require current source/test/runtime evidence where claimed; keep static, review, runtime, device, publication, activation, and learning states separate |
| Stop conditions | Any required row is missing/unverified, a receipt is stale or unbound, a blocker remains, or every required row has evidence |

### `writer`

| Contract field | Boundary |
| --- | --- |
| Allowed task types | `implementation`, `documentation`, `test`, `repair`, `migration` when explicitly authorized |
| Default read/write authority | `REPOSITORY_WRITE` only after AE grants one task-bound writer lease |
| Maximum scope | One disjoint, explicitly allowlisted file/symbol set; one writer per overlapping scope and no scope expansion |
| Non-goals | No routing, model policy, worker dispatch, state-root creation, credential handling, worklog/memory writes, activation, closure, or unrelated cleanup |
| Evidence format | Before/after scope, required repository-first reuse decision (`REUSE`, `EXTEND`, `NEW`, or `DOCUMENTED_EXCEPTION`), changed files, rationale, focused tests/checks, diff fingerprint, blockers, and residual risk |
| Validation expectations | Complete and record the bounded reuse assessment before every code/source mutation, implement the smallest coherent change, preserve contracts, and hand the immutable diff to `reviewer`, `regression`, `testing`, and `verification` as needed |
| Stop conditions | Lease expires, a file is outside the allowlist, another writer overlaps, acceptance changes, a safety conflict appears, or the focused change is complete |

## Reuse and over-engineering gate

Before `writer` adds a new abstraction, dependency, process, service, queue,
state root, or generalized framework, the task record must identify:

1. the exact requirement or proven constraint that needs it;
2. why the existing direct path cannot satisfy that requirement safely; and
3. why reuse, deletion, consolidation, or a local correction is insufficient.

Without all three, stop and use the direct path. A specialist contract never
authorizes future-proofing, a second controller, or an always-on background
workflow.

## Completion handoff

Role evidence is input to AE review. It does not close a task. AE reconciles
the evidence, rejects missing/stale/unbound receipts, runs the required focused
checks, records unavailable host capabilities explicitly, and alone decides
whether the task can proceed or close.
