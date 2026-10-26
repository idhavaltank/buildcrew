# Contributing to BuildCrew

Thanks for helping improve the skill. Please keep changes small, explain the
behavior they affect, and distinguish source validation from live host proof.

## Before you change code

1. Read `SKILL.md` and the references related to your change.
2. Trace the current implementation and tests before editing it.
3. Keep one lifecycle controller. Host adapters report capabilities and
   execution evidence; they do not grant authority or claim work completed.
4. Avoid tool-specific defaults in the core. Put host-specific behavior behind
   a bounded adapter or an explicit compatibility setting.
5. Do not add credentials, personal data, generated caches, local state, or
   archives to the source package.

## Validate your change

Run focused tests for the behavior you changed, then the package suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -q
```

When changing packaging, manifests, or release instructions, also run the
candidate release build described in [RELEASE.md](RELEASE.md). A passing unit
test does not prove live behavior in Codex, Claude Code, Cursor, or another host.
Record which host and version you exercised when making a host claim.

## Propose a change

In a pull request, explain the user-facing behavior, why the change is needed,
the files touched, verification performed, and any unverified host boundaries.
Include a focused test when it guards a real contract or failure mode. Update
the relevant reference when the workflow or configuration changes.

Contributions to this repository are distributed under the [MIT license](LICENSE).
Do not submit material you cannot contribute under those terms.
