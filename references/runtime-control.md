# Runtime control boundary

`engine/runtime_control.py` is the host-called enforcement slice for the
portable task packet. It does not create an AI provider or execute a tool by
itself. A host supplies the executor and must call the control plane before
each step and tool action.

```python
from engine.runtime_control import RuntimeControlPlane

control = RuntimeControlPlane(
    task_packet,
    source_fingerprint="sha256:<current-source>",
    run_id="task-run-1",
)
control.start()
control.begin_step("inspect")
result = control.execute_tool(
    "repository-reader",
    "read",
    host_executor,
    arguments={"path": "src/feature.py"},
)
control.complete("focused checks passed")
```

The control plane supplies:

- wall-clock, step, tool, worker, token, and cost budgets;
- repeated-action detection inside a bounded sliding window;
- task-packet permission and explicit external-approval enforcement;
- untrusted-content rejection before authority-bearing actions;
- compact context that preserves the objective and acceptance criteria;
- redacted, ordered evidence events and optional durable persistence;
- explicit `TIMED_OUT`, `BLOCKED`, `CANCELLED`, `FAILED`, and `COMPLETED`
  terminal states.

The control plane intentionally reports `sourceOnly=true`,
`liveExecution=false`, and `sideEffects=[]` in its snapshots. A host adapter
must supply real runtime identity, credentials, sandboxing, cancellation, and
platform evidence before claiming production execution.

The injected executor must enforce detailed target allowlists, process-level
timeouts, credential isolation, network policy, rollback, and database
transactions. The control plane cannot safely infer those host-specific
properties from a generic callback.
