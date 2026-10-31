# `ger edit` / `ger reword`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_edit.py` |
| **Requires** | Git; attention targeting needs `gerrit.webUrl` + credentials, as does a `change:<n>` / URL / `q:` target |

Interactive rebase to **edit**, **reword**, or **drop** one commit in the current stack (by SHA or Change-Id), including an **attention walk** over edit-attention commits (unresolved comments or CI failed).

---

## Usage

```
ger edit [REV] [--reword | --drop] [--first-attention-commit | -n | --next]
ger edit --cursor | --cursor-history | --set-cursor CHANGEISH
ger reword [REV] [--edit | --drop] [--first-attention-commit | -n | --next]
```

`ger edit` defaults to **edit** stop; `ger reword` defaults to **reword**. Each command exposes the other actions via flags.

### Change resolution

`REV` is resolved by `resolve_stack_changeish` — the same path as [`ger fix`](fix.md), [`ger reword`](edit.md) and [`ger rebase`](rebase.md). Change-Ids, triplets, `change:<n>`, `refs/changes/…` refs, URLs and `q:` queries all map to the stack commit with that identity; anything not **in** the stack is an error, because an interactive rebase over the stack cannot touch it.

A **Change-Id** or **triplet** matches offline; `change:<n>`, a URL or a `q:` query costs one Gerrit round trip to learn the Change-Id, and a `refs/changes/…` ref the repository already has needs none. Change-Id matching is case-insensitive. When Gerrit narrowing occurred, the **resolution note** is printed to stderr. Shared changeish grammar: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `REV` | Optional SHA or Change-Id in stack. With `-n`, treated as AFTER (next above that Change-Id). Omitted → **resume** the attention walk |
| `--first-attention-commit` | **Reset** the walk to the oldest edit-attention commit on the same first-parent chain `ger log` shows |
| `-n` / `--next` | Force the next edit-attention commit tip-ward of the cursor (or of `REV`), even if the cursor still needs attention |
| `--cursor` | Print the attention cursor for the working branch and exit |
| `--cursor-history` | Print recent walk events from `.git/ger/attention-history.jsonl` and exit |
| `--set-cursor CHANGEISH` | Set the cursor without starting a rebase |
| `--reword` / `--edit` / `--drop` | Override action (mutually exclusive per command) |
| `--debug-log`, `-v` | Standard helpers |

---

## Attention walk

**Edit attention** = unresolved comments or CI failed (same reasons as `--first-attention-commit` historically).

Bare `ger edit` (**resume**):

1. Load the usable **attention cursor** (TTL clear, missing clear, lazy adopt from `ger.lastAttentionChangeId`).
2. No usable cursor → oldest edit-attention commit; set cursor.
3. Cursor still has edit attention → stop there; refresh `touchedAt`.
4. Cursor no longer has edit attention → next tip-ward; advance cursor.
5. None left → error `no commit needs edit attention…`.

Cursor identity is a **Change-Id** (survives rebase). Config keys: [Configuration.md](../../Configuration.md#attention-cursor-ger-and-branch-local). ADR: [0009](../../adr/0009-attention-cursor-is-change-id-walk-state.md).

---

## Behavior

1. Resolve target commit in stack (explicit `REV`, or attention walk).
2. On a successful attention or explicit-stack target, update the attention cursor when a Change-Id is known.
3. `git rebase -i <merge-base>` with custom `GIT_SEQUENCE_EDITOR` marking only the target line (`edit` / `reword` / `drop`).
4. For `edit`, user amends then `git rebase --continue`.
5. When `gerrit.editNextShow` is true, this was `ger edit --next` (not `ger reword`), and the rebase exits 0, run `ger show` on that commit. Show exiting 1 (attention) leaves the rebase's 0 in place. Any other show failure is returned as-is. Unset, the flag is off and `--next` only moves.

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
| `7` | `GIT` — a git command failed (including no edit-attention target) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger log`](log.md)
- [`ger show`](show.md) / [`ger sha`](sha-change-id.md) — read-only `--first-attention` / `--next-attention`
- [Configuration.md](../../Configuration.md#ger-edit) — `gerrit.editNextShow`
- [`ger rebase`](rebase.md)
