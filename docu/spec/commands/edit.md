# `ger edit` / `ger reword`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_edit.py`, `reword_run.py` |
| **Requires** | Git; attention targeting needs `ger.webUrl` + credentials, as does a `change:<n>` / URL / `q:` target |

Interactive rebase to **edit** or **drop** one commit in the current stack (by SHA or Change-Id), or a message-only **reword** of it with no rebase at all, including an **attention walk** over edit-attention commits (comments to answer, CI failed, or a negative vote).

---

## Usage

```
ger edit [REV] [--reword | --drop] [--first-attention-commit | -n | --next]
ger edit --cursor | --cursor-history | --set-cursor CHANGEISH
ger reword [REV] [-m MSG… | -F FILE] [--edit | --drop] [--first-attention-commit | -n | --next]
```

`ger edit` defaults to **edit** stop; `ger reword` defaults to **reword**. Each command exposes the other actions via flags.

### Change resolution

`REV` is resolved by `resolve_stack_changeish` — the same path as [`ger fix`](fix.md), [`ger reword`](edit.md) and [`ger rebase`](rebase.md). Change-Ids, triplets, `change:<n>`, `refs/changes/…` refs, URLs and `q:` queries all map to the stack commit with that identity; anything not **in** the stack is an error, because an interactive rebase over the stack cannot touch it.

A **Change-Id** or **triplet** matches offline; `change:<n>`, a URL or a `q:` query costs one Gerrit round trip to learn the Change-Id, and a `refs/changes/…` ref the repository already has needs none. Change-Id matching is case-insensitive. When Gerrit narrowing occurred, the **resolution note** is printed to stderr. A triplet or `change:<n>` naming a **sibling** on another branch still maps to the stack commit with that Change-Id; a stderr note says which branch that commit targets. Shared changeish grammar: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `REV` | Optional SHA or Change-Id in stack. With `-n`, treated as AFTER (next above that Change-Id). Omitted → **resume** the attention walk |
| `--first-attention-commit` | **Reset** the walk to the oldest edit-attention commit on the same first-parent chain `ger log` shows |
| `-n` / `--next` | Force the next edit-attention commit tip-ward of the cursor (or of `REV`), even if the cursor still needs attention. When a rebase is already in progress, `ger edit -n` runs `git rebase --continue` first |
| `--cursor` | Print the attention cursor for the working branch and exit |
| `--cursor-history` | Print recent walk events from `.git/ger/attention-history.jsonl` and exit |
| `--set-cursor CHANGEISH` | Set the cursor without starting a rebase |
| `--reword` / `--edit` / `--drop` | Override action (mutually exclusive per command) |
| `-m MSG` / `-F FILE` | A reword's new message without an editor: `-m` repeated for paragraphs (as `ger reply` / `ger comment` take it, `cli_args.add_message_args`), `-F -` reads stdin. A message without a `Change-Id:` footer keeps the commit's. Refused with any other action |
| `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |
| `--color WHEN`, `--hyperlinks WHEN` | `always` \| `auto` \| `never`, as for `ger log` and `ger show`; `auto` honours `NO_COLOR` ([Configuration.md](../../Configuration.md#colour)) |

---

## Attention walk

**Edit attention** = unresolved comments waiting on you, a failed build (a `Verified -1` among it), or a negative Code-Review vote (`core.attention.EDIT_ATTENTION_REASONS`). A vote, like a failed build, belongs to the patch set it was given on: once you have amended the commit with other code (push pending), Gerrit's vote is on a patch set you replaced, so the walk no longer stops there. Only rebasing it keeps the vote, as Gerrit carries it over. A comment chain stops the walk until it is answered, a draft reply included ([reply.md](reply.md)).

Bare `ger edit` (**resume**):

1. Load the usable **attention cursor** (TTL clear, missing clear, lazy adopt from `ger.lastAttentionChangeId`).
2. No usable cursor → oldest edit-attention commit; set cursor.
3. Cursor still has edit attention → stop there; refresh `touchedAt`.
4. Cursor no longer has edit attention → next tip-ward; advance cursor.
5. None left → `no commit needs edit attention…`, a non-zero exit, then, when the walk passed over commits whose comments all wait on the other side (`ger log` still marks them), a line naming the first of them: `1 commit has comments waiting on a reply: ger show 4583d2f`. The normal end of a walk, so it carries no `error:` prefix; after `-n` finished a stopped rebase it follows that rebase's closing line (`rebase finished: …`, [rebase.md](rebase.md#after-a-rebase-finishes)), with no `next:`: the walk is the next step.

Before the editor opens, a walk-chosen target is announced on stderr, e.g. `rewording 89d87be Add feature 3 (next needing attention: 1 unresolved comment)` or `editing 33e6b1b Add feature 2 (attention walk: Code-Review -1 from Bob)`. An explicit `REV` is not announced: you named it.

Cursor identity is a **Change-Id** (survives rebase). Config keys: [Configuration.md](../../Configuration.md#attention-cursor-ger-and-branch-local). ADR: [0009](../../adr/0009-attention-cursor-is-change-id-walk-state.md).

---

## Behavior

1. `ger edit -n` / `--next`, when a rebase is already in progress, runs `git rebase --continue` before resolving the next commit, the way `ger rebase --continue` does ([rebase.md](rebase.md#going-on-with-a-rebase-underway)): one shared `rebase_run.go_on_with_rebase`, so it refuses where that does: over unresolved conflicts or unstaged changes to tracked files, and without a terminal it takes a message as it is unless `GIT_EDITOR` is set. The cursor does not move unless that rebase finishes and the next one starts. A failed continue returns git's exit code. If git exits 0 but the rebase is still underway, the command exits non-zero.
2. Any other `ger edit` invocation, and `ger reword`, refuses with exit `2` (`USAGE`) while a rebase is in progress (or a `git am`, with its own hint), before the cursor moves; an edit or a drop also refuses a work tree with uncommitted changes to tracked files (git would refuse the rebase). A reword rebases nothing, so it does not. What counts is what `git stash` would save: a submodule with modified or untracked content inside it, or checked out at another commit than the one recorded, does not count. A submodule *staged* at another commit refuses with its own message, after uncommitted changes: commit it or `git restore --staged` it (the message gives the command for every such path). Git's own check before the rebase leaves it out, so the rebase would start and then stop at its first commit ("your local changes would be overwritten"), and an `edit` stop it reached would fold the staged submodule commit into the amended commit. A file or symlink put where a submodule is recorded, unstaged, refuses with its own message as well: `git stash` does not save it and the rebase would delete it, so commit it or move it out of the way. `ger rebase` makes the same refusals, with the same messages: one shared check, `rebase_run.require_history_rewrite`.
3. Resolve target commit in stack (explicit `REV`, or attention walk).
   **A change Gerrit already closed refuses**, exit `2` (`USAGE`), before the rebase starts and the cursor moves: git alone cannot tell, since a change submitted after your last `git fetch` is still on the local stack. A merged change says `<sha> is already merged on Gerrit, …; `git fetch`, then `ger rebase` to take it off the local stack`; an abandoned one says `<sha> is abandoned on Gerrit; restore it there first, or drop the commit (`ger edit --drop <sha>`)`. `--drop` is exempt: dropping a closed change is what to do with it. The status is the one the annotated stack already overlaid to find the target, so this costs no request of its own. One check, `rewrite_refusal.closed_change_refusal`, shared with [`ger fix`](fix.md).
4. For an edit or a drop, `git rebase -i --no-autosquash <merge-base>` with custom `GIT_SEQUENCE_EDITOR` marking only the target line (`edit` / `drop`). `--no-autosquash` whatever `rebase.autosquash` says: the todo never opens, so a fold would happen unseen and take the `fixup!` commit's sha with it. Folding is `ger rebase --autosquash`.
5. **A reword is no rebase** (`reword_run.reword`, through `core/reword.reword_commit`, as `ger serve`'s reword): the commit and the ones above it are recreated with their trees as they are, so uncommitted changes stay where they are and nothing can conflict. The message comes from `-m` / `-F`, else from your editor (`GIT_EDITOR`, `core.editor`, `VISUAL`, `EDITOR`, `vi`) on the commit's message, `#` lines left out. Without a terminal and with neither `-m`/`-F` nor `GIT_EDITOR` set it refuses, exit `2`, before anything moves: `no terminal to edit the message in: give it with -m MSG or -F FILE ('-': stdin), or set GIT_EDITOR to an editor that runs without one` (before, it rewrote every commit from the target up for nothing, or stopped mid-rebase on "Terminal is dumb"). An empty message, or an editor exiting non-zero, leaves the commit as it was, exit `1`. A message left as it was changes nothing: `the message of <sha> <subject> is unchanged: the stack is as it was`, every id kept. Done, it says what changed and how to take it back, then the closing lines a finished rebase has:

   ```text
   reworded 5dd708d → 9c1e2f0 parser: tokenize numbers
   to take it back: git reset --keep 6018164
   reword finished: rewrote 2 of 3 commits, all now ahead of Gerrit
   next: `ger push` uploads the stack as it is now
   ```

   A message without the commit's `Change-Id:` footer gets it back. One that names another Change-Id is taken as written, with a warning that the old Change-Id left the stack: the next push would create a new change and leave the old one open. The warning gives the remedy: put the `Change-Id:` footer back with `ger reword <sha>` to keep updating the old change, or abandon it on Gerrit. `ger edit` cannot tell, because the commit is amended after it returns.
   `--drop` says what it dropped and how to undo the whole rewrite, in the words `ger rebase --skip` uses (`rebase_run.print_dropped`): `dropped <sha> <subject>; its change <n> stays open on Gerrit` (the change as the cache last saw it: dropping a commit abandons nothing), then `to get it back: git reset --keep <pre-rebase HEAD>` (keeps uncommitted edits, refuses to overwrite them; the rewrite changed nothing else). Then the closing lines every finished rebase has ([rebase.md](rebase.md#after-a-rebase-finishes)): `rebase finished: dropped 1 commit; the other 2 commits as they were`, `next: `ger push` uploads the stack as it is now`. No question is asked: the drop is said, and can be taken back.
   A rebase that stops on a conflict (here, or in the `git rebase --continue` that `-n` runs) ends with one shared next step, the same for every ger-started rebase: `next: resolve the conflicts in <paths>, `git add` them, then `ger rebase --continue` to finish; `ger rebase --skip` leaves this commit out, `ger rebase --abort` goes back to where the rebase started` ([rebase.md](rebase.md#going-on-with-a-rebase-underway) has every stop's). During an attention walk (`ger edit -n`, `--first-attention-commit`, or the cursor) it also names the way on, between the two: `` `ger edit -n` finishes and goes on to the next commit needing attention``. `rebase_run.run_interactive_rebase` / `go_on_with_rebase` print it from `rebase_next_step.stop_next_step`.
6. The attention cursor moves only once the rebase has started (it exited `0`, or stopped with a conflict) and the target's Change-Id is known.
7. For `edit`, the stop says `stopped at <sha> <subject>`, then `next: amend it (`git commit --amend`), then `ger rebase --continue` to finish` (`rebase_next_step.edit_stop_next_step`). During an attention walk it goes on `; `ger edit -n` finishes and goes on to the next commit needing attention`: `ger rebase --continue` only finishes the rewrite; `ger edit -n` continues the rebase and walks to the next edit-attention commit. At a commit with edit attention (from the overlay the target was found by, no request of its own) one more line says how to see why and answer (`rebase_next_step.attention_stop_hint`): `once fixed, answer each comment `ger show` lists: `ger reply PATH:LINE --done` (or `-m TEXT`)`, for a failed build `` `ger ci` shows what failed in the build``, and for a negative vote `the reviewer says why in their comments or their message on Gerrit`.
8. When `ger.showAfterEdit` is on (the default), this was `ger edit --next` or `ger edit --first-attention-commit` (not `ger reword`, not `ger edit REV`), and the rebase exits 0, run `ger show` on that commit. Show exiting 1 (attention) leaves the rebase's 0 in place. Any other show failure is returned as-is. Off, the walk only moves.

---

## Exit codes

`ger edit` returns `git rebase`'s own exit code when the rebase fails. `0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract.

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger log`](log.md)
- [`ger show`](show.md) / [`ger sha`](sha-change-id.md) — read-only `--first-attention` / `--next-attention`
- [Configuration.md](../../Configuration.md#ger-edit) — `ger.showAfterEdit`
- [`ger rebase`](rebase.md)
