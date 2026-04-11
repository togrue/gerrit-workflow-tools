# git gready

**Status:** Implemented

Compute the pushable prefix of the local stack according to stop-pattern policy. Reports the boundary commit, reason, and the exact push range / tip SHA that `git gpush` will use.

---

## Usage

```
git gready [options]
```

---

## Options

| Option | Description |
|--------|-------------|
| `--all` | Treat the entire stack as pushable (ignore stop patterns) |
| `--until REV` | Limit pushable tip to a specific commit (must be before the boundary) |
| `--ignore-pattern REGEX` | Disable a specific configured stop pattern (repeatable) |
| `--no-config-patterns` | Do not use `gerrit.stopPattern` values from git config |
| `--json` | Machine-readable JSON output |
| `-v`, `--verbose` | Log git commands and ready calculation to stderr |

---

## Output

Human output:

```
Push mode: ready
Pushable commits: 2
Boundary commit: c3d4e5f
Boundary reason: subject matches stop pattern '^test!'

Push range:
  1234abcd..b2c3d4e
```

JSON fields: `push_mode`, `pushable_commits`, `boundary_commit`, `boundary_reason`, `merge_base`, `push_tip`, `push_range`.

---

## Stop patterns

Stop patterns are configured globally in git config:

```ini
[gerrit]
    stopPattern = ^dropme!
    stopPattern = ^TODO\b
    stopPattern = ^test!
```

The ready boundary is the **first commit** (bottom-up) whose subject matches any configured pattern.

---

## See also

- [`git gpush`](gpush.md) — uses the ready result to determine what to push
- [`git gstack`](gstack.md) — inspect the full stack including ready state
- [`git gbranch`](gbranch.md) — configure `gerritPushMode` per branch
