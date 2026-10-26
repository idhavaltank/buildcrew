# Team Workflow Reference

Load this reference when a task is non-trivial, ambiguous, cross-layer, or requires
delegation. Keep the core skill as the always-loaded policy; this file contains the
role/output detail.

## Contents

1. Intake and clarification
2. Team roles
3. Pre-implementation decision record
4. Minimal-change gate
5. Adaptive topology
6. Dispatch, receipt, and operator status
7. Completion checklist

## 1. Intake and Clarification

Create a compact task packet before repository exploration:

~~~yaml
request: normalized user input
request_type: bug | feature | task | requirement | review | question | other
project: identified project or unknown
repository: identified repository or unknown
domain: web | mobile | backend | data | auth | infrastructure | other
current_behavior: observed or unknown
expected_behavior: explicit or unknown
reproduction: steps, data, environment, or unknown
acceptance_criteria: observable outcomes
constraints: user, repository, safety, time, or compatibility constraints
missing_decisions: decision-blocking questions only
learning_state: none | candidate | blocked
dispatch:
  max_parallel: host/task bounded integer
  waves: ordered bounded wave records
  writer_owner: one owner or none
  source_or_diff_fingerprint: immutable binding
verification: requirements, checks, receipts, blockers, residual risk
~~~

Classify missing information:

- **Blocking:** cannot determine the behavior, scope, authority, safety, or verification
  path. Ask the user a consolidated set of targeted questions and pause.
- **Non-blocking:** a preference or detail that does not change the solution materially.
  Make a safe assumption, record it, and continue.
- **Discoverable:** can be established from source, tests, configuration, or the knowledge
  system. Investigate it; do not ask the user to provide it.

For issue reports, explicitly distinguish:

- functionality missing;
- functionality implemented but failing;
- expected behavior mistaken for a bug;
- environment/data/configuration failure;
- insufficient evidence.

Do not edit code while the packet contains unresolved blocking questions.

## 2. Team Roles

Roles are responsibility contracts, not a requirement to spawn one agent per row. The lead
selects the smallest topology that reduces risk.

| Role | Required output | Activation |
|---|---|---|
| Requirement Analyst | normalized request, current/expected behavior, acceptance criteria | Every actionable request |
| Clarification Gate | blocking questions or explicit “ready” decision | Only when packet is incomplete |
| Knowledge Analyst | bounded local-memory receipt first; optional provider matches, evidence cards, confidence, stale/conflict warnings second | Every input; keep lightweight inputs read-only and lead-owned unless delegation materially helps |
| Codebase Analyst | executing path, dependencies, relevant files, evidence, likely root cause; before code/source mutation, candidate abstractions/patterns, public boundaries, representative usages/tests, and reuse/negative-search evidence | Any non-trivial code/system task |
| Task Router | domain, risk tier, specialist scope, file boundaries | When delegation is useful |
| Specialist | minimal patch and focused tests within approved scope, with the required repository-first reuse decision | Implementation stage only |
| Code Reviewer | unnecessary edits, convention violations, readability findings, architecture/regression concerns, and unexplained duplicate/raw/direct-client bypasses | After implementation |
| Regression Analyst | affected flows, edge cases, existing coverage gaps | After implementation, before completion |
| Testing Agent | focused test commands, results, real-behavior checks | Verification stage |
| Verification Agent | requirement-to-evidence matrix and residual risk | Final stage |
| Learning Curator | sanitized candidate lesson with repository/module/feature route and lifecycle state | Every completed input, including explicit none |

The lead owns user questions, final judgment, scope changes, integration, and delivery.
A subagent must not claim to have completed another role.

Each real agent prompt must contain:

- objective and original request;
- context and dependencies;
- current status, risks, and blockers;
- exact file/path boundary and write permission;
- expected output and completion criteria;
- “do not revert others’ edits.”

Each result must return file references, findings, commands, confidence, blockers, and
conflicts. Runtime nicknames never replace canonical role names.

### Bounded dispatch and ownership

Before launching a wave, the lead records its purpose, predecessor, `max_parallel`,
deadline, packet version, fingerprint, role slots, and writer binding. Use the lower
of the host and task limits. Normally use two or three active lanes; four is the
hard default ceiling and requires independent scope plus host capacity.
Do not launch a slot unless the runtime confirms a real start. A launch failure is
runner/configuration failure, not coverage.

Read-only roles may run in parallel only against one immutable source fingerprint.
Review roles may run in parallel only against one immutable diff fingerprint. There
is exactly one writer owner for a task and for each write scope; overlapping write
requests are serialized or explicitly split before launch. Analysts, reviewers, and
retries have no write authority unless their packet explicitly grants a disjoint
scope. Preserve unrelated worktree changes.

Each role receives one bounded checkpoint window and a terminal deadline. If no
terminal receipt arrives, request one checkpoint, record the last activity and
partial evidence, and issue at most one status/close request. Do not blind-wait or
retry a running role. Nonterminal, malformed, stale, wrong-task, wrong-fingerprint,
or incomplete output is `MISSING_COVERAGE`; after a bounded recovery failure the
task is `BLOCKED` or `INVALIDATED`, never complete.

### Operator status contract

Status and closure output must expose these fields separately:

```text
taskId, route, complexity, riskTier, state, waveId, activeRoles,
writerOwner, source/diff fingerprint, evidence coverage, blockers,
lastCheckpoint, terminalReceipts, runtime/device status, worklog status,
learning status, shadow status, activation status, nextAction
```

Use `NOT_REQUIRED`, `NOT_STARTED`, `UNAVAILABLE`, `MISSING_COVERAGE`, and
`UNVERIFIED` explicitly. Do not collapse them into `PASS`, and do not claim an
agent, review, runtime check, worklog write, shadow comparison, or activation
receipt that the host did not produce.

## 3. Pre-Implementation Decision Record

Before any code edit, the lead must answer:

| Question | Required answer |
|---|---|
| Is the functionality already implemented? | yes / no / unknown with evidence |
| Is the report a bug, expected behavior, missing feature, or unclear? | classification plus evidence |
| What is the current behavior? | observable behavior and executing path |
| What is the expected behavior? | user-confirmed or contract/test-derived |
| Is reproduction/validation sufficient? | steps, data, environment, or blocking gap |
| What is the root cause? | proven cause, or explicit hypothesis for non-bug work |
| Which files/modules are involved? | narrow file and symbol list |
| Which reusable abstractions or established patterns were considered? | bounded candidates across the changed layer, public boundaries, representative usages/tests, decision, or negative-search evidence |
| What is the minimum change? | smallest coherent patch and test change |
| What could regress? | affected flows and edge cases |
| How will it be verified? | exact tests/checks/real behavior |

For a feature, replace “root cause” with the existing implementation gap, constraints,
and design decision. For a review or question, mark implementation fields not-applicable.

No code edit, generated artifact, migration, or configuration mutation may begin until the
record is complete or the lead has asked the required clarification questions and received
answers.

## 4. Minimal-Change Gate

Before implementation, record a file allowlist and a short change ledger:

~~~text
path/symbol → add/modify/delete → rationale → owner → verification
~~~

Rules:

- Prefer the fewest files and lines that solve the proven problem.
- Search for an existing helper, pattern, test, or abstraction before creating one.
- Before every actionable code/source mutation, inspect existing components, hooks, clients, services,
  contracts, repositories, validators, utilities, scripts, configuration, schemas, tests, exports,
  and representative usages or local patterns within the bounded target as applicable to the
  changed layer.
- Reject an unexplained duplicate abstraction, incorrect form binding, raw-control fallback, or
  direct API-client bypass when a suitable local abstraction exists; require a recorded exception
  or negative-search decision.
- Create a helper or abstraction only when duplication, testability, or a verified
  boundary makes it necessary; record the reason.
- Do not refactor unrelated code, rename for style, reorganize imports gratuitously, or
  “clean up while here.”
- Preserve current architecture and conventions unless evidence requires a change.
- Treat an expanded file list, new dependency, schema change, or architecture change as a
  new decision requiring impact review and user approval when materially different.
- If implementation disproves the diagnosis, stop, report the evidence, and return to the
  requirement/root-cause gate rather than patching around the symptom.

Review the final diff against the allowlist. An unexplained file or helper is a review
failure. Every changed hunk must map to an approved requirement or verification need;
otherwise reject it or return to the scope gate.

## 5. Adaptive Topology

Use the same workflow at every project size, but scale context and delegation:

- **Small:** lead performs intake, knowledge lookup, analysis, minimal implementation, and
  focused verification. Spawn only when an independent review materially helps.
- **Medium:** lead plus one analyst or reviewer; split code analysis and test/regression
  work only when scopes are independent.
- **Large:** lead, requirement/knowledge analysis, scoped codebase analysts, domain
  specialist, reviewer, regression/test, and verification roles. Partition by repository,
  service, or evidence path; never give every agent the entire organization context.

Do not spawn every listed role by default. A role is activated only when its output is
needed for the task’s risk or dependency structure.

## 6. Completion Checklist

For actionable work, maintain the detailed internal checklist below and project
it through a compact visible plan derived from the actual task. Start the
projection after intake and update it whenever a material item changes:

```bash
python3 -B scripts/progress_checklist.py start --task-id TASK_ID \
  --display-phase 'inspect=Inspect the current implementation' \
  --display-phase 'change=Implement the bounded change' \
  --display-phase 'verify=Verify the result' --color
python3 -B scripts/progress_checklist.py update --task-id TASK_ID \
  --phase context --status PASS --detail "Source inspected" \
  --display-phase inspect --next-display-phase change \
  --evidence "affected source and focused tests reviewed" \
  --evidence-tier test --color
```

Meaningful transitions print concise `-`/`+` diffs. Full snapshots appear at
start, periodically, when blocked, on recovery with `show`, and at completion.
Use `🟩`, `🟨`, `⬜`, `🟥`, and `🟦` for completed, active, pending,
blocked, and informational/not-required work. Completion needs evidence;
blockers need the exact blocker, checked evidence, and next action. The internal
phase order remains `intake`, `knowledge`, `context`, `plan`, `dispatch`,
`implementation`, `review`, `verification`, `learning`, and `closure`;
it governs closure but is not generic user-facing filler. Represent real
delegated work as bounded named lanes and keep active lanes visible until they
produce evidence or a truthful blocked/not-required result. Use `--quiet` only
for an explicitly silent request or internal test harness. Simple questions
remain exempt and do not create checklist state.

Every input first invokes the bounded read-only memory route (or performs the
equivalent root-index lookup). At terminal closure, invoke
`scripts/record_task_learning.py` for required work and for lightweight work
only when `reusableLearning=true`. It performs an exact-scope sanitized local
upsert and queues a no-clobber repository/module/feature candidate note. The
existing lesson is updated, expanded, or superseded only within the same tree,
tenant, and access scope; a new lesson is saved only with evidence; and a
pre-write failure remains `blocked`. Tenant/restricted records stay in the
scoped JSON store, and any failure after a durable side effect is visible as
`partial`, never as success.

Detailed completion checks:

- [ ] Requirement understood.
- [ ] Current behavior identified.
- [ ] Expected behavior identified.
- [ ] Reproduction or validation path sufficient.
- [ ] Blocking questions resolved.
- [ ] Existing issues and knowledge searched.
- [ ] Relevant code and executing path analyzed.
- [ ] Root cause or implementation gap established.
- [ ] Pre-implementation decision record complete.
- [ ] Minimum file/change set approved.
- [ ] Specialist implementation complete.
- [ ] Code review complete.
- [ ] Regression impact checked.
- [ ] Focused tests and real-behavior verification complete.
- [ ] Original requirement verified.
- [ ] Documentation/worklog updated where required.
- [ ] Learning candidate sanitized, classified, and promoted/rejected/kept session-local.
- [ ] All dispatch waves stayed within their parallel and writer bounds.
- [ ] Every required role has a valid terminal receipt, or the task is explicitly blocked.
- [ ] Failure, shadow, rollback, worklog, and activation status are separately reported.
- [ ] No unexplained or unrelated changes remain.
