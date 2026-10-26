# Worktree source routing

Before mutation AE inspects repository, worktree, branch/detached state, SHA,
dirty baseline, lane and writer. Read-only work stays in the current checkout.
For an owner-authorized `FAST_DAILY` mobile source change in the current
checkout, do not create a new worktree solely for this advanced inspection;
retain one writer, an explicit target allowlist, and focused verification.
This exception does not apply to shared, cross-repository, release, or
high-risk work.
For a new worktree, follow the repository's branch naming convention. When it
has none, use `task/<task-slug>[-N]` or a name chosen by the user.
Names are checked against origin, local refs and active worktrees. Duplicates or
an unverifiable origin block creation without side effects. Paths are supplied
by host configuration and never encoded as teammate-specific absolute paths.
