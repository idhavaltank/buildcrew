# Harness capability boundary

Harness is an optional capability provider, not a second AE controller. A
Harness manifest is a declarative catalog entry that AE may inspect and, only
after an explicit AE decision, project into a bounded task packet. It cannot
route work, select a model, own a lifecycle, grant authority, create state, or
close a task.

The schema for this boundary is
`schemas/capability-manifest-v1.schema.json`. The schema is intentionally
strict: unknown fields are rejected so a prompt, callback, secret, command, or
other authority-bearing payload cannot hide inside a manifest.

## Canonical registry and default state

There is one canonical registry for the host/workspace capability catalog. Its registry owner
is general, its controller is AE, its `canonical` marker is `true`, and its
`enabledByDefault` marker is `false`. A Harness-owned catalog or a second
registry is not merged with it. Duplicate IDs are rejected before selection.

Every capability is independently opt-in and also has
`enabledByDefault: false`. A manifest describes what exists; it does not turn
anything on. AE must separately decide whether a capability is the smallest
useful capability for the current task and grant a task-bound scope. An
optional capability is never an implicit bundle. Capabilities are disabled by default.

## Status is evidence, not authority

Each capability has an `availability` and four required status dimensions:

| Field | Meaning |
| --- | --- |
| `availability` | `AVAILABLE`, `UNAVAILABLE`, `DISABLED`, `STALE`, `DUPLICATE`, or `INVALID`; only `AVAILABLE` can be considered for selection |
| `status.documented` | `DOCUMENTED` proves that the capability has a current catalog description, otherwise `UNAVAILABLE` |
| `status.installed` | `INSTALLED` proves the host exposes the declared package/profile, otherwise `UNAVAILABLE` |
| `status.runnable` | `RUNNABLE` proves the host can invoke the bounded capability, otherwise `UNAVAILABLE` |
| `status.executed` | `EXECUTED` requires task-bound execution evidence; `NOT_EXECUTED` is not a pass and `UNAVAILABLE` is explicit missing coverage |

If a provider is not present, AE records
`CANDIDATE_UNAVAILABLE` in its canonical task packet and records the manifest
capability as `UNAVAILABLE` when a manifest entry exists. AE continues only
on its supported path; it never simulates an execution, invents a receipt, or
turns an unavailable capability into a successful gate.

The manifest also records, for every entry:

- `owner`, `version`, and a content `fingerprint` for provenance and freshness;
- supported `taskCategories`;
- a `readWriteClass` (`READ_ONLY` or the narrowly named
  `REPOSITORY_DOCUMENTATION_WRITE` class);
- a bounded `scope` with repository/path limits, allowed path classes, an
  explicit AE grant requirement, and no external side effects; and
- `evidenceRequirements` describing the evidence type and freshness needed to
  accept each state.

An owner/version/fingerprint mismatch is stale. Stale output is rejected, not
silently refreshed or treated as authoritative. A duplicate ID, duplicate
registry, or stale fingerprint is a pre-selection error.

## Non-authoritative by construction

The manifest authority declaration is `NON_AUTHORITATIVE` and names `AE` as
the controller. Its explicit boundary is false for routing, model selection,
write granting, lifecycle closure, state creation, secret access, instruction
fetching, callbacks, and arbitrary code execution.

The manifest has no fields for or permission to carry:

- no routing or route proposals;
- no model selection, reasoning level, or model override;
- no lifecycle closure, activation, rollback, or task state;
- no state roots, receipt stores, worklogs, or memories;
- no credentials, secrets, or other protected material;
- no prompts, embedded instructions, or URLs that fetch instructions;
- no callbacks, shell commands, scripts, arbitrary executable content, or
  worker-spawn directives.

The provider cannot dispatch another worker, change AE policy, expand a
writer lease, update the canonical registry, write a worklog, update memory,
activate a skill, or close a task. A `REPOSITORY_DOCUMENTATION_WRITE` class
describes a possible bounded operation; it is not a grant. Only AE can issue
the task-bound writer lease and only the `writer` role can use it.
No credentials are ever supplied by this declarative boundary.

## Optional capability catalog

These six capabilities are independent. Each has a small purpose, a bounded
scope, and an explicit unavailable path.

| Capability | Intended task categories | Default class and maximum scope | Required proof | If unavailable |
| --- | --- | --- | --- | --- |
| `repository-documentation` | documentation, verification | `REPOSITORY_DOCUMENTATION_WRITE`; one repository and only AE-allowlisted documentation paths | current documentation reference, source/diff-bound documentation change, focused validation result | Record `UNAVAILABLE`; AE keeps documentation ownership and does not claim the Harness change ran |
| `lint-architecture` | lint, architecture, verification | `READ_ONLY`; one repository and bounded check/evidence paths | runnable check result, source fingerprint, findings, and focused test or architecture evidence | Fast check is missing coverage; do not silently downgrade a required gate |
| `ci-gates` | ci, verification | `READ_ONLY`; one repository and task-bound CI evidence | installed/runnable check status, exact executed result, and side-effect inventory | Record unavailable; no CI edit, activation, service start, or pass claim |
| `observability` | observability, verification | `READ_ONLY`; one repository and task-bound sanitized observations | runtime observation, privacy check, and task-bound receipt | Record unavailable; static evidence cannot be presented as runtime proof |
| `specialist-profiles` | specialist-selection | `READ_ONLY`; task evidence only, never a second role registry | profile provenance, owner/version/fingerprint, and AE reconciliation | AE uses its canonical role contracts or remains lead-only |
| `quality-technical-debt` | quality, technical-debt, verification | `READ_ONLY`; one repository and bounded quality evidence | focused quality/debt result, source fingerprint, owner, freshness, and disposition | Record unavailable; no implicit background collection or closure effect |

Capability names are not AE roles. `specialist-profiles` may describe a
profile proposal, but AE still selects one of its eight role contracts,
chooses the model, binds the scope, and owns the receipt.

## Validation sequence

Before a capability can appear in a selected task packet, AE/host validation
performs this sequence:

1. Parse the exact v1 schema and reject unknown fields, malformed versions, or
   malformed fingerprints.
2. Require the single canonical registry and its disabled-by-default marker.
3. Require Harness ownership, a known independent capability ID, all four
   status dimensions, a task category, read/write class, bounded scope, and
   evidence requirements.
4. Reject duplicate IDs, duplicate registries, stale owner/version/fingerprint
   records, `DISABLED`, `STALE`, `DUPLICATE`, or `INVALID` entries, and any
   capability that is not explicitly opt-in.
5. Treat `UNAVAILABLE` as a valid reported state but an ineligible selection;
   record the reason and missing evidence rather than guessing.
6. Reconcile the capability with the current AE packet. User direction,
   current source/tests/runtime evidence, and AE policy outrank provider
   metadata. Conflicts fail closed.
7. Require task-bound execution evidence before accepting `EXECUTED`. A
   manifest fingerprint or non-empty receipt ID alone is insufficient.

No step invokes a Harness controller, imports a Harness prompt, starts a
service, edits CI, creates a state root, or mutates an external repository.

## Evidence and failure handling

Evidence must identify the capability ID, owner/version/fingerprint, task and
repository scope, source/diff fingerprint, check or observation, result,
timestamp/freshness, blockers, and residual risk. It must be sanitized: no raw
prompts, secrets, credentials, customer data, or untrusted instruction text.

`NOT_EXECUTED`, `UNAVAILABLE`, `STALE`, `DUPLICATE`, `INVALID`, and missing
evidence remain visible as their own states. They are not converted to
`PASS`, `EXECUTED`, or `CLOSED` because AE needs a clean status. The static
manifest contract proves the boundary; it does not prove that a host actually
installed, ran, or observed the capability.
