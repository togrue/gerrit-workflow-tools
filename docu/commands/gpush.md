# git gpush

**Status:** Implemented

Push the ready prefix of the local stack to Gerrit. Orchestrates: target branch resolution → ready boundary calculation → Change-Id validation → push.

The push is always a single-tip push: `<tip>:refs/for/<target>[%r=…]`. Gerrit sees all ancestor commits automatically.

---

## Usage

```
git gpush [options] [REV]
```

`REV` — optional; push only through this commit (must be before the ready boundary).

---

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Print what would be pushed without executing |
| `-y`, `--yes` | Push without confirmation (required when stdin is not a terminal) |
| `-i` | Prompt for reviewers on a TTY; merged after branch config and `--reviewers` (cannot be combined with `--yes`) |
| `--show-attributes` | After the usual preview, query Gerrit and show current vs proposed review attributes per commit (see below) |
| `--all` | Push the entire stack, ignoring stop patterns |
| `--force-boundary` | Deprecated: same as `--all` — prefer `--all` |
| `--target BRANCH` | Override the Gerrit target branch for this push |
| `--save-target` | Persist `--target` into branch config for future pushes |
| `--ignore-pattern REGEX` | Disable a specific stop pattern (repeatable) |
| `--no-config-patterns` | Ignore all configured stop patterns |
| `--reviewers ACCOUNTS` | Comma-separated Gerrit reviewer accounts (repeatable; merged with `branch.<name>.gerritReviewers`, deduplicated) |
| `-v`, `--verbose` | Log git commands and push steps to stderr |

**`-i` (interactive reviewers)** — Only when stdin is a TTY. You are prompted for comma-separated reviewers (empty keeps branch and CLI defaults). Order after merge: branch `gerritReviewers`, then each `--reviewers` argument, then the interactive line (duplicates removed, first occurrence wins). A second prompt asks whether to save the merged reviewer list to `branch.<name>.gerritReviewers`. `-i` cannot be used with `-y`/`--yes` (use one or the other).

**`--show-attributes`** — For each commit in the push range that has a Change-Id, the tool batches Gerrit `change:` queries (same path as `git glog`) and appends a column to the “Updated commits” lines: `` `current` `` or `` `current` -> `new` `` when the proposed push would change reviewers on the refspec. Tokens are comma-separated: one `r=<account>` per reviewer (order matches Gerrit’s `reviewers` list for **current**; merged push order for **new**), then `wip` and `private` when set on the change. The push does not send `%wip`/`%private` in the refspec, so **proposed** wip/private always match the server for existing changes (only reviewer differences produce an arrow). New changes (no match in Gerrit) show `` `(none)` -> `r=…` `` when you add reviewers.

**Prerequisites for `--show-attributes`:** `git config gerrit.webUrl <https://…>` and REST credentials (`gerrit.user` with `gerrit.token` or `gerrit.password`). If either is missing, the command exits with code `1` after validation (before the push confirmation prompt). With `--dry-run`, Gerrit is still queried for the display.

---

## Pre-push checks

`git gpush` runs the following automatically and aborts on failure:

1. Gerrit target branch is configured (`gerritTarget` or `--target`).
2. Ready boundary is computed — blocked commits are excluded unless `--all`/`--force-boundary`.
3. Change-Id check — aborts with exit code `2` if any hard errors exist.

---

## Output

Before the push, the command prints (in order):

1. The exact `git push …` line (including `refs/for/<branch>` and any `%r=<reviewer>` options).
2. A blank line, then `ready reason: <explanation>`.
3. `Updated commits:` and one line per commit in the push range (oldest first): `    <short_sha> # <subject>`. Words `todo` and `dropme` in subjects are highlighted on a color terminal.

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
git gbranch init --target main --reviewers alice,bob

# Then push
git gpush
```

Or set the target inline and save it:

```bash
git gpush --target main --save-target
```

Add reviewers on the command line (merged with branch config):

```bash
git gpush --reviewers alice,bob
```

Preview how Gerrit reviewers / wip / private compare to this push (requires `gerrit.webUrl` and credentials):

```bash
git gpush --dry-run --show-attributes
```

Prompt for extra reviewers (TTY only), then push with confirmation:

```bash
git gpush -i
```

---

## See also

- [`git gbranch`](gbranch.md) — configure target, reviewers, push mode
- [`git gcid --check-duplicates`](gsha-gcid.md) — run the Change-Id check manually
