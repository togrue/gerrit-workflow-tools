# Attention cursor is Change-Id walk state

The **attention cursor** is branch-local walk state for iterative edit-attention rework: a Change-Id and a timestamp, plus an append-only history under `.git/ger/`. It is not a second attention verdict, not a SHA, and not Gerrit state.

`ger edit --first-attention-commit` always jumped to the oldest edit-attention commit. Rework is tip-ward and often skips a still-red CI commit until after push. Bare `ger edit` therefore **resumes** (cursor still needs work → that commit; otherwise next above it). Reset is explicit (`--first-attention-commit`). Force skip is `-n` / `--next`.

## Why Change-Id

Rebase and amend rewrite SHAs. Gerrit re-download creates a new local branch with different parents but the same Change-Ids. Storing a SHA or stack index would go stale; storing a Change-Id resolves lazily against the current annotated stack.

## TTL and adopt

`ger.attentionCursorTtl` (default `14d`) always clears an expired cursor without prompting. Repo-level `ger.lastAttentionChangeId` enables **lazy adopt** when a branch has no cursor but that Change-Id is in its stack. Expiry also clears the repo last so adopt cannot revive a stale walk in the same step.

## Consequences

- Cursor writes happen from successful `ger edit` targeting (and `--set-cursor`), not from `ger show` / `ger sha` attention queries.
- History is local recovery (`--cursor-history`), not a sync protocol.
- Core owns storage and selection (`attention_cursor.py`, `edit_attention_walk.py`); the CLI never re-derives edit attention.
