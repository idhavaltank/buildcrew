# Unified operating model

This reference defines the operating model owned by the standalone skill. The
external project is an optional design/reference source, not a runtime
dependency.

## Ownership

The standalone skill performs first-pass classification, request-packet
creation, route selection, quiet progress tracking, and date-wise worklog
updates through its bundled utility. It owns the request packet, route decision,
risk floor, execution topology, evidence requirements, and completion decision.

Its operational responsibilities include:

- requirement-first intake and clarification;
- source-grounded execution-path tracing;
- real agent delegation with canonical role names;
- lead ownership and conflict resolution;
- minimal-change and change-ledger gates;
- implementation, review, regression, testing, and final verification;
- sanitized learning write-back and honest blocker reporting;
- adaptive model selection and runtime capability fallback.

## Canonical intake and capability projection

The active skill is the sole lifecycle authority. Optional source capabilities
are consumed through a provider-neutral, pure projection boundary, never by
invoking another controller:

```text
project(normalized_intake) -> versioned projection
```

The projection may contain only route, complexity signals, risk tier/reasons,
requirements, capabilities, and coverage. It may not create or mutate task,
plan, run, lane, writer, receipt, worklog, or activation state. The provider
must return `AVAILABLE`, `CANDIDATE_UNAVAILABLE`, or `INVALID` explicitly.

Both sources feed one versioned canonical intake packet. The packet preserves
raw and normalized intake, source/projection status, evidence, conflicts, and
the controller's reconciliation result. Only the reconciled packet drives
routing, execution, verification, and closure.

## Reconciliation and fallback

The controller reconciles projections with explicit user requirements first,
then current repository, test, runtime, and device evidence, then general
domain rules, with candidate safety signals as an additional input. Candidate
route output is advisory; the controller selects the final route. Complexity
and risk use the higher verified tier, risk reasons are unioned, and required
capabilities/coverage are unioned. Conflicts remain visible; unresolved safety
conflicts fail closed. No source may lower an existing verified risk floor.

When projection is unavailable, the packet records
`candidateProjection.status=CANDIDATE_UNAVAILABLE` and a reason. The controller
continues on the supported general-only path when otherwise authorized, marks
hybrid coverage as unavailable, and does not simulate output or fabricate
receipts. A later shadow projection may compare results without silently
changing the plan or lifecycle state.

It also incorporates these deterministic behaviors from the external
autonomous-engineering candidate without requiring that package:

- stable route names and side-effect-free classification;
- strict task, requirement, plan, run, lane, review, and receipt concepts;
- risk signals, monotonic complexity floors, and capability coverage;
- functional diagnosis roles and bounded waves;
- coupled-boundary serialization;
- terminal evidence receipt validation;
- source and lane provenance;
- shadow-mode and legacy-boundary safeguards.

## Decision sequence

1. Minimally normalize the input enough to route it.
2. Run the bounded local-memory lookup for every input and revalidate matches.
3. Build the minimum canonical requirement packet when lifecycle state is needed.
4. Obtain any optional provider/capability projection and record its status.
5. Reconcile route, complexity, risk floor, and required capabilities.
6. Choose the smallest useful topology and disjoint scopes.
7. Trace current behavior and pass the minimal-change gate.
8. Execute with read-only or mutation authority explicitly separated.
9. Validate evidence, receipts, review, tests, and requirement coverage.
10. Capture the outcome in the worklog and learning system.

For a `FAST_DAILY` mobile or Flutter source change, use the bounded target
classifier before this sequence and keep steps 3–5 target-scoped: nearest
instructions, the owning module, and the narrow screen/state/data path are
enough unless the evidence reveals a coupled boundary. Resolve the
`FAST_DAILY_DIRECT` lead model before building strict model-selection task
facts. This skips task-facts, fingerprint, host-catalog, expiry,
coordinator-scope, and fallback construction, but not target context,
minimal-change, focused tests, or source-only runtime honesty. Do not invoke the
full project-context, expanded repository-wide reusable-abstraction, QA, or
worktree sidecars unless the request is shared, runtime-bound, coupled,
high-risk, or delegated. The bounded repository-first reuse assessment remains
required before code/source mutation. This exception changes cost, not the risk floor or
the requirement to test the changed path.

## Route authority

Routing never creates a lane, starts an agent, writes repository files, or
invokes a legacy controller. It produces a decision for the unified controller.
The controller may choose to stop after a direct answer, request clarification,
run source analysis, or proceed to scoped execution.

The same rule applies to a capability projection: it can propose route, risk,
and coverage only. It cannot reconcile conflicts, claim completion, or change
the controller's lifecycle state.

## Adaptive effort

| Scope | Default handling |
| --- | --- |
| Informational or narrow | Lead-only, targeted evidence, direct answer |
| Small implementation | Lead plus focused analysis or review only when useful |
| Normal implementation | Requirement gate, bounded specialist, reviewer, targeted tests |
| Coupled or high-risk | Functional diagnosis waves, strict evidence, one writer, expanded regression checks |

The presence of a role in a reference does not require spawning that role. Every
agent must have a concrete output that can change an implementation or
verification decision.

## Role-based model policy

The executable policy in `config/model-selection-v1.json` selects a model from
the normalized task complexity and separates coordination from bounded
execution:

- direct questions and narrow checks: no assignment;
- lightweight workers: FastModel at low reasoning;
- small workers: FastModel at medium reasoning;
- normal workers and normal coordination: BalancedModel at high reasoning;
- coupled, security, architecture, cross-repository, difficult-root-cause, and
  high-risk work: AdvancedModel at max reasoning, including bounded workers and
  coordination;
- final-audit coordination: AdvancedModel at max, with FastModel/max only for delegated
  evidence collection;
- lightweight/small worker continuity: one fingerprinted policy fallback to
  BalancedModel at high reasoning when the preferred FastModel capability is unavailable;
- normal worker continuity: one fingerprinted policy fallback to AdvancedModel at max
  reasoning when BalancedModel is unavailable;
- coupled/high-risk continuity: one explicit-user AdvancedModel-to-BalancedModel/high exception
  when the preferred AdvancedModel capability is unavailable; final-audit fallback is
  prohibited.

The host catalog resolves availability but cannot change this policy. A normal
fallback is reported as `FALLBACK_EVIDENCED`; a high-risk exception is
`OVERRIDE_EVIDENCED`; missing targets remain `CAPABILITY_UNAVAILABLE` and
`HOST_INHERITED` never dispatches. Every assignment carries task, project,
repository, scope, policy, host, fingerprint, and expiry bindings. Validation
time at or after task expiry is rejected before selection, and a host-owned
claim store consumes an assignment identity once; retries require a fresh
attempt.

The `FAST_DAILY_DIRECT` lead result is not an assignment: it is session-only,
has no coordinator or worker scope, and cannot cross a host dispatch or native
agent bridge. Any delegation request, runtime requirement, shared boundary, or
verified risk signal returns to the strict policy above.
