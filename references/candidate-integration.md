# Optional candidate integration

The teammate `autonomous-engineering` project is an optional source-only
projection provider. It is not a dependency of this skill and it is never a
second lifecycle controller.

## Accepted source boundary

The adapter accepts either:

- the candidate project root containing `src/autonomous_engineering`; or
- its `skills/autonomous-engineering` directory.

It computes a deterministic source fingerprint over the candidate source,
schemas, skill policy, implementation status, and project metadata. The
fingerprint binds shadow evidence to the exact candidate snapshot.

Before projecting a request, the adapter runs the candidate's read-only status
projection and requires:

- `mode=SOURCE_ONLY`;
- `installed=false`;
- `active=false`; and
- `legacyRuntime=UNTOUCHED`.

Any other result is `INVALID`, not a usable candidate recommendation.

## Projection mapping

The adapter invokes only these candidate CLI projections:

1. `status`
2. `classify`
3. `diagnosis-dispatch-plan` for routes that own a requirement packet
4. `explain-tier`

Candidate values are mapped before reconciliation:

| Candidate value | Hybrid value |
| --- | --- |
| `READY_FOR_SHADOW` / available source | `AVAILABLE` |
| no supplied source | `CANDIDATE_UNAVAILABLE` |
| malformed or unsafe projection | `INVALID` |
| `SMALL` | `LIGHTWEIGHT` |
| `MEDIUM`, `LARGE` | `NORMAL` |
| `COUPLED` | `COUPLED` |
| `T0`, `T1` | `LOW` |
| `T2` | `MEDIUM` |
| `T3` | `HIGH` |

The candidate's diagnostic capabilities and required evidence are carried as
proposals only. Candidate `RouteKind`, status names, and tier names do not
enter the canonical packet unchanged.

## Reconciliation

general remains authoritative:

- the general route wins route conflicts;
- complexity and risk may only move upward;
- risk reasons, required capabilities, and required coverage are unioned;
- route conflicts are retained as `CONFLICT` and cannot reach closure;
- invalid candidate output is ignored for execution and blocks hybrid coverage;
- candidate shadow output cannot create state, lanes, writers, receipts,
  reviewers, worklogs, activation, or rollback.

Shadow reports redact credential assignments and bearer values. Do not supply
raw secrets, tokens, cookies, private keys, or customer data to a projection
command; credential-bearing prompts are rejected before candidate invocation.

Run a candidate-aware shadow report with:

```text
python3 scripts/classify_hybrid_shadow.py \
  --corpus tests/fixtures/shadow \
  --candidate-root /path/to/autonomous-engineering-employee-handoff
```

Omit `--candidate-root` to exercise the standalone fallback. The report must
show `CANDIDATE_UNAVAILABLE` and an empty side-effect list in that mode.

## Deliberate non-goals

This adapter does not copy the candidate source, launch its dispatcher,
install its package, create Git lanes, select live reviewers, migrate legacy
state, activate a workflow, or perform rollback. Those capabilities remain
behind the general controller and its separately approved promotion process.

The supplied candidate currently contains developer-specific legacy path
constants in its own source-only boundary module. The adapter does not treat
those constants as active host configuration or use them for general
protection. This remains a portability residual and must be resolved before
any candidate activation or cross-host promotion is considered.
