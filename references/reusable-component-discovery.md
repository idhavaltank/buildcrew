# Repository-first reuse and existing-pattern discovery

Use this reference before every actionable code or source mutation. The bounded baseline check is always
active, even when the request does not mention reuse. It covers existing components, hooks, clients,
services, contracts, generated types, repositories, validators, utilities, scripts, configuration,
schemas, tests, templates, and established local patterns. The repository
is the source of truth for names, public boundaries, behavior, data-flow, and conventions.
User-provided names are search hints, not proof that an abstraction exists.

This procedure is additive to the main skill. It does not create a second controller, lifecycle,
packet schema, registry, component catalog, or runtime scanner. It does not replace contract-first,
authorization, validation, rate-limit, persistence, migration, runtime, or platform-specific gates.

## When this procedure applies

### Always-active bounded baseline

Before every actionable code or source mutation:

1. identify the target repository, module/layer, and local instructions;
2. search the nearest approved scope for exact names and semantic equivalents;
3. inspect the public boundary and implementation plus a representative usage, test, or sibling
   pattern when a plausible candidate exists;
4. record `REUSE`, `EXTEND`, `NEW`, or `DOCUMENTED_EXCEPTION` before editing.

For configuration, schema, automation, or infrastructure source mutation, search for the existing
configuration key, schema declaration, script, helper, or repository convention instead of
pretending the task needs a UI-component scan.

A direct answer or read-only investigation has no mutation to gate. If it becomes an edit, stop and
complete the baseline first. Previously collected executing-path evidence may satisfy the bounded
search when it identifies the candidate, its boundary, and a representative usage or test; record
that evidence rather than repeating work mechanically. Runtime-only validation and documentation-only
edits do not require the typed code-reuse assessment; documentation still follows nearby style and
structure under the normal minimal-change rule.

### Expanded assessment

For normal, coupled, shared-boundary, cross-package, unclear-ownership, or explicitly reusable work,
expand the baseline before mutation when the request involves any of the following:

- a page, form, field, input, select, radio, checkbox, button, modal, widget, design-system
  primitive, layout component, or shared UI utility;
- a data-fetching or data-writing hook, client-side API client, service module, endpoint wrapper, query or
  mutation hook, cache utility, or shared request/response mapper;
- a new shared form utility or a change that composes existing controls with form state,
  validation, loading, error, or submission behavior;
- a backend contract, DTO, controller/route, service, repository, validator, persistence helper,
  authorization/rate-limit boundary, queue/cache integration, or migration pattern;
- a script, workflow, configuration key, schema, template, or test harness
  that could duplicate an established repository convention.

### Fast mobile exception

When the mobile classifier returns `FAST_DAILY` with `mutationRequested=true`, always run the bounded
baseline but do not run the
expanded repository-wide procedure automatically. Search the explicit target and nearest module
boundary, inspect the nearest public API or established pattern plus one nearby usage or test when a
candidate exists, and record `REUSE`, `EXTEND`, `NEW`, or `DOCUMENTED_EXCEPTION`. Escalate to the
expanded assessment only when the change is shared, cross-package, or the target boundary is not
already known.

The default choice order is:

    REUSE -> EXTEND -> NEW -> DOCUMENTED_EXCEPTION

The order is a decision discipline, not a requirement to force an incompatible abstraction into the
feature.

## 1. Establish the search boundary

Before searching, identify:

1. the target repository, application, package, and platform;
2. the nearest applicable `AGENTS.md` or equivalent local instructions;
3. the feature entry point and its owning package/module;
4. the existing UI-to-data path, if the request crosses a client, hook, service, API, cache, or
   generated contract boundary;
5. package or workspace boundaries that cannot be inspected or modified.

Search only within the approved boundary. If a candidate is outside the allowed package, record the
boundary and use its public import or API contract rather than copying its implementation.

## 2. Search exact names and semantic equivalents

Search both the requested vocabulary and the repository's vocabulary. Start with bounded file and
symbol searches, then narrow to definitions, exports, usages, tests, and stories. Typical locations
include:

- `components`, `fields`, `forms`, `widgets`, `ui`, `design-system`, `shared`;
- `hooks`, `api`, `client`, `services`, `queries`, `mutations`, `repositories`, `data`;
- `contracts`, `openapi`, `dto`, `controllers`, `routes`, `validators`, `persistence`, `migrations`;
- `scripts`, `config`, `schemas`, `templates`, `workflows`, and representative tests;
- package entry points, barrel files, generated client/type directories, and feature siblings.

For a UI request, search the exact name and aliases such as text/input, select/dropdown, radio,
checkbox/toggle, button/submit, field/form-control, modal/dialog, and the local design-system terms.
For a data/API request, search the resource and action names plus aliases such as fetch/get/list,
create/update/save, query/mutation, service/repository, client, endpoint, and the generated type or
operation name.

For backend, tooling, configuration, or schema work, search the behavior and owning
boundary plus aliases such as contract/DTO, controller/route, validator, repository/persistence,
middleware/guard, migration, script/task, configuration key, schema, template, runbook, and test
fixture.

Useful search terms include the requested symbols, likely import paths, `register`, `control`,
`Controller`, `name`, `value`, `defaultValue`, `onChange`, `error`, `isLoading`, `isPending`,
`useQuery`, `useMutation`, the repository's request client, and endpoint/resource identifiers.
Use the repository's established search tool and keep the commands bounded. For example:

```bash
rg --files -g '*component*' -g '*field*' -g '*form*' -g '*hook*' -g '*service*' -g '*client*' \
  -g '*query*' -g '*mutation*' <approved-package>
rg -n "TextField|SelectField|Radio|Checkbox|Button|register|Controller|useQuery|useMutation" \
  <approved-package>
rg -n "<CandidateName|useCandidate|candidateService|candidateClient|candidateQuery" \
  <approved-package>
```

The example terms are probes, not required names. Follow up with export/barrel searches so an
implementation is not recreated merely because its definition is not in the feature directory.
Record meaningful negative results; do not claim that no abstraction exists after searching only one
filename or one directory.

## 3. Inspect the definition and how it is used

For every plausible candidate, inspect the public import/export path, implementation or contract,
and at least one representative call site. Inspect more than one call site when behavior varies or
the first example is incomplete. Read focused tests, stories, or sibling usage when available.

For UI candidates, establish:

- props and types, controlled/uncontrolled behavior, default values, and state ownership;
- the exact form binding pattern for `name`, value/default, change, validation, and error;
- whether the form uses `register`, `control`, `Controller`, a field adapter, or another local
  convention;
- loading, disabled, touched, pending, empty, and submission behavior;
- styling, layout, accessibility, labels, descriptions, keyboard behavior, and platform limits;
- composition rules, required wrappers/providers, and the tests that protect the behavior.

For data/API candidates, establish:

- the public client/service/query/mutation import and the owning endpoint or operation;
- request parameters, generated/manual types, response shape, and existing data transformation;
- authentication, workspace/tenant context, authorization, headers, retries, and error handling;
- loading, pending, empty, stale, cache-key, invalidation, optimistic-update, and refetch rules;
- how representative callers pass parameters and consume the result;
- focused tests or usage examples for the transport and data-flow boundary.

For backend, tooling, configuration, or schema candidates, establish:

- the authoritative contract, route/controller, service, repository, validator, helper, script,
  schema, template, or local convention;
- public ownership, callers/consumers, generated-file boundaries, and compatibility constraints;
- transaction, auth, validation, cache/queue, migration, execution, or publication behavior as
  applicable;
- a representative caller, sibling implementation, focused test, or validated example.

Trace the narrow real path when the request crosses layers:

    form or screen -> field/hook -> client or service -> endpoint/operation -> response/cache/state

Do not infer a candidate's API from its filename. Do not infer correct form binding from a visual
similarity. Do not infer a reusable data boundary from a hook name while ignoring its client,
endpoint ownership, cache behavior, or response mapping.

### Form binding rule

When an existing field or button abstraction fits, compose it using the same binding shape observed in
the repository. Preserve the field's expected `name`, value/default, change handler, error,
`register`, `control`, or `Controller` integration. A visually correct field that is not connected to
the repository's form state, validation, and submission path is not a valid reuse.

### Data-fetching rule

When a shared API client, service, endpoint module, generated operation, query/mutation hook, or
request utility fits, use that public abstraction and its established data-flow. Preserve its auth,
parameters, types, loading/error behavior, caching, invalidation, and data-shaping conventions. Do
not create a new hook that calls Axios or `fetch` directly, hard-codes an endpoint path, duplicates
response mapping, or introduces a second query pattern when a suitable shared boundary exists.

### State ownership and lifecycle rule

After identifying the root cause and before choosing the implementation, trace how the proposed
behavior fits the existing flow. For each affected concern (loading/submission, errors, data,
permissions, caching, retries, or cleanup), identify its current owner and the public signals or
operations it exposes. Read the implementation and a representative consumer; copying a sibling
without checking its necessity can reproduce the same duplication.

For asynchronous flows, check when each signal starts and ends, which callbacks or refreshes are
awaited, and what happens on success, failure, and any relevant concurrent calls. Names such as
`isLoading`, `isFetching`, or `isPending` do not establish those semantics. Prefer deriving the
needed UI behavior from compatible existing signals. Add state, an effect, a wrapper, or another
request only when the existing owner cannot express a required behavior; record the concrete
gap and its verification in the existing assessment's fit/gaps and rationale fields.

A distinct operation-level state can be appropriate when verified work outlives the existing
signals, or when concurrency/cancellation requires different semantics. Do not assert a timing
gap without evidence, and do not remove necessary state solely to reduce line count. The target
is the smallest correct change that preserves the existing integration.

Verify the lifecycle at the consumer boundary: loading/disabled behavior during each awaited
stage, the handoff between stages, completion, and failure. Use the real hook with mocked
transport where practical, or a contract-faithful test double that updates state and rerenders.
Do not freeze dependency loading flags to false and then treat a passing test with local state
as evidence that the local state is required. Keep behavioral assertions independent of the
chosen state variable or hook arrangement.

## 4. Record the decision before mutation

Put the following assessment in the existing task packet, implementation plan, or change ledger,
then encode the mutation-authorizing evidence as
`schemas/repository-reuse-assessment-v1.schema.json`. This is an evidence contract inside the
existing workflow, not a new lifecycle or controller.

```text
Repository-first reuse assessment
Task/repository/source/task-scope bindings:
Target repository/application/package/platform:
Requested behavior and candidate abstraction/pattern families:
Bounded search terms and paths:
Commands and results:
Candidates inspected:
  - path/symbol -> public import -> implementation/API/data-flow -> representative usages/tests -> fit/gaps
Decision: REUSE | EXTEND | NEW | DOCUMENTED_EXCEPTION
Chosen abstraction/import/binding/data-flow pattern:
Why this is the smallest safe choice:
Why alternatives were rejected:
Negative-search evidence for NEW:
Raw native control or direct low-level API exception, if any:
Verification:
Terminal status, issued time, and expiry:
```

A short task may keep the record compact, but it must still identify what was searched, what was
inspected, and why the selected outcome is safe. If evidence is missing because a package is
unavailable, mark the limitation instead of converting uncertainty into `NEW`.

## 5. Choose one outcome

### REUSE

Use the existing abstraction when its public API and behavior satisfy the requirement. Follow the
observed import path and representative usage. Do not wrap it in a duplicate feature-level component
or hook unless composition is required by the existing API and the wrapper has a separately justified
boundary.

### EXTEND

Extend an existing abstraction only when the requirement belongs to its responsibility and the
smallest backward-compatible extension serves both current and new callers. Record the changed API,
callers, compatibility risk, and focused regression checks. Prefer composition when the behavior does
not belong in the shared abstraction.

### NEW

Create a new abstraction only after exact and semantic search, export inspection, representative
usage inspection, and extension/composition analysis establish a real gap. Give it a responsibility-
accurate name, use the nearest existing lower-level boundary, and record why the new abstraction will
not duplicate an existing one.

### DOCUMENTED_EXCEPTION

Use this outcome when a native control, low-level transport call, or other bypass is required by
platform-native behavior, special semantics, accessibility constraints, performance/transport
constraints, or a proven absence of a suitable local abstraction. Record the candidate considered,
the incompatibility or negative-search evidence, the scope of the exception, and the reviewer. The
exception must remain as narrow as possible.

## 6. Bypass rules

- Do not add feature-level raw `<input>`, `<select>`, `<textarea>`, `<button>`, radio, checkbox, or
  equivalent native controls when a suitable shared field/control exists. Native elements inside the
  implementation of the shared primitive are not feature-level bypasses.
- Do not add direct Axios/`fetch`, hard-coded endpoint paths, ad-hoc auth headers, duplicate
  response mapping, or a new cache/query convention when the repository already owns that behavior in
  a client, service, endpoint module, generated operation, query/mutation hook, or shared utility.
- Do not render a shared field as an unbound visual element when existing forms bind it through
  `name`, `register`, `control`, `Controller`, or an equivalent adapter.
- Do not copy an inaccessible implementation across a package boundary. Use the public export and
  record an access or ownership blocker when the public API cannot satisfy the request.
- Do not choose a third-party primitive before checking compatible local abstractions and the
  platform adapter.
- Do not create a duplicate contract, DTO, controller/route, service, repository, validator,
  migration helper, script, configuration key, schema, or template when a
  compatible repository-owned boundary or pattern already exists.

These are review-failure conditions unless the decision record contains a valid
`DOCUMENTED_EXCEPTION`.

## 7. Worked patterns

### Existing shared form controls

For a create/update form with a boolean that controls whether the form is shown, followed by name,
mobile, email, and submit fields:

1. Search for the repository's shared boolean/toggle, text, select, radio, checkbox, field-wrapper,
   and button abstractions and their public exports.
2. Inspect a representative create or update form to learn the form library and binding shape.
3. Compose the existing controls, preserving the observed checked/unchecked behavior, field names,
   values/defaults, change handlers, validation errors, disabled/pending state, and submit binding.
4. Reuse the existing create/update mutation or service boundary when one exists, including its
   payload shape, auth/context, loading/error handling, and cache invalidation.
5. Record `REUSE` for each compatible primitive and verify the rendered conditional behavior plus the
   submitted payload. Do not add raw HTML controls simply because the new form is in another feature
   directory.

The names in a request are search hints. If a repository uses different names, use the discovered
public abstractions and document the mapping.

### Existing data-fetching abstraction

For a request to create a settings-fetching hook:

1. Search settings resource/action names, existing settings callers, API clients, services,
   endpoint modules, generated operations/types, and query/mutation hooks.
2. Inspect the existing settings call and the shared client/service/query implementation, including
   path ownership, parameters, auth/context, cache key, loading/error behavior, invalidation, and
   response shape.
3. Reuse the compatible public abstraction or compose a thin hook around it if a hook boundary is
   genuinely needed by the feature. Preserve the repository's types and data-flow.
4. Record `REUSE` or `EXTEND` and the representative evidence. A new hook must not bypass the shared
   client with direct Axios/`fetch` or hard-code a path that the repository already owns.

If no shared data boundary exists, record the bounded search and create the smallest hook/service at
the repository's established transport boundary, not a second transport convention.

### Permission refresh after creating an object

When creation succeeds but access only appears after a page refresh, trace the permission and
entity-state owners before changing the create flow. If the verified missing step is refreshing
permission tuples, reuse the existing permission API hook and store action, then await the
existing entity refresh before navigation.

For Save-button loading, inspect the create hook, permission hook, and entity-refresh hook. If
their signals cover the required sequence, derive the button's loading value from those signals
instead of adding a parallel `isRefreshingAccess` state and setter/reset paths. Verify pending
permission and entity stages, duplicate submission prevention, and failure behavior with mocks
that preserve the hooks' loading transitions. If the lifecycle trace reveals uncovered work,
document that gap before adding operation-level state. This example is not a rule that every
permission problem requires these requests or that all loading flags are interchangeable.

### No suitable match

If a needed primitive or data abstraction is absent, record the search paths and terms, the closest
candidate, the extension/composition analysis, and the reason it cannot satisfy the requirement.
Then create only the smallest missing abstraction, using existing lower-level conventions for
styling, form state, transport, errors, types, and tests. The absence of an exact filename alone is
not sufficient evidence.

### Explicit native or low-level exception

If a requirement genuinely needs a platform-native control or low-level transport behavior, record
the requirement, candidate abstraction, observed incompatibility, narrow scope, and verification
before implementation. Keep the exception local and avoid introducing a reusable duplicate unless a
separate `NEW` decision proves that boundary is needed.

## 8. Review and verification

Before completion, verify:

- all requested candidates have a `REUSE`, `EXTEND`, `NEW`, or `DOCUMENTED_EXCEPTION` outcome;
- imports resolve through the repository's public path and no duplicate barrel/export was added;
- feature-level raw controls and direct low-level API calls are absent or explicitly justified;
- form controls preserve the observed name/value/change/error and form-library binding behavior;
- hooks/services preserve endpoint ownership, auth/context, types, loading/error, caching, and
  invalidation behavior;
- added state/effects/wrappers/requests address an evidenced gap in the existing owner, and tests
  preserve the dependency lifecycle relevant to that decision;
- the implementation follows at least one inspected representative usage and its focused tests or
  stories where available;
- rendered, submission, loading/error, accessibility, and platform behavior is verified as relevant;
- the final diff contains no unexplained abstraction, helper, endpoint path, or response mapping;
- backend, tooling, configuration, and schema changes follow the inspected
  repository-owned boundary or record a narrow exception.

## 9. Fail-closed mutation enforcement

The written decision is not enough by itself. Encode it as
`schemas/repository-reuse-assessment-v1.schema.json`, validate it with
`scripts/validate_reuse_assessment.py`, and provide it to the enforced context gate before mutation.
The record is bound to task, repository, source fingerprint, task-scope fingerprint, target paths,
search terms, candidates/evidence, outcome, issue/expiry time, and terminal status. Missing,
malformed, stale, or mismatched records block mutation authority. `NEW` without negative-search
evidence and `DOCUMENTED_EXCEPTION` without a reason also block.

If a reviewer cannot determine whether an abstraction was reused correctly, the result is
`MISSING_COVERAGE`, not a pass. Resolve the evidence gap with bounded repository inspection or report
the residual risk and keep the task open.
