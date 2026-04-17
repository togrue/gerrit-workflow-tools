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
| `--all` | Push the entire stack, ignoring stop patterns |
| `--force-boundary` | Deprecated: same as `--all` — prefer `--all` |
| `--target BRANCH` | Override the Gerrit target branch for this push |
| `--save-target` | Persist `--target` into branch config for future pushes |
| `--ignore-pattern REGEX` | Disable a specific stop pattern (repeatable) |
| `--no-config-patterns` | Ignore all configured stop patterns |
| `--reviewers ACCOUNTS` | Comma-separated Gerrit reviewer accounts (repeatable; merged with `branch.<name>.gerritReviewers`, deduplicated) |
| `-v`, `--verbose` | Log git commands and push steps to stderr |

> **Note:** `-i` (interactive mode) is not yet implemented. Use `git gbranch init` to configure the branch first.

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

---

## See also

- [`git gbranch`](gbranch.md) — configure target, reviewers, push mode
- [`git gcid --check-duplicates`](gsha-gcid.md) — run the Change-Id check manually
