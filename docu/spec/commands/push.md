# `ger push`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_push.py` |
| **Requires** | Gerrit mode: target resolution, Change-Ids; attribute preview / REST strategies need `gerrit.webUrl` + credentials |

Push the **ready prefix** of the local stack to Gerrit (`refs/for/<target>`), or run plain **`git push`** in **vanilla** mode when upstream is not on `gerrit.remote`.

---

## Usage

```
ger push [options] [REV]
```

`REV` — push only through this commit (must be before the ready boundary unless `--all`).

---

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Print preview only |
| `-y`, `--yes` | Skip confirmation (required when stdin is not a TTY) |
| `-i` | TTY: prompt for reviewers (cannot combine with `-y`) |
| `--all` | Push full stack (ignore stop patterns) |
| `--ignore-pattern REGEX` | Disable one stop pattern (repeatable) |
| `--reviewers ACCOUNTS` | Comma-separated reviewers (repeatable; merged, deduped) |
| `--reviewer-strategy` | `push` \| `lazy` \| `overwrite` (see below) |
| `--topic NAME` | Magic ref `%topic=…` |
| `--wip` | Magic ref `%wip` |
| `--private` | Magic ref `%private` |
| `--branch NAME` | Push a different local branch than current |
| `--no-rebase-check` | Skip fetch/linear-on-remote check (`gerrit.push.remotePolicy`) |
| `--until REV` | Same as positional `REV` |
| `--color`, `--debug-log`, `-v` | Standard CLI helpers (`-v` reserved, no extra effect today) |
| `--follow-merges` | Include merge commits in stack range |

---

## Push modes

| Mode | When | Behavior |
|------|------|----------|
| **Gerrit** | `branch.*.gerritTarget` set, **or** upstream remote == `gerrit.remote` | Ready range → Change-Id check → `git push <tip>:refs/for/<target>[%options]` |
| **Vanilla** | Upstream on another remote | Plain `git push`; Gerrit-specific flags ignored (warning printed) |

No upstream and no `gerritTarget` → error; set upstream (e.g. `git branch --set-upstream-to=<remote>/<branch>`) or `branch.<name>.gerritTarget`. On a TTY, several commands prompt interactively via `ensure_branch_upstream_interactive`.

---

## Reviewer strategies

| Strategy | Behavior |
|----------|----------|
| `push` (default) | Reviewers on refspec `%r=…` |
| `lazy` | REST: add reviewers only where none assigned |
| `overwrite` | REST: replace reviewers per change |

`lazy` / `overwrite` may run REST assignment after push when git reports no new changes. Topic/WIP/private use magic ref options for all strategies.

Interactive push line (`push_input_prompt`) can set strategy keywords; see module `push_input_line.py`.

---

## Pre-push checks (Gerrit mode)

1. Target ref resolves locally (fetch if needed).
2. Ready boundary (unless `--all`).
3. Change-Id validation (exit `2` on hard errors).
4. Optional: `gerrit.push.remotePolicy` linearity check (unless `--no-rebase-check`).

---

## Confirmation output

Prints: `git push …` line, `ready reason: …`, `Updated commits:` with optional attribute preview (`gerrit.pushShowAttributes`). Prompt: `Do you want to push these commits? [Y/n]:` unless `--dry-run` / `-y` / non-TTY without `-y`.

After success, optional `lastPush/<branch>` marker (`gerrit.lastPushedBranch`, default on).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, dry-run OK, or user cancelled at prompt |
| `1` | Nothing to push / config / non-interactive without `-y` |
| `2` | Change-Id check failed |
| other | `git push` exit code |

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.remote` | Gerrit remote name (default `origin`) |
| `gerrit.pushShowAttributes` | Reviewer/wip/private preview |
| `gerrit.lastPushedBranch` | Local `lastPush/<branch>` after push |
| `gerrit.push.remotePolicy` | Remote tip linearity |
| `branch.*.gerritTarget`, `gerritReviewers` | Destination & default reviewers |
| `gerrit.stopPattern` | Ready boundary |

---

## See also

- [Configuration.md](../../Configuration.md) — `branch.*.gerritTarget`, `gerritReviewers`
- [`ger change-id`](sha-change-id.md#ger-change-id)
