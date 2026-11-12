# Commit-chain roadmap

Where the **local stack** handling should go next, and the architecture that makes it cheap.
Written 2026-11-11, after the reorder conflict preview landed.

## Where we are

| Operation | Path today |
|-----------|-----------|
| reorder, squash, drop | `core/reorder.py`: preview with `merge-tree` (`core/reorder_preview.py`), then `git rebase -i` with a generated todo |
| edit, reword | `cli_edit.py`: `git rebase -i` with `rebase_sequence_editor.py` patching one line |
| rebase onto upstream | `cli_rebase.py`: `git rebase -i` with `rebase_enricher.py` |
| fix | `git commit --fixup`, folded by a later autosquash |
| undo | `/stack/undo`, only for the rewrite just made, only from the extension |

Four routes for "replace the stack with a rewritten one", each with its own refusal rules. The
preview simulates what the rebase will do; it is not the thing that runs, so the two can
drift apart.

## The architectural move: one stack-rewrite engine

Every history edit becomes **plan → simulate → apply → record**:

1. **Plan**: a *stack plan* is the `StackStep` list `core/reorder.py` already has, extended with
   `edit` (stop here), `reword` (new message), `split`, and a new **base** (for rebasing onto
   upstream). One type for every operation.
2. **Simulate**: the batched `merge-tree` + tree replay from `reorder_preview.py`, generalised.
   It builds the whole new chain in the object store (`commit-tree` per pick), worktree untouched.
   Result: either the new head and an old → new commit map, or the first conflict.
3. **Apply**:
   - *Clean simulation, clean worktree:* `update-ref` the branch to the simulated head and
     `read-tree -m -u` the worktree. No rebase runs, so nothing can half-fail, and **what was
     previewed is exactly what lands**.
   - *Conflict, or a step that stops (`edit`):* fall back to `git rebase -i` with the todo from
     the same plan, so the user resolves in the usual place.
4. **Record**: every apply appends `{op, old head, new head, Change-Id → old/new sha}` to an
   operation log under `.git/ger/`. Undo, the extension's "follow the change by Change-Id", and
   the attention cursor ([ADR-0009](../adr/0009-attention-cursor-is-change-id-walk-state.md))
   all read the same map.

This moves `reorder.py`, `reorder_preview.py`, the edit/reword sequence editor and the rebase
todo generation behind one owner (a new *stack rewrite* module in `core/`), which the module catalog and
`tests/test_architecture.py` then pin. It needs an ADR: "rewrites are computed in the object
store; `git rebase` is the fallback for stops and conflicts".

## Features, in order of value

| # | Feature | What it gives | Built on |
|---|---------|---------------|----------|
| 1 | **Operation log + `ger undo`** | Any rewrite (CLI or extension, reorder, rebase, edit) is one step back; `ger oplog` lists them | Record |
| 2 | **`ger sync`** | Fetch upstream, drop commits already merged (matched by Change-Id, not by patch), rebase the rest, with the conflict preview *before* starting | Simulate with a new base |
| 3 | **Restack after amend** | Amending a mid-stack commit outside `ger edit` leaves its descendants on the old version; detect it by Change-Id and replay them | Plan + simulate |
| 4 | **Absorb** | Route working-tree hunks into the stack commit that last touched those lines (as fixups, or folded immediately). The fastest way to address review comments across a chain, for people and for the agent runbook | blame per hunk + apply |
| 5 | **Adopt a newer remote patchset** | Gerrit's "Rebase" button or a co-author uploads PS n+1; the stack shows it as diverged and offers to take it into the local stack | Gerrit overlay + plan |
| 6 | **Split a commit** | Choose files or hunks for a new commit below; the Change-Id stays with the part you pick | Plan step `split` |
| 7 | **Insert a commit at a position** | "New commit below this one" from the stack view: an `edit` stop on the parent | Plan step `edit` |
| 8 | **Rebase state in the UI** | During a stopped rebase the stack view shows *Continue* / *Abort* / *Skip* and the conflicted files instead of "a rebase is in progress" | *repo state* (below) |

Deliberately left out until someone asks: multiple stacks per branch (a stack *graph*), and
cross-repository topics.

## Supporting architecture

- **A *repo state* verdict in core.** One verdict: clean, dirty, rebase stopped (on which commit, which
  files), review checkout active. Every mutation endpoint and command refuses or offers
  continue/abort from it, instead of each checking `rebase_in_progress` itself.
- **Cache what is keyed by object ids.** Merge results, diffs and trees for given SHAs never
  change, so memoise them for the life of the service. Keep the per-request clear only for what
  reads refs. That makes repeated previews (`/stack/moves` after an undo) close to free.
- **Reads off the request lock.** `ger serve` serialises every request (`api_lock` in
  `server/httpd.py`). A slow read, such as a long preview or a CI log, then blocks a quick one.
  Reads on immutable inputs can run outside the lock; writes stay serialised.
- **Push events to the extension.** Watch `HEAD`, the branch ref and `rebase-merge/` and push a
  "stack changed" event (server-sent events). The extension stops polling and stops asking
  for `/stack/moves` when nothing changed.

## UI behaviour users expect

What people bring from stack tools (Graphite, Sapling's ISL, GitButler, jj UIs) and from VS
Code itself, against what the stack view does today:

| Expectation | Today | Change |
|-------------|-------|--------|
| **Undo is always there**, not only in a toast that disappears | "Undo" button on the notification after a rewrite | A permanent *Undo last stack change* in the view title and `Ctrl+Z` while the view has focus, backed by the operation log |
| **No confirmation for what can be undone** | Drop asks in a modal dialog | Drop at once, offer undo. Keep modals for what cannot be taken back (push, abandon on Gerrit) |
| **The row moves at once** (optimistic update), and snaps back on refusal | The list redraws after the server answers | Reorder the tree locally, spinner on the moved row, revert and show the reason on refusal |
| **Drag shows where it lands**: an insertion line between rows | Done: *Local stack* is a webview, and a line between two rows shows where the dragged commits land | — |
| **Keyboard first**: move like moving lines | Arrows only as inline icons | `Alt+Up` / `Alt+Down` to move, `Delete` to drop, `Enter` to open, `F2` to reword |
| **Several commits at once** | Multi-select moves a run of commits as one rewrite, and copies their ids | Squash or drop a run of commits as one rewrite |
| **Few inline icons, everything in the context menu** | Up to four inline icons per row | Inline: move up/down (or the blocked marker) and the primary action; the rest in the context menu and palette |
| **Selection follows the commit** through a rewrite | The change view follows by Change-Id; tree selection does not | Re-select by Change-Id after every rewrite |
| **Actions say where they happen** | "Check Out" meant two different things (now split) | Every action that changes files names the place: *this checkout*, *review worktree* |
| **Conflicts open where conflicts are resolved** | "A rebase is in progress" row | Conflicted files listed, opened in VS Code's merge editor, *Continue* / *Abort* / *Skip* buttons |
| **State at a glance** per row: needs push, diverged from Gerrit, CI, votes | Icons and description cover most of it | Add *diverged* (Gerrit has a newer patchset) once feature 5 exists |
| **Empty states lead somewhere** | "No local commits" | Links to create a commit or pull a change from the inbox |
| **Never frozen**: other views stay live during a long operation | The service serialises requests | Reads off the lock (below); progress shown in the view, not a modal |

Most of these are extension-only and small (undo button, no modal on drop, keyboard bindings,
selection by Change-Id, inline-icon trim); they can go before phase 1.

## Phases

0. **UI quick wins** from the table above that need no new service endpoints.

1. **Engine, no new features.** Extract a new *stack rewrite* module in `core/`; move reorder/squash/drop onto
   simulate + `update-ref` apply; keep the `git rebase` fallback. Done when the existing
   reorder, preview and e2e tests pass unchanged and a clean reorder starts no rebase. ADR.
2. **Operation log and `ger undo`.** `/stack/undo` becomes a reader of the log. Edit and reword
   are recorded too, at the point their rebase finishes.
3. **`ger sync`** (rebase onto upstream through the engine, merged-change drop, preview first),
   then restack-after-amend.
4. ***repo state*** and rebase continue/abort in the extension.
5. **Absorb**, then split and insert.
6. **Service:** object-id memo, reads off the lock, then pushed events. These are independent
   of 2–5 and can come earlier if the extension feels slow.

Each phase lands as its own refactor commit plus feature commit, per [AGENTS.md](../../AGENTS.md).
