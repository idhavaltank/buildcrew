# Daily plans, worklogs, and reports

This reference defines the standalone daily continuity and reporting duties for
this workflow. It uses the bundled worklog writer and does not depend on another
skill or provider.

## At substantive work start

Resolve the current date in the user's local timezone. Look for a plan or prior
context in this order:

1. `daily-report/YYYY-MM-DD/` in the workspace. Read the dated plan/report if
   present.
2. `plans/` in the workspace. Read a relevant legacy plan only when no dated
   plan covers the work. An older plan is context, not current approval or scope.
3. The configured date-wise worklog at `<root>/YYYY-MM-DD/worklog.md`.

Resolve `<root>` through [shared storage configuration](storage-configuration.md),
including `BUILDCREW_HOME` and explicit component settings.
An explicit `--root`
selects a teammate's configured location. Do not hardcode a user- or machine-
specific path. If a location cannot be read, record that limitation and
continue with available context.

For actionable work, maintain one compact packet for the whole session. Include
the request and scope, request type, date, stable session ID, repository or
project when known, plan/worklog paths checked, selected skills with a brief
reason for each, decisions, blockers, evidence and verification, and the four
progress groups below. Select skills based on the task and explain the routing
briefly; do not list unrelated skills. Reuse the same session ID for every
update to this task. Use the active session ID when available. Otherwise make a
stable date-and-topic identifier that remains unchanged for the session.

Keep these progress groups separate:

- **Complete**: finished work supported by evidence.
- **In progress**: active work that is not finished.
- **Todo**: agreed remaining work.
- **New / off-plan**: newly discovered or requested work outside the current
  plan.

A plan does not prove that work happened, set hours, or grant approval. Record
only actions, outcomes, and time supported by user statements or reliable
evidence.

## Worklog updates

After substantive work, update the date-wise worklog when the task is completed,
blocked, or explicitly paused. Use `scripts/update_daily_log.py` with the
packet's stable session ID and all four progress groups. The writer upserts the
session section by ID, so later updates replace that session's section instead
of creating duplicates. Pass `--root` for an explicitly selected root or rely
on the writer's shared storage configuration. The script
uses the host's local date and time by default; pass `--date` (and `--time` if
needed) from the user's resolved timezone when they differ. The script does
not provide a quiet flag; skip logging for simple informational inputs unless
they change a tracked plan, decision, blocker, or next action.

Example:

```text
python3 scripts/update_daily_log.py \
  --title "Short task title" \
  --session-id "stable-session-id" \
  --date "YYYY-MM-DD" \
  --summary "Outcome and selected skill reasons" \
  --complete "Verified requested behavior" \
  --in-progress "Remaining active work" \
  --todo "Agreed next action" \
  --new-item "New work outside the plan" \
  --root "/path/to/teammate/worklogs"
```

Repeat `--summary`, `--complete`, `--in-progress`, `--todo`, or `--new-item`
for multiple entries. Omit `--root` when the configured root should be used.
Check the command result and reported path. A failed write remains a visible
blocker; do not claim the update succeeded. Do not put secrets, private user or
customer data, raw transcripts, unredacted logs, or unverified agent opinions
in the worklog.

## Requested plan or end-of-day report

When asked to prepare a daily plan or end-of-day report, assemble a complete,
self-contained draft from available evidence. Include the date, worklog path,
relevant stable session IDs, the four progress groups, decisions, blockers,
verification performed and its results, and a concise summary. A plan must mark
proposed or pending work as such. An end-of-day report must include only
supported work and verified outcomes. Preserve wording the user has approved
verbatim; do not invent tasks, results, hours, or approvals.

Create the requested draft locally at the requested location when known. If no
location is specified, provide it in the response. Do not save or post it to an
external service unless the user explicitly requested that write or posting
action. If an external write requires approval, prepare the full reviewable
draft first, then request only the required approval. Report local file creation
separately from external saving or posting. A draft or worklog entry does not
prove publication, delivery, or runtime behavior.
