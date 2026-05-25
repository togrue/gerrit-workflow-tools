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

## V1 scope delta

From [Version 1 Scope](../../Version%201%20Scope.md) — **verify / implement before release:**

| Check | Status |
|-------|--------|
| Fail if target change already **merged** on Gerrit | Open |
| Fail or `--force` if fixup would **conflict** | Open |

---

## See also

- [`ger edit`](edit.md)
- [`ger sha`](sha-change-id.md)
