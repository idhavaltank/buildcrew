# One-time project onboarding

Project onboarding is a per-repository context gate, not the global activation
mechanism and not a replacement for the normal every-input lookup. A host
configures BuildCrew globally once for each `(installation, host)` scope, then
invokes project onboarding once for each repository it opens.

```text
first input after install
  -> inspect_onboarding
  -> classify project state
  -> show the introduction once
  -> gather or inspect project context
  -> complete_onboarding
  -> normal bounded memory/context workflow
```

## User-led activation after installation

The [README setup prompt](../README.md#step-2-configure-buildcrew-globally)
gives a developer a one-time way to ask their AI coding tool to register
BuildCrew globally. The prompt itself applies to the current conversation.
Persistent cross-project activation requires a tool-supported global
instruction or lifecycle hook; skill files alone do not install one.

1. Install the skill using the tool's supported skill or plugin method. Send the
   README setup prompt once at the Codex level. The host must locate the
   installed `SKILL.md` and inspect existing persistent instructions before
   adding an BuildCrew rule. Follow the skill's activation checks, preserve the
   previous configuration for rollback, and verify that one controller owns
   the scope. If another lifecycle controller already owns that scope, report
   the conflict before changing it.
2. During setup or on the first input for a new repository, call `inspect_onboarding`
   with a stable installation, host, and project identity plus an approved
   external state root. If it returns `showIntro=true`, introduce the context
   step, classify the project, gather only the needed context, and call
   `complete_onboarding`. A `PENDING` marker suppresses repeat introductions
   while context gathering continues.
3. On later inputs, read the marker and call `scripts/context_loop.py auto`
   with a sanitized intake. Call `close` when actionable work completes, blocks,
   or pauses. Exact task identity lets another session resume it. Keep project
   context current through the ordinary verified learning flow; promote a
   candidate into canonical project memory only after the separate review gate.
4. After a reload or in a fresh session, open a different repository and send an
   ordinary request that does not name BuildCrew. Check the host's loaded global
   instruction or hook and both the global activation receipt and project marker.
   A setup response or installed file by itself is not proof that BuildCrew
   handled the new request.

If the host has no persistent global instruction or hook, report that the setup
applies only to the current conversation and give the developer the
host's supported manual invocation step. If the current session does not
refresh installed skills or instructions, follow that host's reload or new
session procedure before verification. A host that only discovers skills by
relevance cannot guarantee that BuildCrew runs on every input; report that limit
instead of treating discovery as activation.

## Project states

- `NEW_PROJECT`: no bounded source or project manifest was found. Ask for the
  purpose, requirements, stack, architecture, scope, and priorities.
- `EXISTING_WITHOUT_DOCUMENTATION`: implementation or manifests exist but no
  bounded project documentation was found. Inspect structure, dependencies,
  configuration, and execution paths, then save reusable context.
- `EXISTING_WITH_DOCUMENTATION`: implementation and documentation exist. Read
  the relevant documentation, validate it against current source, and then
  continue with the request.

## Persistence and repetition rules

The host must provide for project onboarding:

- an explicit external `state_root`;
- a stable `project_id`;
- a `host_id`; and
- an `installation_id` that changes when a new skill installation should
  receive a new first-interaction message.

The marker is written atomically below the external state root. It stores the
project classification and sanitized context only. It does not store raw
prompts, transcripts, credentials, or secrets.

The first inspection returns `showIntro=true` and the introductory message.
Later inspections in the same scope return `showIntro=false`, even while
onboarding is still `PENDING`. After the host has gathered enough context,
`complete_onboarding` marks the scope `COMPLETED`; future requests skip the
onboarding message and use the normal lifecycle.

If the host cannot persist the external marker, the result explicitly reports
`persistence=unavailable`. The host may suppress repetition for the current
process, but the skill cannot claim that the one-time decision survived a
restart.

## CLI

```bash
python3 -B scripts/project_onboarding.py inspect \
  /absolute/path/to/project \
  --state-root /absolute/path/to/external/state \
  --project-id my-project \
  --host-id my-ai-tool \
  --installation-id install-1
```

Completion accepts a sanitized JSON object containing only `summary`,
`decisions`, `references`, and `nextAction`:

```bash
python3 -B scripts/project_onboarding.py complete \
  /absolute/path/to/project \
  --state-root /absolute/path/to/external/state \
  --project-id my-project \
  --host-id my-ai-tool \
  --installation-id install-1 \
  --context-json /absolute/path/to/onboarding-context.json
```
