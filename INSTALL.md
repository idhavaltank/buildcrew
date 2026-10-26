# Install BuildCrew

BuildCrew is an installable plugin package containing a portable engineering
skill. Each AI coding host has its own way to load plugin packages, skills,
extensions, or project instructions. Choose the installation method that
matches the AI coding tool you use.

Follow the installation steps in order: install BuildCrew, configure the host for
every-input activation, start a fresh session, and verify the setup. The
one-time configuration prompt is available in
[README.md](README.md#step-2-configure-buildcrew-for-every-user-input).

## Step 1: Install BuildCrew in your AI coding tool

### Codex plugin

After the GitHub repository is registered as a plugin marketplace source:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Restart the Codex application or start a new CLI thread after installation.

### Claude Code

Project-local installation from the repository root:

```bash
mkdir -p .claude/skills/buildcrew
cp -R SKILL.md config engine references schemas scripts .claude/skills/buildcrew/
```

For a global installation, copy the same files into
`~/.claude/skills/buildcrew/` and start a new session.

### Cursor, Windsurf, Cline, Kiro, Zed, and Copilot Chat

Use the host's repository-instruction support to load `AGENTS.md`, or copy the
package contents (`SKILL.md`, `config/`, `engine/`, `references/`, `schemas/`,
and `scripts/`) into the host's native skill or rules directory.
The workflow remains provider-neutral; the host controls how it loads the
instructions.

### Gemini CLI, Antigravity, OpenCode, and other hosts

Use the host's native plugin or skill installer when available and provide the
BuildCrew plugin package or its `buildcrew` skill directory. If the host supports neither the
Agent Skills format nor `AGENTS.md`, it needs a small host-specific adapter.
BuildCrew does not claim a native one-command installer for every host.

### Manual Codex installation on macOS/Linux

```bash
mkdir -p ~/.codex/skills/buildcrew
cp -R SKILL.md config engine references schemas scripts ~/.codex/skills/buildcrew/
```

Copy only the BuildCrew package contents when installing from a clean plugin or
skill archive. Do not copy repository `.git`, `.github`, `docs`, generated
state, or release output into an installation.

## Step 2: Configure BuildCrew for every user input

After installation, open the project in your AI coding tool and use the
one-time setup prompt in [README.md](README.md#step-2-configure-buildcrew-for-every-user-input).
It configures the host to load BuildCrew for every user input when the host supports
project instructions, hooks, or an equivalent mechanism. If the host cannot
support persistent activation, it must report that limitation.

## Host runtime configuration for multi-agent execution

In this guide, **host** means the AI coding tool that loads and runs
BuildCrew—for example, Codex, Claude Code, Cursor, Windsurf, Gemini
CLI, or another supported tool.

Installing BuildCrew provides the workflow, agent roles, routing rules, and evidence
contracts. It does not automatically create live sub-agents by itself. Real
multi-agent execution requires the selected host to be configured with a
runtime that can start agents and return their results.

| Setup | What happens |
| --- | --- |
| BuildCrew skill loaded only | The host’s lead AI follows the BuildCrew workflow by itself. |
| Host runtime configured | BuildCrew can select and spawn the required specialist agents automatically. |
| Host runtime unavailable | BuildCrew continues with the lead AI and reports that separate agent execution is unavailable. |

Live multi-agent execution requires an approved native-agent executor (the
component that starts an agent), a model provider (the AI model service), a
runtime coordinator (the component that manages agent assignments), a project
scope, and execution permissions. Configure each AI coding tool separately;
installing BuildCrew in one host does not configure it in another. BuildCrew never claims
that agents ran unless the host returns valid execution evidence.

For example, an architecture request may use the Orchestrator, Context
Coordinator, Knowledge Retriever, Codebase Analyst, Solution Architect,
Independent Reviewer, and Final Validator. If the host cannot spawn agents,
the lead AI follows the same workflow without pretending that separate agents
ran.

## Step 3: Start a fresh session and verify activation

Reload the project or start a new session, then send an ordinary request
without naming BuildCrew:

```text
Inspect this project and explain the relevant existing patterns before making any changes. Do not edit files yet.
```

Verify that the response follows the BuildCrew workflow and reports what it inspected.

## Shared context storage

To share memory, continuity, progress, and worklogs between supported tools,
configure one application-data location before launching them:

```bash
export BUILDCREW_HOME="/absolute/path/buildcrew-data"
```

Inspect the effective paths with:

```bash
python3 -B scripts/storage_paths.py
```

## Optional verification detail

After completing the required installation steps, you can perform a deeper
workflow check with:

```text
Use BuildCrew to explain its workflow and inspect this project without editing files.
```

Then verify that the host loaded `SKILL.md`, the onboarding marker was created
in the approved external state root, and later inputs use the normal bounded
memory workflow. A setup response alone is not proof of persistent activation.

## Uninstall

Remove the installed BuildCrew plugin package or its `buildcrew` skill
directory from the location used by your host. For a plugin installation, use
the host's plugin removal command. Existing memory and continuity data are not
removed automatically.
