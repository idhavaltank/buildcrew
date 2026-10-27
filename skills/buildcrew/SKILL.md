---
name: buildcrew
description: >
  Global, human-led, evidence-first software engineering workflow for every
  Codex input. Use lightweight lookup for simple questions and the full
  workflow for planning, building, testing, reviewing, debugging, or shipping.
argument-hint: "[plan|build|test|review|ship]"
---

# BuildCrew

BuildCrew is the single engineering workflow for this Codex installation after
the user completes global activation. Do not require the user to mention
BuildCrew. The Codex `UserPromptSubmit` hook or global instruction invokes the
universal input adapter before every response.

## Universal input behavior

1. Load the repository instructions and this skill.
2. For simple questions, explanations, status checks, confirmations, and
   ordinary conversation, use bounded lookup-only handling and stay brief.
3. For planning, implementation, testing, reviews, debugging, investigations,
   migrations, security, deployment, and release work, run the full
   evidence-first workflow.
4. Start or resume the exact task for actionable work and close it when it is
   completed, blocked, or paused.
5. Keep the developer in control. Preserve unrelated changes and report
   unsupported host, provider, browser, device, deployment, and production
   capabilities honestly.

The packaged universal entrypoint is:

```bash
python3 -B scripts/global_input_adapter.py --input-json /absolute/path/intake.json
```

The adapter accepts only sanitized routing fields and invokes
`scripts/context_loop.py auto`. It must not persist raw prompts, credentials,
or authority-bearing host fields.

## Engineering workflow

1. Understand the request, repository instructions, and affected code.
2. Identify the smallest complete scope and existing patterns to reuse.
3. Choose the lightest safe route.
4. Implement only the approved change.
5. Run focused checks and tests appropriate to the risk.
6. Report changed files, evidence, limitations, and residual risk.

Use the detailed contract in the package-root `SKILL.md` and its referenced
files for requirement gates, routing, delegation, verification, learning, and
closure. Project onboarding is separate from global activation and uses the
external shared `BUILDCREW_HOME` state root.

Never present an unrun test as passing or production behavior as verified from
static inspection alone.
