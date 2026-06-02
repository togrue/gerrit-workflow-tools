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

---

## Options

| Option | Description |
|--------|-------------|
| `-a`, `--all` | Stage all tracked modifications (`git commit -a`) |
| `--no-verify` | Pass `-n` to `git commit` (skip hooks) |
| `--debug-log`, `-v` | Standard helpers |

Default: only **staged** changes are committed.

---

## Behavior

1. Resolve `REF_OR_CHANGE` to a commit SHA (local ref, `refs/changes/…` fetch, or Gerrit API + fetch when the argument is a Change-Id or numeric change id).
2. If the index has no staged changes and `-a` was not passed, exit `1` with a hint to stage edits or use `-a`.
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

- [`ger edit`](edit.md)
- [`ger sha`](sha-change-id.md)
