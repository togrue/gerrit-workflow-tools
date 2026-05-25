# `ger branch`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_branch.py` |
| **Requires** | Git only |

Manage branch-local Gerrit metadata in `.git/config`.

---

## Usage

```
ger branch <subcommand> [options]
```

---

## Subcommands

| Subcommand | Purpose |
|------------|---------|
| `show` | Print current branch Gerrit config (target override, push mode, reviewers) |
| `init [--target BRANCH] [--reviewers LIST]` | Write `gerritTarget` / `gerritReviewers` (no-op hint if both omitted) |
| `set-target <branch>` | Set `branch.<name>.gerritTarget` |
| `set-reviewers <list>` | Set `branch.<name>.gerritReviewers` |
| `infer-upstream` | Pick nearest remote-tracking branch, set upstream (`--yes` for non-TTY) |

Global: `--debug-log`, `-v` (reserved).

---

## Git config keys

```ini
[branch "feature/x"]
    gerritTarget = main
    gerritReviewers = alice,bob
```

`gerritTarget` is the **server branch name** (e.g. `main`), not `origin/main`.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| non-zero | Git/config errors (`GitError` via `handle_git_error`) |

---

## V1 scope delta

No changes required for v1 ([Version 1 Scope](../../Version%201%20Scope.md)).

---

## See also

- [`ger push`](push.md)
- [Configuration.md](../../Configuration.md#branch-local-branchname)
