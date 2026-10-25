# Install BuildCrew

BuildCrew is distributed as a plugin containing the `buildcrew` skill. Use the
native plugin installer when your host supports it; use manual installation
when it does not.

## Codex plugin

After publishing this repository as a plugin marketplace source:

```bash
codex plugin marketplace add idhavaltank/buildcrew
codex plugin add buildcrew@buildcrew
```

Restart the Codex desktop app or start a new CLI thread after installation.
Review and trust lifecycle hooks if a future BuildCrew release adds them.

## Manual macOS/Linux installation

```bash
mkdir -p ~/.codex/skills/buildcrew
cp -R skills/buildcrew/* ~/.codex/skills/buildcrew/
```

## Manual Windows PowerShell installation

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills\buildcrew"
Copy-Item "skills\buildcrew\*" "$HOME\.codex\skills\buildcrew" -Recurse -Force
```

## Project-local installation

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
