# `ger edit` / `ger reword`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_edit.py` |
| **Requires** | Git; `--first-attention-commit` needs `gerrit.webUrl` + credentials, as does a `change:<n>` / URL / `q:` target |

Interactive rebase to **edit**, **reword**, or **drop** one commit in the current stack (by SHA or Change-Id).

---

## Usage

```
ger edit [REV] [--reword | --drop] [--first-attention-commit]
ger reword [REV] [--edit | --drop] [--first-attention-commit]
```

`ger edit` defaults to **edit** stop; `ger reword` defaults to **reword**. Each command exposes the other actions via flags.

### Change resolution

`REV` is resolved by `resolve_stack_changeish` — the same path as [`ger fix`](fix.md), [`ger reword`](edit.md) and [`ger rebase`](rebase.md). Change-Ids, triplets, `change:<n>`, `refs/changes/…` refs, URLs and `q:` queries all map to the stack commit with that identity; anything not **in** the stack is an error, because an interactive rebase over the stack cannot touch it.

A **Change-Id** or **triplet** matches offline; `change:<n>`, a URL or a `q:` query costs one Gerrit round trip to learn the Change-Id, and a `refs/changes/…` ref the repository already has needs none. Change-Id matching is case-insensitive. When Gerrit narrowing occurred, the **resolution note** is printed to stderr. Shared changeish grammar: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `REV` | Optional SHA or Change-Id in stack |
| `--first-attention-commit` | Oldest commit matching log attention (unresolved comments or CI failed), searched over the same first-parent chain `ger log` shows |
| `--reword` / `--edit` / `--drop` | Override action (mutually exclusive per command) |
| `--debug-log`, `-v` | Standard helpers |

---

## Behavior

1. Resolve target commit in stack.
2. `git rebase -i <merge-base>` with custom `GIT_SEQUENCE_EDITOR` marking only the target line (`edit` / `reword` / `drop`).
3. For `edit`, user amends then `git rebase --continue`.

---

## Exit codes

Semantic codes from the shared `ExitCode` table ([exit-codes.md](../exit-codes.md)) — the same
codes [`ger fix`](fix.md) uses for the same failures.

| Code | Meaning |
|------|---------|
| `0` | Rebase completed (or git's own exit code) |
| `1` | `ATTENTION` — declined the upstream prompt |
| `2` | Usage error (bad arguments) |
| `3` | `NOT_FOUND` — no commit in the stack matches `REV` |
| `4` | `AMBIGUOUS` — several stack commits share the Change-Id |
| `5` | `GERRIT` — Gerrit answered badly, or not at all |
| `6` | `CONFIG` — required git configuration missing |
| `7` | `GIT` — a git command failed |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger log`](log.md)
- [`ger sha`](sha-change-id.md)
- [`ger rebase`](rebase.md)
