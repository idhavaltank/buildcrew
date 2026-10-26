# BuildCrew

## You Lead. AI Assists.

BuildCrew is an installable AI software engineering plugin containing a
repeatable skill for understanding projects, planning changes, building safely,
testing the result, and reporting evidence.

It is designed for daily development—not just demos. BuildCrew helps an AI
agent inspect the repository before editing, reuse existing patterns, keep
routine work lightweight, and make remaining uncertainty visible.

### Why it is useful

Typical AI coding often follows:

```text
prompt → generated code → confidence
```

BuildCrew encourages:

```text
instruction → context → reuse → smallest change → checks → evidence
```

### Install in Codex

From a published plugin marketplace:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Start Codex, review any requested hooks, and open a new thread. This starter
package is instruction-only and does not require lifecycle hooks.

### Manual installation

#### macOS/Linux

```bash
mkdir -p ~/.codex/skills/buildcrew
cp -R skills/buildcrew/* ~/.codex/skills/buildcrew/
```

#### Windows PowerShell

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills\buildcrew"
Copy-Item "skills\buildcrew\*" "$HOME\.codex\skills\buildcrew" -Recurse -Force
```

For one project only, copy the skill to:

```text
your-project/.codex/skills/buildcrew/
```

Start a new agent session after installation.

### Example prompts

```text
Use BuildCrew to plan this feature before changing files.
Use BuildCrew build mode to implement the approved plan.
Use BuildCrew review mode to inspect this diff and report evidence and risk.
```

### Documentation

Read the complete [BuildCrew documentation](https://idhavaltank.github.io/buildcrew/)
for setup, concepts, workflows, examples, reference, FAQ, and contribution
guidance.

| Guide | Purpose |
| --- | --- |
| [Get started](https://idhavaltank.github.io/buildcrew/#get-started) | Run your first BuildCrew task. |
| [Installation](https://idhavaltank.github.io/buildcrew/#installation) | Install the plugin or standalone skill. |
| [Core concepts](https://idhavaltank.github.io/buildcrew/#concepts) | Understand the plugin, skill, modes, and evidence loop. |
| [Workflows](https://idhavaltank.github.io/buildcrew/#workflows) | Plan, build, test, review, and ship. |
| [Examples](https://idhavaltank.github.io/buildcrew/#examples) | See practical prompts and expected behavior. |
| [Reference](https://idhavaltank.github.io/buildcrew/#reference) | Prompt structure and completion response guidance. |

### Ownership and contributors

- **Owner:** [Dhaval Tank](https://github.com/idhavaltank)
- **Mobile Engineering Collaborator:** [Jay Zala](https://github.com/jayzala-dev)

BuildCrew is open to community suggestions. Anyone can fork the repository and
open a pull request; changes are reviewed by Dhaval Tank before they are
merged into the official `main` branch.

### Project status

This is the public release starter package. Before publishing a production
release, select and add the project license, connect the final repository URL,
and validate the plugin on each target host.
