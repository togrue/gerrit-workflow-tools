# Diverged is decided by history, not by having the commit

A `newer` commit has **diverged** when Gerrit's current patchset of its change is one it was not
built on: somebody (a reviewer's web edit, another checkout) uploaded it, and pushing the local
commit would replace that work. `ger log` flags it and `ger push` refuses it.

The first version decided this by whether the repository had the patchset's commit object: a
patchset you pushed from here, or took with `ger checkout --update`, is in the object store, one
uploaded elsewhere is not. But objects arrive without anything being built on them. A refused
`ger checkout <change> --update` (its merge conflicts: "nothing was changed") has already fetched
the patchset; so has a `git fetch refs/changes/…` or a `ger review` of the change. After any of
them `ger log` said `ahead of gerrit` and `ger push` uploaded over the patchset the user never
integrated. A refusal that removes the guard it refused for is the opposite of "nothing was changed".

## Decision

Gerrit's current patchset counts as integrated only when it is **in the local commit's history**
(`core/patchset_history.py`, `built_on`):

- reachable from a branch holding the local commit, as it is now or **as it was** (the branch's
  reflog): pushing a commit and then amending or rebasing it leaves the pushed one there, so a
  push from here followed by local work is `ahead`, not diverged;
- or reachable from ger push's marker for that branch, `lastPush/<branch>` (and its reflog),
  which still holds what was pushed once the reflog entry has expired;
- or **recorded as taken over by ger**: `ger checkout --update` merging your edits into a newer
  patchset writes `refs/ger/taken/<patchset>` pointing at the commit it built, and the patchset
  counts while that commit is in the branch's history. The merged commit sits where the
  patchset sits rather than on it (it is pushed as the next patchset of the same change), so
  ancestry alone cannot show it was built on it.

The walk stops at the upstream of each branch holding the commits (an open change's patchset is
never on its target branch). It is three git calls for the whole overlay, made only when a
commit is `newer`.

Other branches do not count: `ger review` and `ger checkout -b NAME` put a patchset on a branch
of its own to read it, which does not make your version built on it.

## Consequences

- `CONTEXT.md` defines **diverged** by history. `gerrit_change_status.is_diverged` is the one
  decision; `ger log`, `ger show`, `ger serve` read it from the **Gerrit overlay**, and the push
  guard must use the same function rather than asking for the object.
- A patchset you integrated by hand (fetched it, merged it in yourself, amended) still reads as
  diverged: ger cannot tell your hand merge from an unrelated edit. `ger checkout <change>
  --force` takes the patchset onto the branch (it is then in its history) and prints your old tip
  to re-apply your edits on; `ger push --force` overrides the guard.
- With `core.logAllRefUpdates=false` and `ger.lastPushedBranch=false`, an amend after a push is
  reported as diverged: nothing records that the pushed commit was ever yours. That is the safe
  side of the mistake.
- `refs/ger/taken/` grows by one ref per merge ger performs and is never pruned yet; each ref
  keeps one commit alive for `git gc`.

## Considered options

**Keep object presence, fetch into a scratch object store.** A refused update could fetch into a
temporary object directory and drop it. Rejected: it fixes one path and leaves `git fetch`,
`ger review` and every other way an object arrives.

**Every local branch's reflog.** Simpler, but `ger review` creates `change-<number>` at the
patchset, so reviewing a change would hide its divergence.

**A trailer in the commit message** naming the patchset taken. Rejected: it would be uploaded to
Gerrit with the commit.
