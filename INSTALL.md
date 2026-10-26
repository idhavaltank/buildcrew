# Install BuildCrew

BuildCrew is a portable skill distributed inside an installable plugin. The
workflow is shared across AI coding hosts, but each host has its own way to
load skills, plugins, extensions, or repository instructions.

Choose one installation path for your host. Do not run every section. All
manual commands below should be run from the root of a BuildCrew checkout
unless the section says otherwise. After installation, start a new session and
run the verification prompt at the end of this guide.

## Codex plugin

After publishing this repository as a plugin marketplace source:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Restart the Codex desktop app or start a new CLI thread after installation.
This release is instruction-only and does not require lifecycle hooks.

## Claude Code

Project-local installation:

```bash
mkdir -p .claude/skills/buildcrew
cp -R skills/buildcrew/* .claude/skills/buildcrew/
```

For a global installation, copy the same files into
`~/.claude/skills/buildcrew/`, then start a new session.

## Cursor, Windsurf, Cline, Kiro, Zed, and Copilot Chat

Copy the included `AGENTS.md` from the BuildCrew checkout into the project root
when the host supports
repository instructions. Otherwise, copy `skills/buildcrew/` into the host’s
native skill or rules directory.

## Gemini CLI, Antigravity, OpenCode, and other hosts

Use the host’s native extension or skill installer when available and provide
the `skills/buildcrew/` directory. Native commands and directories vary by
host. If a host supports neither the Agent Skills format nor `AGENTS.md`, add a
small host-specific adapter that loads the same `SKILL.md` instructions. This
repository does not currently claim a native one-command installer for every
host.

## Manual Codex installation on macOS/Linux

```bash
mkdir -p ~/.codex/skills/buildcrew
cp -R skills/buildcrew/* ~/.codex/skills/buildcrew/
```

## Manual Codex installation on Windows PowerShell

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills\buildcrew"
Copy-Item "skills\buildcrew\*" "$HOME\.codex\skills\buildcrew" -Recurse -Force
```

## Project-local Codex installation

Copy the `skills/buildcrew` directory into the project where it should apply:

```text
.codex/skills/buildcrew/
```

## Repository-instruction fallback

Hosts that read `AGENTS.md` can use the included file from the repository root.
Copy it into the target project or place it in the host's supported global
instructions directory.

## Verification

Start a new session and ask:

```text
Use BuildCrew to explain its workflow and plan a small change without editing files.
```

## Uninstall

Remove the installed `buildcrew` skill directory from the location used during
installation. For a Codex plugin installation, use the host's plugin removal
command or remove the plugin from its configured marketplace.
