# git gchangeid-check

**Status:** Implemented

Validate Change-Id footer presence and uniqueness for all commits in the local stack. Also run automatically by `git gpush` before any push; a hard error (exit 2) blocks the push.

---

## Usage

```
git gchangeid-check [options]
```

---

## Options

| Option | Description |
|--------|-------------|
| `--range RANGE` | Check only commits in the given range, e.g. `origin/main..HEAD` |
| `--strict` | Treat malformed Change-Id as error (default) |
| `--lenient` | Treat malformed Change-Id as warning only |
| `--json` | Machine-readable JSON output |
| `-v`, `--verbose` | Log git commands and checked revisions to stderr |

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All Change-Ids present, valid, and unique |
| `1` | Warnings only (e.g. missing Change-Id in lenient mode) |
| `2` | Hard errors: duplicate or malformed Change-Id |

---

## Output

Human output on success:

```
Change-Id check: OK
```

On error (to stderr):

```
ERROR: a1b2c3d duplicate_change_id: I111... also on b2c3d4e
```

JSON fields per issue: `kind`, `sha`, `short_sha`, `detail`, `severity`.

Issue kinds: `missing_change_id`, `duplicate_change_id`, `malformed_change_id`.

---

## Common causes of failures

| Problem | Cause |
|---------|-------|
| Missing Change-Id | Commit created without the Gerrit commit-msg hook |
| Duplicate Change-Id | Copy-paste of commit message, squash/split, or cherry-pick |
| Malformed Change-Id | Manually edited footer |

---

## See also

- [`git gpush`](gpush.md) — runs this check automatically before pushing
- [`git gstack`](gstack.md) — shows Change-Ids for all commits in the stack
- [`git gcid` / `git gsha`](gsha-gcid.md) — look up individual Change-Ids
