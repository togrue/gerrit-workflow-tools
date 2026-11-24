# `ger rebase`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_rebase.py`, `rebase_enricher.py` |
| **Requires** | Gerrit enrichment: `ger.webUrl` + credentials |

Start **`git rebase -i`** with Gerrit status annotations on each `pick` line (patchset, Verified, CR, comments, attention note).

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
| `--onto-remote` | Rebase onto fetched `refs/remotes/<ger.remote>/<target>` tip |
| `--no-onto-remote` | Force merge-base behavior (overrides `ger.rebaseOntoRemote`) |
| `--drop-merged-equivalent` | Mark provably merged-equivalent commits as `drop` in todo |
| `--autosquash` | Fold `fixup!` / `squash!` commits (what [`ger fix`](fix.md) makes) into their targets: `git rebase -i --autosquash`. The way `ger fix`, `ger log` and `ger push` tell you to squash one. Without it, git's `rebase.autosquash` decides, as for a plain `git rebase -i`: you see the todo either way |
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
Without a rebase they refuse (`no rebase is in progress`, naming a `git am` when one is underway
instead). Each refusal exits `2` and changes nothing:

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

A rebase that stops on a conflict ends with the shared next step ([edit.md](edit.md#behavior)): resolve the paths, `git add` them, then `git rebase --continue`, or `git rebase --abort`.

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
