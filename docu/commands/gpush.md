# git gpush

**Status:** Implemented

Push the ready prefix of the local stack to Gerrit. Orchestrates: target branch resolution → ready boundary calculation → Change-Id validation → push.

The push is always a single-tip push: `<tip>:refs/for/<target>`. Gerrit sees all ancestor commits automatically.

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
| `--all` | Push the entire stack, ignoring stop patterns |
| `--force-boundary` | Same as `--all` |
| `--target BRANCH` | Override the Gerrit target branch for this push |
| `--save-target` | Persist `--target` into branch config for future pushes |
| `--ignore-pattern REGEX` | Disable a specific stop pattern (repeatable) |
| `--no-config-patterns` | Ignore all configured stop patterns |
| `--reviewer ACCOUNT` | Add a reviewer (reserved; passed through) |
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

```
Summary
  branch:       feature/my-work
  target:       main
  remote:       origin
  push tip:     b2c3d4e...
  ready reason: subject matches stop pattern '^test!'
  push range:   1234abcd..b2c3d4e

git push origin b2c3d4e...:refs/for/main
```

With `--dry-run`, the push command is printed but not executed.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Push succeeded (or dry-run completed) |
| `1` | Nothing to push / config error |
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

---

## See also

- [`git gbranch`](gbranch.md) — configure target, reviewers, push mode
- [`git gready`](gready.md) — inspect the ready boundary without pushing
- [`git gcid --check-duplicates`](gsha-gcid.md) — run the Change-Id check manually
