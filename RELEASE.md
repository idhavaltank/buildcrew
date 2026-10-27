# Release process

## Release metadata

`config/plugin.json` is the version, publisher, and license record. Update its
`version` and `CHANGELOG.md` together. Keep the `SKILL.md` frontmatter name,
source directory name, and plugin name equal to `buildcrew`.

The current public package version is `0.1.0`. The changelog retains the
earlier candidate milestones for traceability. Existing continuity data can be
reused by setting `BUILDCREW_CONTINUITY_ROOT` to its current directory.

## Build a candidate

1. Work from a clean source export. The release builder rejects `.git`,
   `__pycache__`, bytecode, local state, secrets, and unknown top-level files.
   Public repository files under `.github/`, `docs/`, and the public metadata
   files are allowed in the GitHub source tree but are intentionally excluded
   from the installable plugin or skill archives. A Git-hosted release can export its
   chosen tag to a temporary `buildcrew/` directory. Do not package
   directly from a dirty checkout.
2. Run the package suite from that source directory:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -q
   ```

3. Build into a new directory outside the source root:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/build_release.py \
     --source-root . \
     --output-dir /absolute/path/to/new-release-directory
   ```

The output contains:

- A direct skill ZIP.
- A portable Agent Plugin ZIP.
- A Claude Code plugin ZIP.
- `SHA256SUMS`.
- `release-report.json`.

The builder checks plugin-package source parity, plugin layouts, embedded manifests, file
contents, and hashes before publishing the output directory. It does not
install or activate a plugin.

## Downloadable ZIP distribution

Pushing a version tag such as `v0.1.0` starts the GitHub Actions release workflow.
It builds and publishes verified ZIP assets to the GitHub Release page:

`https://github.com/idhavaltank/buildcrew/releases/latest`

Users should download the `buildcrew-v<version>-agent-plugin.zip` asset when the
host supports the portable plugin format. The release also contains the direct
skill and Claude Code archives, plus `SHA256SUMS` and `release-report.json`.

The ZIP is a distribution artifact, not a project archive. Extract it to a
temporary directory, install the extracted `buildcrew/` plugin directory using
the host's local-plugin workflow, then start a fresh host session. Hosts that
support lifecycle hooks must review and trust the bundled `hooks/hooks.json`
before expecting automatic every-input routing.

## Verify and distribute

### Archive verification

From the output directory, run `sha256sum -c SHA256SUMS` where that utility is
available. Inspect `release-report.json` and the archive file lists. The
direct skill ZIP has one `buildcrew/` directory with `SKILL.md` at its
root. The portable plugin ZIP has `plugin.json` and
`skills/buildcrew/SKILL.md` under its plugin root. The Claude plugin
ZIP has `.claude-plugin/plugin.json` and the same nested skill. Host-specific
plugin installation follows that host's current documentation.

### Host validation

If Claude Code is available, extract the Claude archive to a temporary folder
and run `claude plugin validate <extracted-plugin-root> --strict`. Also test
discovery in a fresh host session before claiming a particular host works.
The source/package checks and static manifest validation do not prove live
agent spawning, provider access, browser/device execution, installation, or
marketplace acceptance.

### Publishing checks

The intended public repository is
`https://github.com/idhavaltank/buildcrew`, with documentation at
`https://idhavaltank.github.io/buildcrew/`. Confirm the repository and
Pages settings before publishing. Publishing archives, tags, or marketplace
listings is a separate action from building candidates.
