# `ger assign` *(planned)*

| | |
|--|--|
| **Status** | **Planned** (v1 scope — not in `cli_ger.py` yet) |
| **Module** | — |
| **Replaces** | Earlier standalone `ger move` idea |

Assign or update Gerrit metadata on **existing** changes without re-pushing code.

---

## Intended usage (from product scope)

```
ger assign [options] <targets>
```

**Targets:** SHA range, Change-Id(s), or implicit current stack.

---

## Intended capabilities

| Capability | Notes |
|------------|-------|
| Set reviewers | One or more changes |
| Set topic | |
| Set WIP / private | |
| Move to different target branch | Absorbs `ger move` recovery workflow |

---

## Relationship to `ger push`

`ger push` already supports reviewers (refspec + REST strategies), topic, wip, private on **push**. `ger assign` is for changes already on Gerrit when you are not uploading a new patch set.

---

## Implementation notes (for future spec updates)

- Likely shares `core/push_reviewers.py` / REST mutation paths with push.
- Should reuse stack resolution from `core/stack.py` and change lookup from `GerritService`.
- Document exit codes and `--dry-run` when implemented.

---

## See also

- [Version 1 Scope](../../Version%201%20Scope.md#ger-assign-new-command)
- [`ger push`](push.md) (current metadata on push)
