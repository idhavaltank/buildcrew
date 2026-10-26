# Platform adapters

The core workflow is platform-independent. Use repository evidence and the
appropriate adapter rather than assuming every project has the same stack.

## Web frontend

Trace UI action, component, route, client request, state/cache update, rendered
behavior, accessibility, and responsive behavior. For React or TanStack work,
inspect existing query, form, table, layout, and design-system patterns before
creating abstractions. For forms, controls, hooks, and data-fetching, apply
`references/reusable-component-discovery.md`: inspect the public component/hook/API-client
or service export and a representative call site before creating a new one. Preserve
the repository's field bindings, auth, loading/error, caching, invalidation, and
data-shaping conventions.

## Backend and APIs

Trace client or caller, route/controller, DTO or contract, service, database or
cache, queue or external integration, and response. Preserve contract-first
rules, generated DTO boundaries, authorization, validation, rate limiting, and
meaningful endpoint tests where those conventions apply. Apply
`references/reusable-component-discovery.md` before mutation: search for the
existing contract, generated type, route/controller, service, repository,
validator, persistence helper, middleware, cache/queue boundary, migration
pattern, and focused test before adding a parallel implementation.

## Mobile and Flutter

Trace screen or event, state management, repository or client, local storage,
sync or queue, platform lifecycle, native boundary, and rendered behavior. For
routine source-local work, use `scripts/classify_mobile_workflow.py` first and
keep discovery bounded to the target module plus the nearest instructions and
manifest. This restores the V1 daily flow without making the full reusable-
abstraction procedure a mandatory repository-wide scan. The bounded reuse
assessment is still required for source mutation; runtime-only validation does
not require it.

When the classifier returns `FAST_DAILY` and no worker, coordinator, native
action, or runtime validation is requested, use its `FAST_DAILY_DIRECT`
selection for the lead session. It intentionally skips strict model-assignment
task facts, fingerprints, host catalog, expiry, coordinator-scope, and fallback
construction. It is not a worker assignment and must not cross a dispatch
adapter; if any delegation or boundary signal appears, use the strict selector.

Search the target and nearest module boundary for existing widgets, native form
fields, design-system controls, repositories, data clients, utilities, and
patterns. Inspect a public API or established pattern plus one representative
usage or test when a candidate exists. If the task only composes an
already-identified local boundary, record `REUSE` with that evidence and
continue with the smallest change. Check offline replay, network retries,
conflict resolution, background/foreground transitions, device-specific
constraints, and API compatibility when relevant; those signals select the
full safety route rather than the fast route.

Source routing covers Android, iOS, React Native, Expo, and Flutter. The
runtime host adapters cover browser, Android, iOS Simulator, and Flutter when
the host supplies the matching runtime, target, and artifact configuration.
Never use Android evidence for Flutter or iOS, and never treat a detected SDK or
simulator as execution evidence until the matching adapter returns a valid
host-bound receipt.

## Cross-platform work

Treat mobile, web, API, storage, and queue changes as one data-flow problem when
the acceptance criteria cross boundaries. Assign disjoint evidence scopes but
serialize shared persistence and contract decisions.

## Missing adapter behavior

If a framework-specific rule is unavailable, use the universal intake,
source-tracing, risk, minimal-change, evidence, and testing rules. Mark the
missing adapter as residual risk. Do not invent framework conventions or claim
platform coverage that was not verified.
