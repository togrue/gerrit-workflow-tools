# ger push

**Status:** Implemented

Push the ready prefix of the local stack to Gerrit, or run a plain **`git push`** when the current branch tracks a **non-Gerrit** remote (see **Push modes** below). Orchestrates: target resolution → (Gerrit mode only) ready boundary → Change-Id validation → push.

In **Gerrit** mode the push is a single-tip push: `<tip>:refs/for/<target>[%r=…]`. Gerrit sees all ancestor commits automatically.

---

## Usage

```
ger push [options] [REV]
```

`REV` — optional; push only through this commit (must be before the ready boundary).

---

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Print what would be pushed without executing |
| `-y`, `--yes` | Push without confirmation (required when stdin is not a terminal) |
| `-i` | Prompt for reviewers on a TTY; merged after branch config and `--reviewers` (cannot be combined with `--yes`) |
| `--update-last-pushed` | After a successful push, update local branch `lastPush/<current-branch>` to the pushed tip. Default: `gerrit.lastPushedBranch` |
| `--no-update-last-pushed` | Skip updating `lastPush/<current-branch>` (overrides `gerrit.lastPushedBranch`) |
| `--all` | Push the entire stack, ignoring stop patterns |
| `--ignore-pattern REGEX` | Disable a specific stop pattern (repeatable) |
| `--reviewers ACCOUNTS` | Comma-separated Gerrit reviewer accounts (repeatable; merged with `branch.<name>.gerritReviewers`, deduplicated) |
| `--debug-log` | Log git commands and push steps to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

**`-i` (interactive reviewers)** — Only when stdin is a TTY. You are prompted for comma-separated reviewers (empty keeps branch and CLI defaults). Order after merge: branch `gerritReviewers`, then each `--reviewers` argument, then the interactive line (duplicates removed, first occurrence wins). A second prompt asks whether to save the merged reviewer list to `branch.<name>.gerritReviewers`. `-i` cannot be used with `-y`/`--yes` (use one or the other).

**Last-pushed marker branch** — When enabled (`gerrit.lastPushedBranch`, default `true`), after `git push` exits successfully the tool runs `git branch -f lastPush/<name> <tip>` where `<name>` is the current branch (`git rev-parse --abbrev-ref HEAD`) and `<tip>` is the commit pushed (same SHA as in the printed refspec). This is a local convenience ref only; it is not sent to Gerrit. If updating that ref fails, a warning is printed and the command still exits with the push’s status code. Disable globally with `git config gerrit.lastPushedBranch false`, or pass `--no-update-last-pushed` for a single run. Use `--update-last-pushed` to force the update when the config is off.

**Attribute preview** (`gerrit.pushShowAttributes`) — When enabled, for each commit in the push range that has a Change-Id, the tool batches Gerrit `change:` queries (same path as `ger log`) and appends a column to the “Updated commits” lines: `` `current` `` or `` `current` -> `new` `` when the proposed push would change reviewers on the refspec. Tokens are comma-separated: one `r=<account>` per reviewer (order matches Gerrit’s `reviewers` list for **current**; merged push order for **new**), then `wip` and `private` when set on the change. The push does not send `%wip`/`%private` in the refspec, so **proposed** wip/private always match the server for existing changes (only reviewer differences produce an arrow). New changes (no match in Gerrit) show `` `(none)` -> `r=…` `` when you add reviewers.

**Prerequisites for attribute preview:** `git config gerrit.webUrl <https://…>` and REST credentials (`gerrit.user` with `gerrit.token` or `gerrit.password`). If either is missing, the command exits with code `1` after validation (before the push confirmation prompt). With `--dry-run`, Gerrit is still queried for the display when preview is enabled.

---

## Push modes

| Mode | When | Behavior |
|------|------|----------|
| **Gerrit** | `branch.<name>.gerritTarget` is set, **or** `@{upstream}` exists and its remote name equals `gerrit.remote` (default `origin`) | Full pipeline: ready range, Change-Id check, `refs/for/…`, optional `gerrit.push.remotePolicy` fetch/check, reviewers. |
| **Vanilla** | `@{upstream}` exists and its remote is **not** `gerrit.remote` | Runs **`git push`** with **no extra arguments** (same as plain Git). `--until`, `--all`, `--reviewers`, `--ignore-pattern`, and `-i` are ignored (a warning is printed if any are set). No `refs/for/`, no Change-Id/ready pipeline, no remote-policy check. |

If there is **no** upstream and **no** `gerritTarget` override, the command exits with an error (set upstream or configure a target).

---

## Pre-push checks (Gerrit mode only)

`ger push` runs the following automatically and aborts on failure:

1. Effective Gerrit destination exists (`branch.<name>.gerritTarget` override, or upstream on `gerrit.remote`).
2. Ready boundary is computed — blocked commits are excluded unless `--all`.
3. Change-Id check — aborts with exit code `2` if any hard errors exist.

The effective target must **resolve locally** (for merge-base). That usually means you have the destination as a local branch or as a remote-tracking ref after `git fetch` on `gerrit.remote`. If you see an error that the target is missing, fetch first—see [Troubleshooting](branch.md#troubleshooting-gerrittarget-missing-locally) under `ger branch`.

---

## Output

Before the push, the command prints (in order):

1. The exact `git push …` line (including `refs/for/<branch>` and any `%r=<reviewer>` options).
2. A blank line, then `ready reason: <explanation>`.
3. `Updated commits:` and one line per commit in the push range (oldest first): `    <short_sha> # <subject>`. On color output, summary highlighting applies using `gerrit.stopPattern` (stop highlight) and `gerrit.warningPattern` (warning highlight, with stop taking precedence when both match).

Unless `--dry-run` or `--yes`/`-y` is used and stdin is a terminal, you are prompted: `Do you want to push these commits? [Y/n]: ` — Enter or `y`/`yes` proceeds; `n`/`no` cancels (exit `0`, no push).

With `--dry-run`, the same preview is printed, `[dry-run] not executing push` is written to stderr, and nothing is executed.

If stdin is not a terminal (e.g. CI) and you are not using `--dry-run`, you must pass `--yes`/`-y` to push; otherwise the command exits with code `1` so the process does not block waiting for input.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Push succeeded (or dry-run completed, or user cancelled at the prompt) |
| `1` | Nothing to push / config error / non-interactive without `--yes` |
| `2` | Change-Id check failed |
| non-zero | Git push exit code (network / Gerrit rejection) |

---

## First push on a new branch

```bash
# Configure branch metadata first
ger branch init --target main --reviewers alice,bob

# Then push
ger push
```

Add reviewers on the command line (merged with branch config):

```bash
ger push --reviewers alice,bob
```

Preview how Gerrit reviewers / wip / private compare to this push (requires `gerrit.webUrl`, credentials, and `gerrit.pushShowAttributes`):

```bash
git config gerrit.pushShowAttributes true
ger push --dry-run
```

Prompt for extra reviewers (TTY only), then push with confirmation:

```bash
ger push -i
```

---

## See also

- [`ger branch`](branch.md) — configure target and reviewers
- [`ger cid --check-duplicates`](sha-cid.md) — run the Change-Id check manually
- [Configuration reference](../Configuration.md) — `gerrit.pushShowAttributes`, `gerrit.lastPushedBranch`, `gerrit.stopPattern`, `gerrit.warningPattern`, credentials
