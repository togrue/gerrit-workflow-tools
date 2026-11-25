# `ger rebase`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_rebase.py`, `rebase_enricher.py` |
| **Requires** | Gerrit enrichment: `ger.webUrl` + credentials |

Start **`git rebase -i`** with Gerrit status annotations on each `pick` line (patchset, Verified, CR, comments, and the hints of the commit's `ger log` row: `render/status_fmt.hints_text`). A comment line `# ---- ready boundary: `ger push` sends the commits above ----` sits before the first commit `ger push` holds back, as in `ger log`, and one more right below the commands says what the tokens on them mean (`render/status_fmt.legend_line`, from the table `ger log --help` prints): `# p pushed · v/cr Verified/Code-Review vote (? none yet) · com unresolved comments`.

**Aliases:** `ger restack`, `ger stack` → `rebase`.

---

## Usage

```
ger rebase [options] [REV]
ger rebase --continue | --abort | --skip
```

`REV` — base commit, Change-Id, or ref (default: merge-base with target). Not used with `--onto-remote`.

Without `REV` the todo is replayed where it is, onto the fork point: nothing moves onto the
upstream's new commits. To rebase onto the upstream as fetched, name it: `ger rebase origin/main`.
That is the rebase `ger log` advises once the upstream moved on (`next: origin/main has 1 new
commit; …`), the one `GET /stack`'s `upstream` previews, and what the VS Code extension's
*Rebase onto origin/main* runs. Unlike `--onto-remote` it takes the upstream of the branch
whatever its remote, the same commits the local stack is counted from. With a commit of the stack merged on Gerrit,
that advice is `ger rebase origin/main --drop-merged-equivalent`: the flag applies to a `REV` as
it does to `--onto-remote`.

### Change resolution

`REV` is resolved by `resolve_stack_changeish`, the same path as [`ger fix`](fix.md) and
[`ger edit`](edit.md), so `change:<n>`, `refs/changes/…` refs, URLs and `q:` queries now work
here too — they previously failed with "cannot resolve without a Gerrit client".

One difference, deliberate: `ger rebase` does **not** require its target to be in the local
stack. `REV` is the commit to rebase *from*, which normally sits below the stack, whereas
`ger fix` / `ger edit` / `ger reword` rewrite a commit *in* it. Grammar:
[change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `--onto-remote` / `--no-onto-remote` | Rebase onto fetched `refs/remotes/<ger.remote>/<target>` tip; `--no-onto-remote` forces merge-base behavior (overrides `ger.rebaseOntoRemote`) |
| `--drop-merged-equivalent` / `--no-drop-merged-equivalent` | Mark provably merged-equivalent commits as `drop` in todo; `--no-…` keeps them even when `ger.rebaseDropMergedEquivalent` is on |
| `--autosquash` / `--no-autosquash` | Fold `fixup!` / `squash!` commits (what [`ger fix`](fix.md) makes) into their targets: `git rebase -i --autosquash`. The way `ger fix`, `ger log` and `ger push` tell you to squash one. `--no-autosquash` keeps every `fixup!` commit where it is whatever `rebase.autosquash` says. Without either, git's `rebase.autosquash` decides, as for a plain `git rebase -i`: you see the todo either way |
| `--continue` | Go on with the rebase underway: `git rebase --continue`. Refused while paths are still unmerged, with the shared next step, over unstaged changes to tracked files, and where nothing is left to apply |
| `--abort` | Stop the rebase underway: `git rebase --abort`, the branch back where it was |
| `--skip` | Leave out the commit the rebase stopped on with a conflict: `git rebase --skip`. Refused at any other stop, and where it would keep a commit made by hand and throw away work |
| `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |

Editor: enricher delegates to `GREBASE_EDITOR` / `GIT_EDITOR` / `core.editor` / `VISUAL` / `EDITOR`, first set wins. `GREBASE_EDITOR` edits only the todo (git's own editor, e.g. for a `squash` message, still comes from `GIT_EDITOR` onwards). Without a terminal (stdin not a TTY: scripts, agents) ger sets `GIT_EDITOR=true`, so unless `GREBASE_EDITOR` is set no editor opens: the todo runs as annotated, so `--autosquash` and `--drop-merged-equivalent` run unattended. A script that wants to edit the todo sets `GREBASE_EDITOR`.

Without a terminal, the two ways ger runs a rebase set `GIT_EDITOR` differently:

| | `GIT_EDITOR` without a terminal |
|--|--|
| `ger rebase [REV]` (runs the todo) | always `true`: a `GIT_EDITOR` the caller set is overridden; `GREBASE_EDITOR` still edits the todo |
| `ger rebase --continue` / `--skip`, `ger edit -n` | `true` only when the caller set none: a caller's `GIT_EDITOR` writes the messages |

### Going on with a rebase underway

`--continue`, `--abort` and `--skip` act on the rebase underway, whoever started it, and take no
`REV` or other option. They are what the VS Code extension's *Continue*, *Abort* and *Skip* run.
Without a rebase they refuse and say where the branch is (`no rebase is in progress, nothing to
continue: feature is at 98a454b Cleanup after experiment`), naming a `git am` when one is underway
instead. A finished `--abort` says the same of the branch it put back: `rebase aborted: feature is
at 6c07cc8 trig: add tan(), as before the rebase`. Each refusal exits `2` and changes nothing:

- `--continue` refuses while the index holds unresolved conflicts, with the shared next step, and
  over unstaged changes to tracked files, which git would refuse with a misleading "You must edit
  all merge conflicts": `git add` them into a conflict's resolution not committed yet, or at any
  other stop amend them in (`git commit --amend -a`), or `git stash` them, then continue. What is
  unstaged is git's own answer: the index refreshed first, then `git diff-files`, submodules left
  out, so a file only touched is not, and a line-ending change `core.autocrlf` sees is. Staged
  changes go on as in git: an `edit` stop's are amended in. Under a rebase through `am`, once the
  conflict was committed by hand and nothing is left uncommitted, it refuses too: git would stop
  again with "No changes"; `--skip` goes on from `HEAD`.
- `--skip` refuses unless the rebase stopped on a conflict (resolved or not) and the commit is not
  in yet: at an `edit` or a split stop, and once a conflict's resolution was committed by hand,
  the commit is already applied, so `git rebase --skip` would keep it and only reset away what is
  not committed. A resolution committed by hand that core cannot tell is the stopped commit
  (another subject, no Change-Id) can be skipped once nothing is left uncommitted: commit or
  `git stash` it first. The ways on a stop allows are core's one verdict, served on `GET /stack`
  as `rebase.actions`, with each refusal as `rebase.refused` ([serve.md](serve.md#get-stack)):
  `ger rebase` judges the stop as `/stack` does, so the two never disagree.

Without a terminal, `--continue` and `--skip` set `GIT_EDITOR=true` unless `GIT_EDITOR` is set
(unlike the todo run above, which sets it always): a resolved conflict is committed with its
message as it is, and so is any later `reword` or `squash` message in the todo. A script that
wants to write those sets `GIT_EDITOR`. `ger edit -n` goes on the same way. A stop on the next
conflict ends with the shared next step, as below.

A rebase that stops ends with ger's own lines, the same for every ger-started rebase
(`rebase_run.print_stop`, words from `rebase_next_step.stop_next_step`): where it stopped
(`stopped at <sha> <subject>`, `(conflict)` after it on a conflict), and one `next:` step that names
ger's way on, never raw git. Git's own advice is off for the invocation (`git -c advice.mergeConflict=false
-c advice.resolveConflict=false -c advice.waitingForEditor=false -c advice.skippedCherryPicks=false`,
never written to config; a git that does not know a key ignores it), and what no key turns off is
left out of its stderr (`rebase_run._GitsWords`): `hint:` lines (an older git's conflict hints),
`Rebasing (n/m)`, `Stopped at …`, `Could not apply …`, the `You can amend the commit now … git rebase
--continue` block of an `edit` stop and its likes for `exec`, and `Successfully rebased …`. Each
remaining line of git's stderr (errors, warnings) is printed on its own line; what the merge says of a
conflict (`CONFLICT (content): …`) is on stdout and stays as it is. Git localised to another
language passes through unfiltered.

| Stop | `next:` |
|--|--|
| conflict | resolve the conflicts in `<paths>`, `git add` them, then `ger rebase --continue` to finish; `ger rebase --skip` leaves this commit out, `ger rebase --abort` goes back to where the rebase started |
| conflict resolved and added | its conflicts are resolved: `ger rebase --continue` commits it and goes on; … |
| conflict git rerere resolved, staged (`rerere.autoUpdate`) | git reused a recorded resolution for `<paths>` (rerere): check it, then `ger rebase --continue` commits it and goes on; … |
| conflict git rerere resolved, not staged | git reused a recorded resolution for `<paths>` (rerere): check it, `git add` it, then `ger rebase --continue` to finish; … |
| `edit` | amend it (`git commit --amend`), then `ger rebase --continue` to finish |
| split | commit what is left of it, then `ger rebase --continue`; … |
| `exec`, `break` | `ger rebase --continue` once you are done here; … |

**A resolution git rerere reused is said to be rerere's.** With `rerere.enabled`, git puts a
resolution it recorded at an earlier conflict back in by itself, staged or not, and nobody has looked
at it: it may be an old, wrong one. A path is rerere's when the stop's merge conflicts in it line by
line (`reorder_preview.text_conflicts`, one `git merge-tree`) and rerere no longer lists it in
`MERGE_RR`, which keeps a path you resolve until the commit (`RebaseProgress.reused`). The stop line
reads `stopped at … (conflict, resolved by git rerere)` when none is left unmerged, `ger log`'s note
ends `git reused a recorded resolution for README.md (rerere): check it before `ger rebase
--continue``, and `GET /stack` serves `rebase.reused`. Nothing in ger goes on by itself from such a
stop: `ger rebase --continue` and `ger edit -n` go on when you run them.

`ger rebase --continue` rather than `git rebase --continue`: it refuses over unstaged changes, which
git misreports, and without a terminal takes a message as it is instead of failing on a missing
editor. The refusals name the same commands (`ger rebase --skip goes on from HEAD`, …), and so does
every history-moving command a rebase underway stops (`a rebase is already in progress; finish it
(`ger rebase --continue`) or `ger rebase --abort` first`).

### After a rebase finishes

Git's `Successfully rebased and updated refs/heads/…` is not shown. Every ger-started
rebase that finishes (`ger rebase [REV]`, `--autosquash`, `--continue`, `--skip`, `ger edit`,
`ger reword`, `ger edit -n`) ends with ger's own, against the local stack before it (the stack at
`HEAD` when ger started the rebase, or at the rebase's `orig-head` for `--continue` / `--skip`);
`core/rewrite_outcome.py` tells it as data, `rebase_run.print_outcome` says it:

```text
dropped 6c07cc8 trig: add tan(); its change 2116 stays open on Gerrit
to get it back: git cherry-pick 6c07cc8
rebase finished: dropped 1 commit; rewrote 1 commit, now ahead of Gerrit
next: `ger push` uploads the stack as it is now
```

- Each commit the stack lost, unless squashed (a `fixup!` whose commit was rewritten) or already in
  the upstream it was rebased onto, is **dropped**: named with its change when the cache has it
  open on Gerrit (dropping abandons nothing), and the way to get it back, `git cherry-pick <sha>`.
  `--skip` that stops on a further conflict says so at once, with `once the rebase is finished,
  git cherry-pick <sha> (or `ger rebase --abort` now)`. Nothing is asked first: the drop is said
  and can be taken back.
- `rebase finished:` counts the commits rewritten (new ids) and, of them, how many are now ahead
  of Gerrit and how many not on Gerrit yet; fixups squashed; commits left out as already upstream.
  `nothing changed, the local stack is as it was (3 commits)` when it changed nothing. Whether a
  change is on Gerrit comes from the cache a `ger log` or the todo's annotations left, never from a
  request: a change's existence does not go stale the way its votes do. Without `ger.webUrl` the
  Gerrit part is left out.
- `next:` is `` `ger push` uploads the stack as it is now`` when anything changed, `squash it with
  `ger rebase --autosquash`` while a fixup is left in the stack (`ger push` would stop at it), and
  nothing during an attention walk, whose next stop is the next step.

---

## Configuration

| Key | Default | Effect |
|-----|---------|--------|
| `ger.rebaseOntoRemote` | off | Default for `--onto-remote` |
| `ger.rebaseDropMergedEquivalent` | off | Default for `--drop-merged-equivalent` |

---

## See also

- [`ger log`](log.md)
- [`ger edit`](edit.md)
