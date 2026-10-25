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

### Documentation showcase

Visit the [BuildCrew GitHub Pages site](https://idhavaltank.github.io/buildcrew/)
for the visual overview, daily workflow, trust model, and contributor credits.

### Ownership and contributors

- **Owner:** [Dhaval Tank](https://github.com/idhavaltank)
- **Mobile-side contributor:** [Jay Zala](https://github.com/jayzala-dev)

### Project status

This is the public release starter package. Before publishing a production
release, select and add the project license, connect the final repository URL,
and validate the plugin on each target host.
