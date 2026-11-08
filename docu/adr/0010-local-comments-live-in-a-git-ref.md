# Local comments live in a git ref

Notes and local drafts (comments that are not on Gerrit) are stored in the repository itself, under `refs/ger/comments`, not in the SQLite cache and not in git notes.

## Why not the SQLite cache

The cache is a trust window over Gerrit state ([ADR-0001](0001-offline-is-a-trust-window-not-a-gerritrest.md)): every row in it can be thrown away and fetched again. Local comments cannot. They are the only copy, so they must not sit somewhere `ger cache clear` empties.

## Why not git notes

Git notes are keyed by commit SHA. Rebase, amend and squash change the SHA, and notes only follow when `notes.rewriteRef` is configured. ger already has an identity that survives rewrites, the **triplet**. Keying by SHA would add a second one.

## What the ref gives

- **Shared by every worktree.** Only `HEAD`, `refs/worktree/`, `refs/bisect/` and `refs/rewritten/` are per worktree. A comment written in the review worktree is visible in the main checkout.
- **History.** Each write is a commit, so a mistake is recoverable with `git log` on the ref.
- **gc safety for anchors.** The tree points at every blob a comment is anchored to ([ADR-0011](0011-comment-anchors-are-immutable.md)), so `git gc` cannot delete the file version a mapping starts from, even after the commit it came from is gone.
- **Optional sync.** The ref can be pushed to a personal remote. ger never pushes it, and never to Gerrit.

## How writes work

- **Plumbing only**, in a temporary index file (`GIT_INDEX_FILE`): `read-tree`, `hash-object`, `update-index`, `write-tree`, `commit-tree`. Your index and working tree are never touched, so a write works mid-rebase and during a staged review.
- **Compare-and-swap** on the ref: `git update-ref <ref> <new> <old>`. A writer that lost the race re-reads the ref and applies its *operation* (add or remove one comment) again on the new tip. Re-applying an operation, rather than merging trees, is what makes concurrent writers safe without a merge strategy.
- **One file per change** (`changes/<triplet>.json`). Readers of one change read one blob.

## Consequences

- `core/local_comments.py` is the one owner of the ref. Nothing else reads or writes it.
- Comments survive `ger cache clear`, a re-clone does not carry them, and `git gc` keeps them.
- Separate clones do not share comments unless the user pushes and fetches the ref.
