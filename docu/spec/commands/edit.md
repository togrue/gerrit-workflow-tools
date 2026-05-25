# `ger edit` / `ger reword`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_edit.py` |
| **Requires** | `ger edit --first-attention-commit` needs `gerrit.webUrl` + credentials |

Interactive rebase to **edit**, **reword**, or **drop** one commit in the current stack (by SHA or Change-Id).

---

## Usage

```
ger edit [REV] [--reword | --drop] [--first-attention-commit]
ger reword [REV] [--edit | --drop] [--first-attention-commit]
```

`ger edit` defaults to **edit** stop; `ger reword` defaults to **reword**. Each command exposes the other actions via flags.

---

## Options

| Option | Description |
|--------|-------------|
| `REV` | Optional SHA or Change-Id in stack |
| `--first-attention-commit` | Oldest commit matching log attention (unresolved comments or CI failed) |
| `--reword` / `--edit` / `--drop` | Override action (mutually exclusive per command) |
| `--debug-log`, `-v` | Standard helpers |

---

## Behavior

1. Resolve target commit in stack.
2. `git rebase -i <merge-base>` with custom `GIT_SEQUENCE_EDITOR` marking only the target line (`edit` / `reword` / `drop`).
3. For `edit`, user amends then `git rebase --continue`.

---

## V1 scope delta

No changes required for v1.

---

## See also

- [`ger log`](log.md)
- [`ger sha`](sha-change-id.md)
- [`ger rebase`](rebase.md)
