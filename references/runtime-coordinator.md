# Runtime coordinator

`engine/runtime_coordinator.py` is the runtime layer above the native-agent
and model-provider contracts. It owns the configured runtime registry and
constructs the existing bridges, so a host integration does not need to
manually assemble every transport.

## What it provides

- registers one native-agent bridge and zero or more provider bridges;
- constructs subprocess transports from the validated host-execution
  manifests;
- probes runtime availability without starting an adapter;
- spawns a configured native-agent process for an already-bound assignment;
- chooses a registered provider whose catalog supports the assignment's
  already-resolved model and reasoning effort; and
- returns `CAPABILITY_UNAVAILABLE` with a non-live receipt when no suitable
  provider is available.

The coordinator does not select models, widen task scope, grant permissions,
read credentials, or claim that an unavailable provider ran. Model-provider
credentials and vendor-specific API code belong inside the host adapter.

## Configuration

Use `schemas/runtime-coordinator-config-v1.schema.json`. The config contains:

- one native bridge manifest and an optional `native-spawn` execution manifest;
- zero or more provider manifests, each with an optional `provider-execute`
  execution manifest; and
- one host identity shared by every manifest.

An unavailable runtime is represented deliberately: use a valid unavailable
bridge manifest or omit its execution manifest. Do not insert a fake provider
or a fake model catalog to make status appear available.

## Python integration

```python
from engine.runtime_coordinator import RuntimeCoordinator

runtime = RuntimeCoordinator.from_config(runtime_config)
status = runtime.status()
agent_result = runtime.spawn_agent(assignment, slot, expected_policy=policy)
provider_result = runtime.execute_provider(assignment, expected_policy=policy)
```

## CLI integration

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/run_runtime_coordinator.py \
  --config /absolute/path/runtime-config.json \
  --operation status
```

For `spawn-agent` and `execute-provider`, pass a request JSON containing the
already validated assignment and, for agent work, its bounded slot. The CLI is
an adapter entry point; the lifecycle controller still owns requirement gates,
scope, verification, and closure.
