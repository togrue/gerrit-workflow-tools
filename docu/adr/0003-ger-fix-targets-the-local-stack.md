# `ger fix` targets the local stack

`ger fix` resolves its **changeish** to a commit on the **local stack** and fails when it does not find one. It never fetches. A **Change-Id** or **triplet** is matched against the stack directly, so `ger fix I…` needs no network at all; `change:<n>`, a `refs/changes/…` ref, a URL or a `q:` query still cost one Gerrit round trip to learn the Change-Id, and the stack match happens after that.

That is a narrowing. It previously resolved the argument through `change_resolution.resolve_changeish`, fetched the Gerrit revision when the commit was not present locally, and used the fetched SHA as the fixup target — the behaviour [spec/commands/fix.md](../spec/commands/fix.md) documented.

The narrowing is what the command is for. `ger fix` is a shortcut for a later rebase edit onto a commit you already have; a fixup aimed at a commit outside your stack is not something a subsequent `git rebase --autosquash` can act on. Resolution therefore uses `resolve_to_stack_sha` — the same **changeish**-to-stack-SHA path as `ger edit` and `ger rebase` — rather than a fourth resolution route of its own.

The fetch path was also quietly fragile. `git commit --fixup=<sha>` writes the subject `fixup! <subject of sha>`, and autosquash matches by **subject**, not SHA. Fetching a Gerrit revision produced a working fixup only while the fetched subject still matched the local commit's; once a local subject had drifted from what was on Gerrit, the command emitted a `fixup!` that autosquash silently never placed.

## Consequences

A change that exists on Gerrit but not on your stack is now an error rather than a fetch. Rebase onto it, or cherry-pick it, before running `ger fix`.

`cli_fix` no longer carries its own `refs/changes/…` regex, its own Gerrit implementation construction, or its own fetch helpers. A `refs/changes/…` argument is still accepted — it is classified as `change-ref` like everywhere else — but it now has to name a commit on the stack.

This is a deliberate difference from `ger show` and `ger resolve`, which do report on changes anywhere on Gerrit. Those commands read; `ger fix` writes a commit into your history, and the only history it can write into is your own.
