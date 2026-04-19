# ger edit

**Status:** Implemented

Edit, reword, or drop a commit in the middle of the current local stack without manually constructing a `git rebase -i` todo list. The commit is identified by SHA or Change-Id.

---

## Usage

```
ger edit <commit> [--reword | --drop] [-v]
```

---

## Arguments

| Argument | Description |
|----------|-------------|
| `commit` | Short or full SHA, or Gerrit Change-Id (`I…`); must be in the current stack |

---

## Options

| Option | Description |
|--------|-------------|
| `--reword` | Reword the commit message only (opens `$EDITOR`) |
| `--drop` | Drop the commit entirely from the stack |
| (none) | Default: `edit` — stop at the commit for amending |
| `--debug-log` | Log git commands and rebase sequence editor steps to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

`--reword` and `--drop` are mutually exclusive.

---

## Behavior

1. Verify the commit is in the current local stack.
2. Launch `git rebase -i <merge-base>` with a custom sequence editor.
3. The sequence editor marks only the target commit with the requested action (`edit`, `reword`, or `drop`); all other commits are left as `pick`.
4. Git pauses at the target commit (for `edit`) or completes automatically (for `reword` / `drop`).

For `edit`, amend the commit normally, then `git rebase --continue`.

---

## Examples

```bash
# Edit a commit by short SHA
ger edit a1b2c3d

# Edit a commit by Change-Id
ger edit Iabc1234...

# Only reword the commit message
ger edit a1b2c3d --reword

# Drop a commit from the stack
ger edit a1b2c3d --drop
```

---

## See also

- [`ger log`](log.md) — see the stack and Change-Ids before choosing a commit
- [`ger sha`](sha-cid.md) — resolve a Change-Id to a SHA before editing
