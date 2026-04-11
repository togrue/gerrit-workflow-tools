# git gstack

**Status:** Implemented

Show the local commit stack from merge-base upward. This is the primary stack inspection primitive — all other commands operate on the same range.

---

## Usage

```
git gstack [options]
```

---

## Options

| Option | Description |
|--------|-------------|
| `--with-change-id` | Include Change-Id column (default: on) |
| `--no-change-id` | Omit Change-Id column |
| `--with-ready-state` | Annotate each commit with its ready/blocked state |
| `--json` | Machine-readable JSON output |
| `-v`, `--verbose` | Log git commands and stack resolution to stderr |

---

## Output

Default (human):

```
Base branch: main
Merge base: 1234abcd
Target review branch: main

  1   a1b2c3d Refactor parser init                    Change-Id: I111...
  2   b2c3d4e Extract command routing                 Change-Id: I222...
  3   c3d4e5f test! temporary experiment              Change-Id: I333...
```

With `--with-ready-state`, a symbol is shown before the short SHA:

| Symbol | Meaning |
|--------|---------|
| `✓` | ready (before stop boundary) |
| `!` | blocked by stop pattern |
| `x` | after a blocked commit |

JSON fields per commit: `index`, `sha`, `short_sha`, `subject`, `change_id`, `ready_state`.

---

## See also

- [`git gready`](gready.md) — computes the push boundary
- [`git gchangeid-check`](gchangeid-check.md) — validates the Change-Ids shown here
- [`git glog`](glog.md) — adds Gerrit status columns to the stack view
