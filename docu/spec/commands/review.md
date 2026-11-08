# `ger review` *(planned)*

| | |
|--|--|
| **Status** | **Planned**: not in `cli_ger.py` yet |
| **Module** | cli_review.py (new); checkout and review state in core/review_checkout.py (new); votes on `CommentApi` in `core/gerrit/comments.py` |
| **Requires** | A git repository, `gerrit.webUrl`, credentials |

Review someone else's change in your editor. `ger review` checks the change out, shows *what this commit changed* to every git tool you already use, walks up and down its review chain, and publishes your comments with your vote.

`ger inbox` finds what to review. `ger review` opens it. `ger comment` and `ger reply` write the comments, and `ger review --vote` sends them.

---

## The loop it enables

```text
ger inbox                   -> chains waiting on you
ger review 1234             -> change 1234 checked out in the review worktree, its diff staged
  ...read it in the IDE; ger comment src/app.py:42 -m "..."...
ger review --next           -> the next change up the chain
ger review --vote CR+1      -> "Publish 3 drafts and Code-Review+1 on 1235? [y/N]"
ger review --done           -> back to how things were
```

---

## Usage

```
ger review CHANGEISH [--patchset N] [--stage | --no-stage] [--in-worktree | --in-place] [--force] [--json]
ger review (--next | --prev) [--stage | --no-stage] [--force] [--json]
ger review (--stage | --unstage) [--json]
ger review --status [--json]
ger review --vote LABEL=N ... [-m MSG] [--dry-run] [-y] [--json]
ger review --done [--force] [--json]
```

`CHANGEISH` takes the full changeish grammar ([change-and-commit-identifiers.md](../change-and-commit-identifiers.md)): a change number, a Gerrit URL, a triplet. A URL that names a patchset picks that patchset; so does `--patchset`. Otherwise the current patchset.

---

## Where the change is checked out

Two **review checkout** modes, set by `ger.reviewCheckout`:

| Mode | Value | What happens |
|------|-------|--------------|
| **Review worktree** (default) | `worktree` | One reusable worktree, at `ger.reviewWorktree` (default: `<repository>-review` beside the main worktree). Created on first use with `git worktree add`, reused after. Your own branch, stack and working tree are never touched |
| **In place** | `branch` | The change is checked out in the current worktree, like a manual `git fetch` + `git switch` |

**First use asks.** When `ger.reviewCheckout` is unset and stdin is a terminal, `ger review` asks once: `Check out reviews in a separate worktree (w, default) or in this checkout (b)?` and writes the answer to the repository's local git config. Without a terminal it uses the review worktree and writes nothing. `--in-worktree` and `--in-place` override the setting for one run.

`ger review` acts on the review worktree whichever worktree you run it from. Its output names the worktree's path, so you can open it in your editor.

### The branch

The change is fetched from `gerrit.remote` by its `refs/changes/…` ref and checked out on a branch named **`change-<number>`**. The branch's upstream is set to the change's target branch on `gerrit.remote` when that remote-tracking ref exists. That makes the review chain below the change the **local stack** there: `ger log` and `ger show --stack` work as they do on your own branches.

A `change-<number>` branch that already exists is moved to the fetched patchset, unless it has commits the patchset does not have (you started reworking it). Then `ger review` exits `1` and names them; `--force` moves the branch anyway, and the old tip is printed.

### Leaving the checkout clean

Before switching to another change, `ger review` checks that the worktree holds nothing of yours: no unstaged edits, and an index that is either clean or exactly the staged review. Otherwise it exits `1` and lists the files. Untracked files are left alone and do not count. `--force` does not override this check; ger never throws your edits away.

In place, the branch you were on before the first `ger review` is remembered, so `--done` can go back to it.

---

## Staging the change

With **`--stage`** (the default; `ger.reviewStage=false` turns it off), the change's diff is put in the index:

```
git switch --detach <patchset>      HEAD and working tree at the change
git reset --soft <patchset>^        HEAD at its parent; the index still holds the change
```

The working tree is exactly the change, and every git tool shows the commit's diff as **staged changes**: the IDE's gutter markers, its "changes" view, `git diff --cached`. HEAD is detached at the parent, and the `change-<number>` branch stays on the change itself.

`--unstage` moves HEAD back to the change (`git reset --soft <patchset>`) and switches to the `change-<number>` branch. Staging a root commit (no parent) exits `2`. A merge commit stages against its first parent.

Edits you make while staged show as **unstaged** changes, on top of the staged review. That is the rework view: staged is what the author wrote, unstaged is what you changed.

---

## Walking the review chain

`--next` and `--prev` move to the change above or below in the change's **review chain**, using Gerrit's related changes for the reviewed patchset (one REST call). Merged and abandoned members are skipped. Each step fetches the new change and stages it like the first one.

At either end of the chain, `ger review` exits `10`, the code scripts already loop on for the attention walk.

---

## Voting and publishing

`--vote LABEL=N` (also `LABEL+N`, `LABEL-N`) votes on the change being reviewed, or on `--change CHANGEISH`. `CR` is short for `Code-Review` and `V` for `Verified`. Several `--vote`s may be given.

One REST call publishes **all your drafts on the change** (every patchset, as the web UI's *Send* does), the votes, and the `-m` message. Before it, ger lists the drafts and the votes and asks `Publish N drafts and Code-Review+1 on <change>? [y/N]`. `-y` skips the question and is required without a terminal. `--dry-run` only lists.

You vote on the patchset you reviewed. When Gerrit's current patchset is newer, `ger review --vote` exits `1` and says so, and you run `ger review` again to see the new patchset first.

With **GER_NO_PUBLISH=1** set, `--vote` exits `2` without contacting Gerrit, the same guard as `ger reply --publish`.

Local drafts are not sent by `--vote`. Uploading them is a later slice of [`ger comment`](comment.md#later-slices).

---

## Review state

What is being reviewed is recorded per worktree in `<git dir>/ger/review.json`: the change number, triplet, patchset, commit, whether it is staged, the mode, the branches ger created, and the branch to go back to. `ger comment` reads it to know which change a comment is for, even while HEAD is detached at the parent.

`--status` prints it. With no review going on, `--status` exits `0` and says so.

`--done` unstages, deletes the `change-<number>` branches the review created (keeping any with commits of yours, which it names), and forgets the review state. In place, it switches back to the remembered branch. The review worktree itself is kept for the next review.

---

## Output

**Human:**

```
reviewing 1234 patchset 4 "Extract command routing" (staged)
  worktree: /home/me/src/proj-review
  chain: 1233 <- [1234] <- 1235
  https://host/c/proj/+/1234
```

**JSON** (`--json`): the review state, plus `chain` (change numbers, base first) and `url`.

---

## Exit codes

| Code | When |
|------|------|
| `0` | Done |
| `1` | The worktree holds your edits; a `change-<number>` branch has your commits; Gerrit's patchset is newer than the one you voted on; the user declined |
| `2` | Bad usage; no review going on for `--next`, `--prev`, `--stage`, `--vote`; staging a root commit; `GER_NO_PUBLISH` with `--vote` |
| `3` | The changeish or patchset resolves to nothing |
| `5` | Gerrit failed |
| `7` | A git command failed (fetch, worktree, switch) |
| `10` | `--next` / `--prev` at the end of the chain |
