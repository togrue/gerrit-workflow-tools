# `ger checkout`

| | |
|--|--|
| **Status** | **Implemented** |
| **Module** | `cli_checkout.py`; the decision and the git work in `core/change_checkout.py`; the patchset from `core/gerrit_patchset.py` |
| **Requires** | A git repository, `gerrit.webUrl`, credentials |

Check a Gerrit change out to work on it: rework a change of yours on another machine, take over a colleague's, or pick up a new patchset someone pushed. The change lands on a local branch whose upstream is the change's **target branch** on `gerrit.remote`, so `ger log` shows its review chain as the **local stack** and `ger push` uploads a new patchset, with nothing to set up.

`ger review` is the other way to check a change out: for reading someone's work, in the review worktree, staged. `ger checkout` is for changing it, in this checkout.

---

## Usage

```
ger checkout CHANGEISH [--patchset N] [-b NAME] [--update | --no-update] [--force]
```

`CHANGEISH` takes the full changeish grammar ([change-and-commit-identifiers.md](../change-and-commit-identifiers.md)): a change number, `change:N`, a Gerrit URL, a triplet, a `refs/changes/…` ref, a Change-Id. A bare number is a change number here, not a git revision, as in `ger review`. A Change-Id that exists on several target branches is narrowed to your push branch with a resolution note; when that leaves more than one, or none on your push branch and several elsewhere, `ger checkout` exits `4` and lists each candidate as `change:N (triplet, status)`, so you can name one.

The current patchset is checked out unless the changeish names one (a `refs/changes/…` ref) or `--patchset N` does.

---

## The branch

A change with no local version goes on a new branch, **`change-<number>`**, the name `ger review` uses too; `-b NAME` picks another. The patchset and the target branch are fetched from `gerrit.remote` in one `git fetch` (`refs/changes/…` plus `+refs/heads/<target>:refs/remotes/<remote>/<target>`), so the upstream exists and is current. The branch's upstream is set to `<remote>/<target>`: `origin/main` for a change destined for `main`.

A branch called `NAME` (or `change-<number>`) that already exists but holds no version of the change is not touched: `ger checkout` exits `1` and says to pick another name or pass `--force`, which resets it to the patchset. When that drops commits the patchset does not have, the old tip is printed on stderr.

---

## The local version

A **local version** of the change is a commit carrying its Change-Id on a local branch, above `gerrit.remote` (`git log --branches --not --remotes=<remote>`). A branch that pushes to another Gerrit branch holds a cherry-pick with the same Change-Id, not a version of this change, and does not count. A branch with no Gerrit destination does count. ger's own bookkeeping branches (`lastPush/<branch>`, the marker `ger push` moves) never count: they are not named in a note, and `ger checkout` never switches onto one (`git_state.is_bookkeeping_branch`).

When the change has a local version, `ger checkout` does not make a second copy. Which branch it goes to, in one line (also in `ger checkout --help`): a branch whose tip is the change wins; else it goes to (or stays on) the branch that holds the change and says how many commits below HEAD it is. So on a stack branch, `ger checkout <a lower change>` switches to that change's own branch when one ends at it, and otherwise stays put with `already on <branch>: … is N commit(s) below HEAD`. The full order is under **Several local versions** below. What it does with the local version depends on how it relates to the patchset:

| Local version | What happens |
|---|---|
| **Is the patchset** | Switch to its branch and say so (`already on …` when you are there) |
| **Another patchset of the change** (its commit is a revision Gerrit knows, so it was pushed) | Ask `Update it to patchset N?` (default yes). Yes updates it, no switches to the branch unchanged. `--update` and `--no-update` answer without asking |
| **Never pushed in that form** (Gerrit has no revision with its commit: you edited it) | Never overwritten silently. `ger checkout` exits `1`, names the commit, and offers the ways out: `--no-update` switches to it as it is, `-b NAME` checks the patchset out on another branch, `--force` replaces your version (the old tip is printed) |

**Without a terminal**, the update question is not guessed: `ger checkout` exits `1` without changing anything and says to pass `--update` or `--no-update`.

**Several local versions** (the change sits on more than one branch): the first rule that leaves exactly one decides — a branch whose tip is the change (the current branch's first), then the branch named for it (`change-<number>`), then the current branch's, then the one that already is the patchset. So after `ger checkout 885` and `ger checkout 886` (stacked on 885), `ger checkout 885` goes back to `change-885`. Only when no rule decides does `ger checkout` exit `1` and list the branches; `-b BRANCH` names the one to use. With `-b NAME`, only the local version on `NAME` counts; local versions on other branches are mentioned in a note.

`--patchset` may name an older patchset than the local version's; that is an update like any other, reported as `moved <branch> from patchset 5 to … patchset 2` rather than `updated`.

### What an update does

The local version's commit is replaced by the patchset, and **the commits above it on the branch are rebased onto the patchset** (`git rebase --onto <patchset> <local version> <branch>`). That is what you expect after a colleague (or you, elsewhere) uploaded a new patchset under work you stacked on top: your commits stay, on the new patchset.

What is below the local version is replaced by what is below the patchset on Gerrit. That loses nothing: the local version was pushed, so everything below it was pushed too.

Before rebasing, each commit above is replayed in the object store (`git merge-tree`, as `ger serve`'s stack moves preview). When one would conflict, `ger checkout` exits `1` before changing anything, names the commit and the files, and says how to do it by hand or how to check the patchset out on its own branch with `-b NAME`. A git too old for the preview tries the rebase and aborts it on a conflict, which leaves the branch as it was.

A forced update of a version that was never pushed works the same way; the branch's old tip is printed on stderr so your version can be recovered.

A local version's branch keeps its upstream. One with no Gerrit destination gets `<remote>/<target>` as its upstream.

---

## Leaving your work alone

Before anything moves, `ger checkout` checks that the working tree and index hold no changes to tracked files. Otherwise it exits `2` (`USAGE`: a state you fix, then rerun, as for `ger edit`), lists the files, and says to commit or stash them, in the wording `ger review` uses. Untracked files do not count; git itself refuses when the switch would overwrite one. When there is nothing to do (you are already on the local version and it is the patchset), the check is skipped.

A rebase in progress stops `ger checkout` before it asks Gerrit anything (exit `2`, with the `git rebase --continue` / `--abort` hint every history-moving command gives).

---

## Output

```
checked out change 1234 patchset 4 "Extract command routing" on change-1234
  upstream: origin/main
  https://host/c/proj/+/1234
```

The first line is one of: `checked out … on <branch>`, `updated <branch> from patchset 3 to change 1234 patchset 4 "…", rebased the 2 commit(s) above it`, `switched to <branch>: it has …` (`already on <branch>: …`; when the change sits below the branch tip, `already on <branch>: change 1234 patchset 4 "…" is 2 commit(s) below HEAD`, and HEAD stays where it is), `switched to <branch>, left at patchset 3 (Gerrit has patchset 4)`, `switched to <branch>, left as it is: your version differs from patchset 4`.

---

## Exit codes

| Code | When |
|------|------|
| `0` | Done, including switching to a local version you chose not to update |
| `1` | Needs your decision: the local version holds edits Gerrit never had; the update question has no terminal to ask on; several local versions no rule decides between; `-b NAME` is taken; the commits above would not rebase cleanly |
| `2` | Bad usage; a rebase is in progress; changes to tracked files are in the way |
| `3` | The changeish or patchset resolves to nothing |
| `4` | The Change-Id names changes on several target branches and narrowing could not pick one |
| `5` | Gerrit failed |
| `6` | `gerrit.webUrl` or credentials missing |
| `7` | A git command failed (fetch, switch). When the remote does not have the patchset's `refs/changes/…` ref, git's message is followed by one line saying what is most likely wrong: for a private change (the change data already says so; no extra call), that Gerrit does not serve a private change's refs to `gerrit.remote` as it signs in (anonymous, or an HTTP URL without `/a/`), so fetch over SSH or an authenticated `/a/` URL; otherwise that the remote does not have the ref and `gerrit.remote` should be checked (`core/gerrit_patchset.py`, shared with `ger review`) |
