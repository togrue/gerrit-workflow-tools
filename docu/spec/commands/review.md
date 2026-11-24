# `ger review`

| | |
|--|--|
| **Status** | **Implemented** |
| **Module** | `cli_review.py`; the vote in `review_vote.py`, planned and sent by `core/review_target.py` (`plan_vote`, `send_vote`: `ger serve` sends through the same); checkout and review state in `core/review_checkout.py`; votes on `CommentApi` in `core/gerrit/comments.py`; the chain from `GerritRest.get_related` |
| **Requires** | A git repository, `ger.webUrl`, credentials |

Review someone else's change in your editor. `ger review` checks the change out, shows *what this commit changed* to every git tool you already use, walks up and down its review chain, and publishes your comments with your vote.

`ger inbox` finds what to review. `ger review` opens it. `ger comment` and `ger reply` write the comments, and `ger review --vote` sends them.

---

## The loop it enables

```text
ger inbox                   -> chains waiting on you, each named by its top change
ger review 1235 --bottom    -> the chain's lowest open change (1234) checked out in the review worktree, its diff staged
  ...read it in the IDE; ger comment src/app.py:42 -m "..."...
ger review --next           -> the next change up the chain
ger review --vote +1        -> "Publish 3 drafts and Code-Review+1 on 1235? [y/N]"
ger review --vote +1 --chain  -> the same for every change of the chain, in one go
ger review --done           -> back to how things were
```

---

## Usage

```
ger review CHANGEISH [--patchset N | --bottom] [--stage | --no-stage] [--in-worktree | --in-place] [--force] [--json | --ai]
ger review (--next | --prev) [--stage | --no-stage] [--force] [--json | --ai]
ger review (--stage | --unstage) [--json | --ai]
ger review --status [--json | --ai]
ger review --vote [LABEL]+N ... [--chain | --change CHANGEISH ...] [-m MSG | -F FILE] [--dry-run] [-y] [--json | --ai]
ger review --done [--force] [--json | --ai]
```

`CHANGEISH` takes the full changeish grammar ([change-and-commit-identifiers.md](../change-and-commit-identifiers.md)): a Gerrit URL, a triplet, `change:N`, `cN`, `#N`. A bare number is a change number here, not a git revision. A URL that names a patchset picks that patchset; so does `--patchset`. Otherwise the current patchset.

---

## Where the change is checked out

Two **review checkout** modes, set by `ger.reviewCheckout`:

| Mode | Value | What happens |
|------|-------|--------------|
| **Review worktree** (default) | `worktree` | One reusable worktree, at `ger.reviewWorktree` (default: `<repository>-review` beside the main worktree). Created on first use with `git worktree add`, reused after. Your own branch, stack and working tree are never touched |
| **In place** | `branch` | The change is checked out in the current worktree, like a manual `git fetch` + `git switch` |

**First use asks.** When `ger.reviewCheckout` is unset and stdin is a terminal, `ger review` asks once: `Check out reviews in a separate worktree (w, default) or in this checkout (b)?` and writes the answer to the repository's local git config. Without a terminal, or with `--json` or `--ai`, it uses the review worktree and writes nothing. `--in-worktree` and `--in-place` override the setting for one run.

`ger review` acts on the review worktree whichever worktree you run it from. Its output names the worktree's path, so you can open it in your editor.

### The branch

The change is fetched from `ger.remote` by its `refs/changes/…` ref and checked out on a branch named **`change-<number>`**. The branch's upstream is set to the change's target branch on `ger.remote` when that remote-tracking ref exists. That makes the review chain below the change the **local stack** there: `ger log` and `ger show --stack` work as they do on your own branches.

They do while the change is staged too. HEAD is then detached at the change's parent, and the review's `change-<number>` branch is the **working branch** (`Worktree.working_branch`, from the review state): `ger log`, `ger show`, `ger show --stack`, `ger ci` and `ger reply` see the chain up to and including the change under review, and `ger show` with no REV shows the change itself (`Repository.head`: what HEAD stands for). Once HEAD is moved off the parent, a detached HEAD is again just that (`HEAD is detached and no local branch points at it …`). What would rewrite or push that branch refuses instead, with exit `2` (`USAGE`) and one message: `ger fix`, `ger edit`, `ger reword`, `ger rebase` and `ger push` (without `--branch`) say `a staged review of c102 is underway here: `ger review --unstage` to work on it, or `ger review --done`` (`review_checkout.staged_review_refusal`). Unstaged, the change is checked out on its branch, and working on it is what you asked for.

A `change-<number>` branch that already exists is moved to the fetched patchset, unless it has commits the patchset does not have (you started reworking it). Then `ger review` exits `1` and names them; `--force` moves the branch anyway, and the old tip is printed.

### Leaving the checkout clean

Before switching to another change, `ger review` checks that the worktree holds nothing of yours: no unstaged edits, and an index that is either clean or exactly the staged review. Otherwise it exits `2` (`USAGE`, as `ger checkout` and `ger edit` do for the same reason), lists the files, and says to commit or stash them. Untracked files are left alone and do not count, and so does a submodule's own state (edits inside it, another commit checked out); a submodule staged at another commit, or a file put where a submodule is recorded, refuses with its own message, naming it, in the wording `ger checkout` and `ger edit` use. While the review is staged, `git stash` would take the staged change under review along with your edits, and a commit would commit it: the advice is then to keep the edits as a patch and put the change back, `git diff <change> -- PATH… > review-edits.patch && git restore --source=<change> --staged --worktree -- PATH…`, which leaves the review staged as `ger review` left it (`git apply review-edits.patch` brings the edits back; the untracked patch file does not count). A submodule refusal while staged gives only the step that unstages it (`git restore --staged --source=<change> PATH`). `--force` does not override this check; ger never throws your edits away.

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

`ger review CHANGEISH --bottom` starts at the **bottom** of CHANGEISH's review chain, its lowest open change, instead of CHANGEISH itself: `ger inbox` names a chain by its top, and review begins at the bottom (one more REST call, the related changes; not with `--patchset`). From there `--next` walks up.

`--next` and `--prev` move to the change above or below in the change's **review chain**, using Gerrit's related changes for the reviewed patchset (one REST call). Merged and abandoned members are skipped. Each step fetches the new change and stages it like the first one.

At either end of the chain, `ger review` exits `10`, the code scripts already loop on for the attention walk.

---

## Voting and publishing

`--vote +1` votes Code-Review (a bare `+N` or `-N` is always `Code-Review`); `--vote LABEL=N` (also `LABEL+N`, `LABEL-N`) votes another label. `CR` is short for `Code-Review` and `V` for `Verified`. Several `--vote`s may be given. The grammar is `core/votes.py`'s, shared with `ger reply --publish --vote`.

What it votes on:

| | |
|--|--|
| (default) | The change being reviewed |
| `--chain` | Every open change of the chain under review (Gerrit's related changes of the reviewed patchset), base first: a reviewed chain is finished in one command. With one `--change`, every open change of that change's chain (its current patchset's related changes), with no review checkout |
| `--change CHANGEISH` | That change; repeat it to vote on several (`--change 2078 --change 2079`). With `--chain`, only one |

Before anything is sent, the changes are read from Gerrit in one light query (one read per `--change`), and your drafts with one `has:draft` query over them; a change's chains are fetched only when it holds drafts. A vote Gerrit does not let you give on a change (outside its `permitted_labels`, when the change data has them) is named with what you may give: `change 2142 does not take Code-Review+3 (you may give -2, -1, 0, +1, +2)`. With `--chain`, such a change is skipped and listed as `skipped change N "subject": …`; when no change takes the vote, the command exits `2`, also under `--dry-run`.

One REST call per change publishes **all your drafts on it** (every patchset, as the web UI's *Send* does), the votes, and the message (`-m`, repeatable, or `-F FILE`, the shared message group). Before it, ger lists each change with its drafts and the votes, then the message (`  message: …`), and asks `Publish 2 drafts and Code-Review+1 on 2141, 2142? [y/N]`. Each change sent prints `published 1 draft and Code-Review+1 on <number>`, and a vote on the change under review ends with what is left: `next: ger review --next · ger review --done` (after `--chain`: `next: ger review --done`). With several changes the message line says it goes to each: `  message (on each of 5 changes): …`. `-y` skips the question and is required without a terminal: without one (or with `--json` / `--ai`) it exits `2` with the message `ger reply --publish` gives, `no prompt to answer (non-interactive, --json or --ai): pass -y to publish` (`cli_common.confirm_publish`). `--dry-run` only lists.

You vote on the patchset you reviewed. When Gerrit's current patchset of the change under review is newer, `ger review --vote` exits `1` and says so, and you run `ger review` again to see the new patchset first.

With **GER_NO_PUBLISH=1** set, `--vote` exits `2` without contacting Gerrit, the same guard as `ger reply --publish`.

Local drafts are not sent by `--vote`. Uploading them is a later slice of [`ger comment`](comment.md#later-slices).

---

## Review state

What is being reviewed is recorded per worktree in `<git dir>/ger/review.json`: the change number, triplet, patchset, commit, whether it is staged, the mode, the branches ger created, and the branch to go back to. `ger comment` reads it to know which change a comment is for, even while HEAD is detached at the parent.

`--status` prints it. With no review going on, `--status` exits `0` and says so.

`--done` unstages, deletes the `change-<number>` branches the review created (keeping any with commits of yours, which it names), and forgets the review state. In place, it switches back to the remembered branch. The review worktree itself is kept for the next review. Drafts of yours still unpublished on the changes the review visited are not lost, but nobody sees them: `--done` warns on stderr, `warning: 1 unpublished draft on c2076: ger reply --publish c2076`, and finishes anyway (one `has:draft` query over those changes, then a drafts GET only where it found some; nothing is said when Gerrit cannot be asked). `--json` lists them as `drafts` (`{number: count}`).

---

## Output

**Human:**

```
reviewing 1234 patchset 4 "Extract command routing" (staged)
  worktree: /home/me/src/proj-review
  chain: 1233 <- [1234] <- 1235
  https://host/c/proj/+/1234
  since your review of patchset 2: src/router.py (+12 -3), tests/test_router.py (+20 -0)
    git diff 1a2b3c4d5e6f 9f8e7d6c5b4a
next: cd ../proj-review · ger comment PATH:LINE -m … · ger review --next
```

The last line names the two or three steps of the loop that fit where you are: below the top of a chain, `ger comment PATH:LINE -m … · ger review --next`; at its top, `ger review --vote +1 --chain · ger review --done` (just `--vote +1` for a single change). The `cd` (to the review worktree, relative when shorter) comes first only when you are not in it. `--status` ends with the same line. On a terminal it wraps between steps ([SPEC.md](../../SPEC.md#output-formats)).

When you have reviewed an older patchset of the change (posted a vote, comment or reply on it), the two lines before it say what changed since: the **review delta** (`core/review_delta.py`, the one the VS Code extension shows), narrowed to the files either patchset touched when it was rebased, and the `git diff` that shows it. Nothing is said for patchset 1 or a patchset you already reviewed, and a failure to compute it never fails the review.

**`--ai`** (or `--format ai`): the human output with colour and hyperlinks off ([Output formats](../../SPEC.md#output-formats)).

**JSON** (`--json`): the review state, plus `chain` (change numbers, base first), `url`, and `since_reviewed` (`{patch_set, base, rebased, files}`) when there is a delta. `--vote --json`: for one change `{change, number, drafts, labels, message, status}` (`status` is `published` or `dry-run`); with `--chain` or several `--change`, `{changes: [those], skipped: [{change, number, ruled_out}]}`, which is also what `ger serve`'s `POST /vote` answers ([serve](serve.md)).

---

## Exit codes

| Code | When |
|------|------|
| `0` | Done |
| `1` | A `change-<number>` branch has your commits; Gerrit's patchset is newer than the one you voted on; the user declined (it says `cancelled`) |
| `2` | Bad usage; `--vote` without a terminal and without `-y`; the worktree holds your edits; no review going on for `--next`, `--prev`, `--stage`, `--vote`; staging a root commit; `GER_NO_PUBLISH` with `--vote` |
| `3` | The changeish or patchset resolves to nothing |
| `5` | Gerrit failed |
| `7` | A git command failed (fetch, worktree, switch) |
| `10` | `--next` / `--prev` at the end of the chain |
