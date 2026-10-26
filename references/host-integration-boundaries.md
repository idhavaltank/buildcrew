# v2 Host-Integration Boundaries

This reference defines how the standalone v2 package becomes host-capable
without becoming host-authoritative.

## Controller rule

AE owns intake, routing, model selection, agent selection, file scope,
verification, lifecycle, and closure. Every host integration is a subordinate
transport. A transport can return evidence; it cannot change AE policy or
claim completion by itself.

## Boundary map

Status meanings are deliberately separate: `AVAILABLE` means the host runtime
prerequisites were detected, `executionReady` means the task-specific inputs
are present, `CONFIGURATION_REQUIRED` means a host adapter or approval has not
been supplied, `CAPABILITY_UNAVAILABLE` means a configured capability cannot
run, and `NOT_SUPPORTED` means this package has no matching adapter. None of
these statuses alone proves that a task executed; only a valid host-bound
receipt can do that.

| Capability | AE contract | Host-owned prerequisite | Missing-host result |
| --- | --- | --- | --- |
| Routine mobile source flow | `engine/mobile_workflow.py` + `select_fast_daily_model()` + target-scoped context | explicit mobile platform and target | source-only; no runtime claim |
| Browser QA | `engine/qa_adapters.py` + `engine/real_qa_adapters.py` | approved origin, Playwright Node module, browser executable, and artifact directory | `BLOCKED` / `NOT_VERIFIED` |
| Android QA | `engine/qa_adapters.py` + `engine/real_qa_adapters.py` | explicit ADB path/serial, booted emulator/device, and artifact directory | `BLOCKED` / `NOT_VERIFIED` |
| iOS Simulator QA | `engine/qa_adapters.py` + `engine/real_qa_adapters.py` | explicit xcrun/simctl path, booted Simulator UDID, and artifact directory | `BLOCKED` / `NOT_VERIFIED` |
| Flutter runtime QA | `engine/qa_adapters.py` + `engine/real_qa_adapters.py` | explicit Flutter project, device identifier, Flutter executable, and artifact directory | `BLOCKED` / `NOT_VERIFIED` |
| Named reviewer source contracts | bundled strict assignment/receipt validators and a source-only bridge that always emits non-live evidence | no host required for source validation | `SOURCE_READY` |
| Named reviewer live execution | no bundled authenticated named-role runner | host-owned role catalog, durable claim store, and independent observed model/effort | `CAPABILITY_UNAVAILABLE` |
| Flutter static execution trace | bundled ten-stage source validator | no host required for source validation | `SOURCE_READY`; runtime remains `NOT_VERIFIED` |
| QA runtime | `engine/qa_runner.py` | registered adapter and approved smoke packet | `BLOCKED` |
| Model provider | `engine/provider_bridge.py` | authenticated catalog and provider transport | `CAPABILITY_UNAVAILABLE` |
| Native agents | `engine/native_agent_bridge.py` | bounded native spawn API | `CAPABILITY_UNAVAILABLE` |
| Explicit adapter execution | `engine/host_execution.py` + `scripts/run_host_adapter.py` | host-owned manifest, pinned executable, approved working root, and adapter protocol | `CAPABILITY_UNAVAILABLE` / `FAILED` / `TIMED_OUT` |
| Runtime coordination | `engine/runtime_coordinator.py` | validated runtime config, registered bridges, and at least one available adapter | `CAPABILITY_UNAVAILABLE` |
| Context enforcement | `engine/context_gate.py` | call from the canonical AE controller | `BLOCKED` / `MISSING_COVERAGE` |
| Source provenance | `engine/git_source_context.py` + `engine/source_provenance_gate.py` | current Git checkout, lane and writer binding | `CONFIRM_CURRENT` / `PROVISION_OR_REUSE` / `BLOCKED` |
| Worktree provisioning | `engine/worktree_host_adapter.py` | validated approval, lane identity and host-controlled target path | `BLOCKED` / `FAILED` |
| RAG/memory | local `scripts/lookup_memory_context.py` first; optional `engine/rag_bridge.py` second | approved memory root; scoped provider only when available | local matched/source-first receipt; provider may remain unavailable |
| Session continuity | `scripts/context_loop.py auto` and `close` | persistent host invocation, shared storage root, and exact task identity | lookup-only or source-first when no host calls the loop |
| Canonical project memory | `scripts/ingest_memory_candidate.py` | explicit trusted reviewer, candidate digest, and current source hashes | candidate remains retrievable only by its owning task |
| Harness | `engine/harness_bridge.py` | confirmed runtime registry/dispatcher | `NOT_EXECUTED` |
| Observability | `engine/observability_store.py` | approved durable store and operations tooling | `CAPABILITY_UNAVAILABLE` |
| Activation | `engine/activation.py` | owner approval, active host, canary switch | `CAPABILITY_UNAVAILABLE` |
| Installation | `engine/installation.py` | approved active root and atomic host switch | `CAPABILITY_UNAVAILABLE` |

## Evidence rule

An injected transport used by tests proves only the transport contract. A live
check requires a receipt bound to the current task, source fingerprint, target
identity, host identity, and approval state. A static validator, fixture, fake
transport, or manifest fingerprint cannot be promoted to runtime PASS.
Caller-provided host object properties cannot authenticate a named-reviewer
observation; that authority belongs to a separate host-owned integration.

The real browser transport launches only the explicitly configured Playwright
browser and supports the bounded actions `open`, `navigate`, `click`, `fill`,
`assert_visible`, `assert_text`, `assert_url`, `reload`, `wait`, and
`screenshot`. It accepts only allowlisted origins and writes logical artifact
references for screenshots, console logs, network records, and traces.

The real Android transport launches no device automatically. It requires an
explicit serial and supports bounded ADB install, launch, tap, text input,
UI-text assertion, wait, clear-data, screenshot, device identity, and command
evidence. Install and clear-data require `DISPOSABLE_ONLY` or
`EXPLICIT_APPROVAL` external state.

The iOS Simulator transport launches no Simulator automatically. It requires an
explicit booted UDID and supports bounded install, launch, open-url, log, wait,
screenshot, device identity, and command evidence through `xcrun simctl`.
Install requires `DISPOSABLE_ONLY` or `EXPLICIT_APPROVAL` external state.

The Flutter transport launches no device automatically. It requires an explicit
Flutter project and device identifier, verifies the selected device through
`flutter devices --machine`, and supports bounded project-relative test,
screenshot, wait, device identity, and command/log evidence. It does not accept
arbitrary shell commands or claim widget-level assertions that the selected
Flutter test did not produce.

The daily mobile path does not invoke these transports unless runtime
validation is explicitly requested or required by acceptance criteria. Flutter
and iOS are never mapped to Android, and source-only classification does not
prove an emulator, simulator, iOS, or Flutter run.

`FAST_DAILY_DIRECT` is a session-only lead model result, not a host assignment.
It must not enter browser, Android, native-agent, or worktree dispatch. Any
worker/coordinator request or boundary signal returns to strict model selection
and its task, scope, host, fingerprint, expiry, and fallback checks.

## Safe continuation

When a host is unavailable, AE may continue source-only work and use the
policy-approved model fallback rules. It must preserve the unavailable state,
avoid silent promotion, and keep the corresponding HTML checklist item open.

The subprocess gateway is an opt-in execution path, not an activation system.
It makes an existing host adapter callable through a bounded stdin/stdout
protocol; it does not supply provider credentials, select models, grant
permissions, or make browser, device, deployment, or production claims.
