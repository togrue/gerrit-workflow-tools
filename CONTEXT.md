# ger — Gerrit stacked reviews

Vocabulary for **`ger`**, a CLI for working with stacked Gerrit reviews: several local commits, each carrying its own Change-Id, pushed as a chain of dependent changes. Terms here are binding — use them in code, docs, commit messages and design discussions.

## Language

### The local side

**Local stack**:
The commits in `upstream_tip..HEAD` (or an explicit rev-range). Not "every open Gerrit change" — only what sits locally above the tracking branch.
_Avoid_: chain, series, branch commits

**Ready boundary**:
The first commit in the **local stack** whose subject matches the configured stop pattern. Everything below it is pushable; it and everything above are held back.
_Avoid_: cutoff, watermark, stop commit

**Stack context**:
The `(project, target branch, push branch)` triple resolved from **Settings** and the remote URL. Every **triplet** is built from it, and every bare **Change-Id** is narrowed against it.
_Avoid_: repo context, gerrit config

**Target branch** / **push branch**:
The **target branch** is the Gerrit destination (may be `origin/main`). The **push branch** is the branch segment inside `refs/for/<branch>` (`main`). They differ whenever the target is written in remote-tracking form.
_Avoid_: using either name for the other

**Settings**:
An immutable snapshot of the repository's effective `git config`, read once per command with a single `git config --list`. Values written after the snapshot are not visible in it; the answer is a new snapshot, not an invalidation. Settings only — nothing in it asks what the repository currently looks like.
_Avoid_: config, config cache, options

**Repository state**:
What the repository looks like right now — branch, detached HEAD, in-progress rebase, upstream, remote-tracking refs, the Gerrit push destination. Answered by running git, not by reading **Settings**, though interpreting the answer may consult them (e.g. whether the upstream's remote is `gerrit.remote`). The dependency runs one way: **Settings** never asks about **repository state**.
_Avoid_: git config (for these), environment, context

### Identity

**Change-Id**:
The `I…` footer on a commit message: `I` plus 40 hex digits, either case. Not unique on its own — the same Change-Id can exist on several branches.
_Avoid_: change id (unqualified), CID

**Footer value** vs **Change-Id**:
The **footer value** is whatever follows `Change-Id:` on a commit's last line, valid or not. It becomes a **Change-Id** only once validated. Keeping them distinct is what lets a garbage footer be reported as *invalid* rather than *absent* — collapse them and the two look identical.
_Avoid_: calling an unvalidated footer value a Change-Id

**Triplet**:
`project~branch~Change-Id` — Gerrit's canonical key for a review. The cache primary key and the path segment for follow-up REST calls.
_Avoid_: change key, full id

**Changeish**:
Any input that might name a commit or a Gerrit change: a git rev, **Change-Id**, **triplet**, `change:N`, `refs/changes/…` ref, Gerrit URL, or `q:` query. Resolution classifies it before acting on it.
_Avoid_: ref, target, identifier

**Resolution note**:
The one-line stderr message explaining that a bare **Change-Id** matched changes on more than one branch and which one was used. Derived from the local cache, never from a per-Change-Id re-query.
_Avoid_: warning, disambiguation message

### The Gerrit side

**Gerrit overlay**:
The step that takes local commit rows and returns them enriched with Gerrit state — patchset status, votes, unresolved comments, CI checks, reviewers. Shared by `ger log`, `ger show` and the rebase enricher.
_Avoid_: enrichment, sync, fetch

**Patchset status**:
How the local commit relates to Gerrit's current patch set: `active`, `newer`, `outdated`, `absent`, or one of the merged variants (`merged-same`, `merged-drift`, `merged-unknown`).
_Avoid_: state, sync status

**Annotated stack**:
The **local stack** after the **Gerrit overlay** and **attention** have been applied — what `ger log` prints, `ger edit --first-attention-commit` searches, and the rebase enricher annotates a todo from. Not a separate fetch: it is the local stack plus what Gerrit knows about it.
_Avoid_: stack view, review stack, enriched commits

**Attention**:
The verdict that a commit needs the author's action — unresolved comments, failed CI, missing Code-Review +2, or a blocked position in the chain. Drives `ger log`'s exit code and `ger edit --first-attention-commit`.
_Avoid_: needs work, flagged, blocked

**Reviewer strategy**:
How reviewers get applied on push: `push` (magic ref option), `lazy` (REST, only where none exist), `overwrite` (REST, replace on every change).
_Avoid_: reviewer mode, assignment policy

### Talking to Gerrit

**GerritRest**:
The set of single-round-trip Gerrit operations — one call, one response. Batching, chunking, aliasing and caching sit *above* it, never inside it.
_Avoid_: GerritApi, client, transport

**HttpGerritRest**:
The **GerritRest** implementation that speaks HTTP to a real Gerrit. Credentials are resolved when it is constructed, not per request.
_Avoid_: GerritClient, real client

**ChangeStore**:
The **GerritRest** implementation backed by a dictionary of ChangeInfo payloads. Lives under `tests/` and is not shipped — no `ger` command constructs one. Stateful — writes update the payloads, so reads see them. Fed by authored payloads in unit tests and by recorded payloads when replaying integration fixtures.
_Avoid_: mock, fake client, stub

**Trust window**:
How long a cached ChangeInfo is served without re-checking Gerrit. Offline / cache-only operation is an unbounded trust window with the freshness probe skipped — it is a policy, not a separate **GerritRest**.
_Avoid_: TTL, cache timeout, offline mode

## Flagged ambiguities

**"Client" is overloaded.**
It has meant the HTTP object, the layered service, and the CLI caller. Resolution: say **HttpGerritRest** for the HTTP implementation, `GerritService` for the cache-and-batch layer above it, and "command" for the CLI caller.

## Example dialogue

> **Dev:** `ger log` shows this commit as `absent`, but the change definitely exists on Gerrit.
>
> **Maintainer:** Absent means no change on your **push branch**. Your **stack context** resolved the push branch as `main` — is the change on `release/2.1`?
>
> **Dev:** It is. So the **Change-Id** matched, just on the wrong branch?
>
> **Maintainer:** Right. The **triplet** is what identifies it, and yours points at `main`. You should have got a **resolution note** on stderr saying it matched on another branch.
>
> **Dev:** I did, I ignored it. Can I make the **Gerrit overlay** look at both branches?
>
> **Maintainer:** No — the overlay reports against your push target, deliberately. If you want that change specifically, pass the **triplet** to `ger show`.
>
> **Dev:** And when I'm on a plane with no network?
>
> **Maintainer:** That's an unbounded **trust window**, not a different Gerrit. Same **GerritRest**, cache served without the freshness probe.
>
> **Dev:** Last thing — I set `gerrit.stopPattern` halfway through a run and nothing changed.
>
> **Maintainer:** **Settings** are a snapshot taken at the entry point. A write after that is invisible by design — otherwise a command could see two different configurations while it runs. Re-run it.
