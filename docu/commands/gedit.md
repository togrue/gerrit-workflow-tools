# git gedit

**Status:** Implemented

Edit, reword, or drop a commit in the middle of the current local stack without manually constructing a `git rebase -i` todo list. The commit is identified by SHA or Change-Id.

---

## Usage

```
git gedit <commit> [--reword | --drop] [-v]
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
| `-v`, `--verbose` | Log git commands and rebase sequence editor steps to stderr |

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
git gedit a1b2c3d

# Edit a commit by Change-Id
git gedit Iabc1234...

# Only reword the commit message
git gedit a1b2c3d --reword

# Drop a commit from the stack
git gedit a1b2c3d --drop
```

---

## See also

- [`git glog`](glog.md) — see the stack and Change-Ids before choosing a commit
- [`git gsha`](gsha-gcid.md) — resolve a Change-Id to a SHA before editing
