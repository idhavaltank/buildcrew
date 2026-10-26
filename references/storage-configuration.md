# Shared storage configuration

All supported AI tools use the same storage resolver in
`scripts/storage_paths.py`. Set one absolute application-data location in the
environment used to launch each tool:

```bash
export BUILDCREW_HOME="/absolute/path/buildcrew-data"
```

In PowerShell, use:

```powershell
$env:BUILDCREW_HOME = "C:\Users\YourName\buildcrew-data"
```

Inspect the effective paths without creating directories or moving data:

```bash
python3 -B scripts/storage_paths.py
```

## Storage layout

The configured home supplies these paths:

```text
buildcrew-data/
  memory/
    MEMORY.md
    autonomous-engineering/
      MEMORY.md
      route-map.json
      module-registry.json
      learning-store.json
      project-memory/<project-repository-digest>/MEMORY.md
    extensions/ad_hoc/notes/
  progress/
  continuity/
  worklogs/
  worklogs-fallback/
```

Memory indexes remain optional. Lookup reports missing context and continues
with current source when no index exists. Paths are initialized by the owning
write operation, not by inspecting configuration. The `autonomous-engineering`
namespace preserves the existing memory formats and route maps; it does not
depend on a particular AI tool.

## Default application-data location

When no path is configured, every host uses the operating system's
application-data location:

| Operating system | Application home |
| --- | --- |
| Linux and other Unix systems | `$XDG_DATA_HOME/buildcrew`, or `~/.local/share/buildcrew` when XDG is unset or relative |
| macOS | `~/Library/Application Support/buildcrew` |
| Windows | `%LOCALAPPDATA%\buildcrew`, then `%APPDATA%\buildcrew`, then `~/AppData/Local/buildcrew` |

These are storage-path defaults. Runtime dependencies, locking support, and
host permissions still determine which operations can run on a given system.

## Override precedence and existing data

Path selection follows this order:

1. An explicit API or command-line path, such as `--memory-root`, `--store`,
   `--state-root`, or the progress/worklog `--root`.
2. A tool-neutral component setting: `BUILDCREW_MEMORY_ROOT`,
   `BUILDCREW_LEARNING_STORE`, `BUILDCREW_PROGRESS_ROOT`,
   `BUILDCREW_CONTINUITY_ROOT`, `BUILDCREW_WORKLOG_ROOT`, or
   `BUILDCREW_WORKLOG_FALLBACK_ROOT`. Use absolute paths when tools
   may start from different working directories.
3. `BUILDCREW_HOME`, which supplies the remaining component paths.
4. A short or legacy component setting, only when the shared home and corresponding
   tool-neutral setting are unset.
5. The neutral application-data path.

An explicit memory root also determines the default learning store under
`<memory-root>/autonomous-engineering/learning-store.json`. Supply an explicit
store path as well to intentionally use a different learning file. A neutral
memory-root setting also keeps the store with that memory root unless the
neutral learning-store setting is supplied.

Worklog safety settings use `BUILDCREW_PROTECTED_ROOTS` and
`BUILDCREW_WORKLOG_ALLOWED_ROOTS`. Each accepts paths separated by the
operating system's path separator. The protected roots setting also applies to
project-context persistence. When old and new safety settings are both set,
protected roots are combined and a worklog root must satisfy both allowlists.

### Reuse existing Codex data explicitly

The short setting `BUILDCREW_CONTINUITY_ROOT` selects a continuity directory when the
shared home and neutral continuity setting are unset. The older settings `CODEX_MEMORY_ROOT`,
`AUTONOMOUS_ENGINEERING_LEARNING_STORE`,
`AUTONOMOUS_ENGINEERING_PROGRESS_ROOT`,
`CODEX_DAILY_WORKLOG_ROOT`, and `CODEX_DAILY_WORKLOG_FALLBACK_ROOT` remain
accepted when the shared home is unset. `CODEX_PROTECTED_ROOTS`,
`CODEX_WORKLOG_ALLOWED_ROOTS`, and
`AUTONOMOUS_ENGINEERING_PROTECTED_ROOTS` remain safety-setting aliases.

To reuse data from an older installation, point the appropriate neutral
component settings or explicit command-line paths at its existing directories.
For continuity data, set `BUILDCREW_CONTINUITY_ROOT` to the existing
absolute continuity directory. Existing session files keep their versioned
format; the setting changes only which directory is read and written.
Previous continuity environment keys are no longer read. Keep the old
directory until a `resume` using the neutral setting succeeds.
For example, to reuse its memory without changing the other defaults:

```bash
export BUILDCREW_MEMORY_ROOT="$HOME/.codex/memories"
```

The resolver never selects `~/.codex` automatically, even if that directory
exists. It never creates an old tool's directory merely by inspecting paths.

Existing files are never copied, moved, merged, or deleted automatically.
Setting a new shared home selects that location; it does not migrate old data.
Set the matching memory, continuity, progress, and worklog paths when
continuing an existing task from another location.
Inspect the effective paths before changing a running project's configuration.
Project, repository, tenant, and access-scope checks still apply to all stored
knowledge.
