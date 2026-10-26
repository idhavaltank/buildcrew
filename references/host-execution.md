# Explicit host execution

The package does not assume that a particular AI coding tool, browser, device,
or model provider is installed.
The host can opt into live execution by supplying a `host-execution-manifest-v1`
and connecting it to the existing native-agent or provider bridge.

## What the gateway does

`engine/host_execution.py` runs one fixed executable with:

- no shell expansion;
- a pinned executable SHA-256;
- fixed arguments declared by the host;
- a working directory below an approved `allowedRoot`;
- a small explicit list of inherited environment variable names;
- a timeout and combined stdout/stderr byte limit; and
- one JSON request on stdin and one JSON object on stdout.

The gateway returns `CAPABILITY_UNAVAILABLE`, `FAILED`, or `TIMED_OUT` when
the host cannot execute safely. It never turns a missing adapter into a
successful result. Adapter output is still interpreted by the existing bridge,
which keeps AE policy, task scope, model binding, receipts, and closure
authority.

## Adapter protocol

The fixed command receives one request object. The request includes a
`hostExecution` object with the selected operation and the constant
`HOST_CONTROLLED` side-effect policy.

For `native-spawn`, return an object containing `status` and optional
`checkpoints` and `blockers`. Valid statuses are `STARTED`, `RUNNING`,
`COMPLETED`, `FAILED`, `CANCELLED`, and `TIMED_OUT`.

For `provider-execute`, return an object containing `status`, `model`, and a
`checkpoint`; optional `evidenceRefs` and `blockers` are accepted. The model
must match the AE assignment. Valid statuses are `EXECUTED`, `FAILED`,
`CANCELLED`, and `TIMED_OUT`.

The adapter owns authentication and provider/device/browser details. Do not
put credentials in the manifest, request, response, repository, or receipts.
Use `environmentKeys` only for explicitly approved host variables.

## Example integration

```python
from engine.host_execution import SubprocessNativeAgentTransport
from engine.native_agent_bridge import NativeAgentBridge

transport = SubprocessNativeAgentTransport(host_manifest)
bridge = NativeAgentBridge(native_bridge_manifest, transport)
result = bridge.spawn(assignment, slot, expected_policy=policy)
```

The CLI form is useful for a host smoke check:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/run_host_adapter.py \
  --manifest /absolute/path/host-execution-manifest.json \
  --request /absolute/path/request.json
```

The status command probes the executable without launching it:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/host_integration_status.py \
  --host-execution-manifest /absolute/path/host-execution-manifest.json
```

This gateway makes the source contracts executable when a real host adapter is
configured. It does not bundle provider credentials, implement every AI-tool
API, or prove browser/device/deployment behavior by itself.
