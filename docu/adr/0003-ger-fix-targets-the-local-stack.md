# `ger fix` targets the local stack

`ger fix` resolves its **changeish** to a commit on the **local stack** and fails when it does not find one. It never fetches. A **Change-Id** or **triplet** is matched against the stack directly, so `ger fix I…` needs no network at all; `change:<n>`, a `refs/changes/…` ref, a URL or a `q:` query still cost one Gerrit round trip to learn the Change-Id, and the stack match happens after that.

That is a narrowing. It previously resolved the argument through `change_resolution.resolve_changeish`, fetched the Gerrit revision when the commit was not present locally, and used the fetched SHA as the fixup target — the behaviour [spec/commands/fix.md](../spec/commands/fix.md) documented.

The narrowing is what the command is for. `ger fix` is a shortcut for a later rebase edit onto a commit you already have; a fixup aimed at a commit outside your stack is not something a subsequent `git rebase --autosquash` can act on. Resolution therefore uses `resolve_to_stack_sha` — the same **changeish**-to-stack-SHA path as `ger edit` and `ger rebase` — rather than a fourth resolution route of its own.

The fetch path was also quietly fragile. `git commit --fixup=<sha>` writes the subject `fixup! <subject of sha>`, and autosquash matches by **subject**, not SHA. Fetching a Gerrit revision produced a working fixup only while the fetched subject still matched the local commit's; once a local subject had drifted from what was on Gerrit, the command emitted a `fixup!` that autosquash silently never placed.

## Consequences

A change that exists on Gerrit but not on your stack is now an error rather than a fetch. Rebase onto it, or cherry-pick it, before running `ger fix`.

`cli_fix` no longer carries its own `refs/changes/…` regex, its own Gerrit implementation construction, or its own fetch helpers. A `refs/changes/…` argument is still accepted — it is classified as `change-ref` like everywhere else — but it now has to name a commit on the stack.

This is a deliberate difference from `ger show` and `ger resolve`, which do report on changes anywhere on Gerrit. Those commands read; `ger fix` writes a commit into your history, and the only history it can write into is your own.

## Addendum: a closed change is refused (#105)

Resolution still never asks Gerrit for a Change-Id, a triplet or a git rev. One question is asked after it: whether Gerrit already **merged** or **abandoned** the target's change. Git alone cannot tell, because a change submitted after your last `git fetch` is still on the local stack, and a `fixup!` for it goes nowhere. Such a target exits `2` (`USAGE`), with the same check and wording as `ger edit` and `ger reword` (`rewrite_refusal.closed_change_refusal`).

The question costs **at most one** request (`ChangeApi.on_push_branch`) and **none** while the cache holds the push branch's change inside the trust window ([ADR-0001](0001-offline-is-a-trust-window-not-a-gerritrest.md)). `tests/test_call_budgets.py` pins exactly that, cold and then warm. It stays optional: when Gerrit cannot be asked (no `ger.webUrl`, no credentials, unreachable), the git-only check stands and `ger fix I…` works offline as before.
