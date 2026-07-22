# `ger fix`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_fix.py` |
| **Requires** | Git; Gerrit lookup when target is a change id |

Create a **fixup** commit: `git commit --fixup=<target>`.

---

## Usage

```
ger fix [options] REF_OR_CHANGE
```

`REF_OR_CHANGE` — commit-ish, `refs/changes/…`, numeric change id, or Change-Id (`I…`).

### Change resolution

`REF_OR_CHANGE` is a **changeish** resolved through **`core/gerrit/change_resolution.py`**: same grammar as `ger show` (git ref default; `change:<n>` for a Gerrit change number; triplet/URL for an exact pick). Ambiguous bare Change-Ids follow target-branch narrowing ([§3](../change-and-commit-identifiers.md#3-duplicate-change-ids-the-important-case)). Full contract: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md). With `--json`, output includes a `resolution` block when Gerrit resolution ran ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)). Ambiguity after narrowing exits `4`.

---

## Options

| Option | Description |
|--------|-------------|
| `-a`, `--all` | Stage all tracked modifications (`git commit -a`) |
| `--no-verify` | Pass `-n` to `git commit` (skip hooks) |
| `--debug-log`, `-v` | Standard helpers |

Default: only **staged** changes are committed. On a TTY with an empty index, prompts to stage tracked modifications (`y` / `n` / `d` for diff).

---

## Behavior

1. Resolve `REF_OR_CHANGE` to a commit SHA (local ref, `refs/changes/…` fetch, or Gerrit API + fetch when the argument is a Change-Id or numeric change id).
2. If the index has no staged changes and `-a` was not passed:
   - **Interactive TTY** and there are unstaged modifications to tracked files: prompt `[y/n/d]` — `y` runs `git add -u` and continues; `d` prints the unstaged diff to stderr and re-prompts; `n` / empty / interrupt declines.
   - Otherwise (non-TTY, declined, or nothing to stage): exit `1` with a hint to stage edits or use `-a`.
3. Run `git commit --fixup=<sha>` (honours `--no-verify` and `-a`).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Fixup commit created successfully |
| `1` | Fixup rejected (no staged changes, resolution/git error) |
| `2` | Usage error (bad arguments) |
| `3` | Gerrit API error (unreachable, auth failure, change not found) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger edit`](edit.md)
- [`ger sha`](sha-change-id.md)
