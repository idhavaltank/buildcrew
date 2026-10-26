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

### Install BuildCrew across AI coding hosts

BuildCrew is a portable skill, but installation commands are host-specific.
The reusable workflow lives in `skills/buildcrew/SKILL.md`; the plugin
manifests and host adapters determine how each AI coding tool loads it.

Choose one installation path for your AI coding host. Do not run every section.
After installation, start a new session and use the verification prompt below.

#### Codex plugin

From the published plugin marketplace:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Start Codex and open a new thread. This release is instruction-only and does
not require lifecycle hooks.

#### Claude Code

From the BuildCrew repository root, use the Claude Code skill directory for a
project or for your user account:

```bash
mkdir -p .claude/skills/buildcrew
cp -R skills/buildcrew/* .claude/skills/buildcrew/
```

For a global installation, use `~/.claude/skills/buildcrew/` instead of the
project-local `.claude/skills/buildcrew/` path, then start a new session.

#### Cursor, Windsurf, Cline, Kiro, Zed, and Copilot Chat

These hosts can use the included repository instructions where supported. From
the BuildCrew repository root:

```text
Copy AGENTS.md into the project root, then open a new agent session.
```

For a host-native skill or rules directory, copy the contents of
`skills/buildcrew/` into that host’s supported skill/rules location.

#### Gemini CLI, Antigravity, OpenCode, and other hosts

Use the host’s native extension or skill installer when available and provide
the `skills/buildcrew/` directory. If the host does not support the Agent
Skills format or `AGENTS.md`, it needs a small host-specific adapter; the
workflow itself remains the same. This repository currently provides the
portable skill and `AGENTS.md` fallback; it does not claim a native one-command
installer for every host.

#### Codex manual installation

##### macOS/Linux

```bash
mkdir -p ~/.codex/skills/buildcrew
cp -R skills/buildcrew/* ~/.codex/skills/buildcrew/
```

##### Windows PowerShell

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills\buildcrew"
Copy-Item "skills\buildcrew\*" "$HOME\.codex\skills\buildcrew" -Recurse -Force
```

For one Codex project only, copy the skill to:

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

Read the complete <a href="https://idhavaltank.github.io/buildcrew/" target="_blank" rel="noopener noreferrer">BuildCrew documentation ↗</a>
for setup, concepts, workflows, examples, reference, FAQ, and contribution guidance.

### Ownership and contributors

- **Owner:** <a href="https://github.com/idhavaltank" target="_blank" rel="noopener noreferrer">Dhaval Tank ↗</a>
- **Mobile Engineering Collaborator:** <a href="https://github.com/jayzala-dev" target="_blank" rel="noopener noreferrer">Jay Zala ↗</a>

BuildCrew is open to community suggestions. Anyone can fork the repository and
open a pull request; changes are reviewed by Dhaval Tank before they are
merged into the official `main` branch.

### Project status

This is the public release starter package. Before publishing a production
release, select and add the project license, connect the final repository URL,
and validate the plugin on each target host.
