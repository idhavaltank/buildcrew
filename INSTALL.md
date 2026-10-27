# Install BuildCrew

BuildCrew is an installable plugin package containing a portable engineering
skill. Each AI coding host has its own way to load plugin packages, skills,
extensions, or project instructions. Choose the installation method that
matches the AI coding tool you use.

Follow the installation steps in order: install BuildCrew, configure global
Codex-level every-input activation, start a fresh session, and verify the setup. The
one-time configuration prompt is available in
[README.md](README.md#step-2-configure-buildcrew-globally).

## Step 1: Install BuildCrew in your AI coding tool

### Download the plugin ZIP

The latest verified ZIPs are published at the
[GitHub Releases page](https://github.com/idhavaltank/buildcrew/releases/latest).
For Codex, download `buildcrew-v<version>-agent-plugin.zip`, extract it, and
use the extracted `buildcrew/` directory as a local plugin source. In Codex
Desktop, add that directory through the local plugin/marketplace flow, then
restart Codex and review/trust its bundled `UserPromptSubmit` hook. Codex's
local marketplace supports a plugin folder with `plugin.json`, `skills/`, and
optional `hooks/` at the plugin root.

For a manual host installation, extract the ZIP and copy the extracted
`buildcrew/` directory to the host's plugin directory. Do not copy the ZIP
itself into a skill directory, and do not copy the repository's `.git`, `docs`,
or release output. Follow the host's plugin-directory rules when they differ.

After the host loads the extracted plugin, run the one-time global activation
prompt in [README.md](README.md#step-2-configure-buildcrew-globally), start a
new session, and verify with an ordinary request that does not mention
BuildCrew.

#### Ready-to-paste ZIP installation prompt

Replace `[ZIP_FILE_PATH]` with the path to the ZIP you already downloaded, then
paste this into the host where BuildCrew should be installed:

```text
I have already downloaded the BuildCrew ZIP file here:
[ZIP_FILE_PATH]

Install BuildCrew globally for this host, not for only one project. Use the local ZIP file above; do not download another copy.

Extract the ZIP into a temporary location and validate that the extracted buildcrew/ directory contains plugin.json, skills/, and hooks/. Install that extracted buildcrew/ plugin directory using this host's supported global plugin or local-marketplace installation method.

Do not copy files into the current project, do not install the ZIP as a skill directory, and preserve unrelated host settings. If the ZIP path is missing, invalid, or ambiguous, ask me for the exact path before changing anything.

Review the bundled hooks/hooks.json and UserPromptSubmit hook before enabling it. If this host cannot install a local ZIP/plugin globally, explain the exact limitation and give me the host-specific manual location instead of claiming success.

After installation, configure BuildCrew as the global engineering workflow for every input across every project in this host. Run the one-time global activation and keep its receipt outside project folders. Then tell me what was installed, where, whether the hook is trusted and active, and whether I should start a new session.

Do not modify project files. Do not claim that BuildCrew is persistent unless you can verify the global installation and activation.
```

### Codex plugin

After the GitHub repository is registered as a plugin marketplace source:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Restart the Codex application or start a new CLI thread after installation.
Review and trust the plugin's bundled `UserPromptSubmit` hook when Codex asks;
Codex does not automatically trust plugin hooks. The hook routes every input
through BuildCrew before the model responds.

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

Then configure global activation with the packaged, reversible installer:

```bash
python3 -B scripts/configure_global_codex.py install
python3 -B scripts/configure_global_codex.py verify
```

The installer updates only a marked block in `~/.codex/AGENTS.md`, stores its
receipt and rollback copy under `BUILDCREW_HOME` (or the default external
BuildCrew data directory), and refuses an active controller conflict unless
the developer explicitly supplies `--allow-controller-conflict`.

Copy only the BuildCrew package contents when installing from a clean plugin or
skill archive. Do not copy repository `.git`, `.github`, `docs`, generated
state, or release output into an installation.

## Step 2: Configure BuildCrew globally for every user input

After installation, use the one-time setup prompt in
[README.md](README.md#step-2-configure-buildcrew-globally). It configures the
Codex host to load BuildCrew for every input across repositories when the host
supports global instructions, lifecycle hooks, or an equivalent mechanism. If
the host cannot support persistent global activation, it must report that
limitation.

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

## Step 3: Start a fresh session and verify global activation

Reload Codex or start a new session, open any repository, then send an ordinary
request without naming BuildCrew:

```text
Inspect this project and explain the relevant existing patterns before making any changes. Do not edit files yet.
```

Verify that the response follows the BuildCrew workflow and reports what it inspected.
Use the packaged verifier when testing a local installation:

```bash
python3 -B scripts/configure_global_codex.py verify
```

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

Then verify that the host loaded `SKILL.md`, the global activation receipt exists,
the per-project onboarding marker was created in the approved external state
root, and later inputs use the normal bounded memory workflow. A setup response
alone is not proof of persistent activation.

## Uninstall

Remove the installed BuildCrew plugin package or its `buildcrew` skill
directory from the location used by your host. For a plugin installation, use
the host's plugin removal command. Existing memory and continuity data are not
removed automatically.
