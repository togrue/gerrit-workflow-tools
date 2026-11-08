# Comment anchors are immutable

A local comment's **anchor** is the blob SHA of the file it was written on, plus the range in that blob, plus the anchored text. It is written once and never updated.

## The alternative

Remap the comment after every rebase and write the new position back. Two problems:

- **Error accumulates.** Each remap is a diff with its own fuzz. Chaining them across ten rebases drifts in a way no single diff would.
- **Worktrees disagree.** Two worktrees can hold different versions of the same change, for example one mid-rebase. Each would write back its own position, and they would overwrite each other through the shared ref ([ADR-0010](0010-local-comments-live-in-a-git-ref.md)).

## The decision

Mapping is a pure function: (anchor blob, range) and the blob being shown give the position there. It is always one diff, from the original blob straight to the target, so it never compounds. It is computed when comments are read, not in a rebase hook, so rewrites made outside ger (`git rebase -i`, an IDE, jj) are handled the same way. Results can be cached by (anchor blob, target blob) because both are content-addressed.

Keying (which change a comment belongs to) is separate from anchoring (where in the file it sits). A squash that removes a change's Change-Id loses the key, not the anchor: the anchored blob still identifies the text, so the comment can be found in the commit that now contains it.

## Consequences

- The store keeps every anchored blob reachable (ADR-0010).
- A comment whose anchored lines changed is shown as outdated next to where they were, with the original text. It is never dropped.
- Nothing is written to the store when comments are only read.
