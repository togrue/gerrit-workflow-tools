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

### Change resolution

When `REV` / `--until` is given, it is resolved as a **changeish** via **`core/gerrit/change_resolution.py`**. Post-push reviewer REST and attribute preview resolve stack commits to Gerrit **triplets** (not bare Change-Ids). Push destination is inherently target-branch-scoped (`refs/for/<target>`). Shared grammar and target-branch narrowing for duplicate Change-Ids are defined in [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Print preview only |
| `-y`, `--yes` | Skip confirmation (required when stdin is not a TTY) |
| `-i` | TTY: prompt for reviewers (cannot combine with `-y`) |
| `--all` | Push full stack (ignore stop pattern; Change-Id errors still tighten the tip) |
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
| **Gerrit** | `branch.*.gerritTarget` set, **or** upstream remote == `gerrit.remote` | Ready range (stop pattern + Change-Id) → `git push <tip>:refs/for/<target>[%options]` |
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

### Push-options history

The interactive prompt (`-i`, and confirm-loop `r`) persists recent lines under
`$XDG_CACHE_HOME/ger/<host>/push_options_history/<project-safe>.txt`, keyed by
`gerrit.webUrl` host and `gerrit.project` (see
[ADR-0005](../../adr/0005-push-options-history-is-host-and-project-scoped.md)).

- Cap: 20 entries per (host, project), newest first, exact-line dedupe.
- Stored: reviewers, topic, `wip` / `private`. **Not** stored: strategy.
- If `--reviewer-strategy` is set, that strategy is merged into the visible
  prefill and Up/Down recall for the session; accept still strips strategy
  before save.
- Missing host/project identity: no read/write of history.
- Empty history: prefill `r=…` from `--reviewers`, else `.ger/reviewers` /
  host registry, else `branch.*.gerritReviewers`. Non-empty history always
  wins over those sources for the initial buffer.

---

## Pre-push checks (Gerrit mode)

1. Target ref resolves locally (fetch if needed).
2. Ready boundary: stop pattern / ready strategy (unless `--all`), tightened by the first missing/malformed/duplicate Change-Id (always, including with `--all`).
3. Optional: `gerrit.push.remotePolicy` linearity check (unless `--no-rebase-check`).

---

## Confirmation output

Prints: pushable commits, ready-boundary notice when a blocking commit exists (`Stopped at commit … because …`), confirm status line, and `git push …`. Prompt: `Do you want to push these commits? [Y/n]:` unless `--dry-run` / `-y` / non-TTY without `-y`.

After success, optional `lastPush/<branch>` marker (`gerrit.lastPushedBranch`, default on).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, dry-run OK, or user cancelled at prompt |
| `1` | Nothing to push / config / non-interactive without `-y` |
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
| `gerrit.stopPattern` | Ready boundary (single regex; see [Configuration.md](../../Configuration.md)) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [Configuration.md](../../Configuration.md) — `branch.*.gerritTarget`, `gerritReviewers`
- [`ger change-id`](sha-change-id.md#ger-change-id)
