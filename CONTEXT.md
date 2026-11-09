# ger — Gerrit stacked reviews

Vocabulary for **`ger`**, a CLI for working with stacked Gerrit reviews: several local commits, each carrying its own Change-Id, pushed as a chain of dependent changes. Terms here are binding — use them in code, docs, commit messages and design discussions.

## Language

### The local side

**Commit row**:
One commit as read by a single `git log` — sha, short sha, subject, body, and the **footer value** exactly as written. A commit row is not yet a **local stack** member: reading every ref, or reading named revisions directly, produces rows that are nowhere in the stack. Its footer is not yet validated either, so a row may carry a garbage **footer value** — whoever is about to ask Gerrit about it validates first.
_Avoid_: commit, log line, record

**Local stack**:
The commits in `upstream_tip..HEAD` (or an explicit rev-range). Not "every open Gerrit change" — only what sits locally above the tracking branch. A selection of **commit rows**, not a separate kind of thing. Loaded one way, `get_stack_snapshot`; the overlay, ready strategies and the highlighter all take the same `Commit` rows.
_Avoid_: chain, series, branch commits

**Ready boundary**:
The first commit in the **local stack** that a ready-boundary strategy (or the configured stop pattern) marks as blocking. Everything below it is pushable; it and everything above are held back.
_Avoid_: cutoff, watermark, stop commit

**Stack context**:
The `(project, target branch, push branch)` triple resolved from **Settings** and the remote URL. Every **triplet** is built from it, and every bare **Change-Id** is narrowed against it.
_Avoid_: repo context, gerrit config

**Target branch** / **push branch**:
The **target branch** is the Gerrit destination (may be `origin/main`). The **push branch** is the branch segment inside `refs/for/<branch>` (`main`). They differ whenever the target is written in remote-tracking form.
_Avoid_: using either name for the other

**Settings**:
An immutable snapshot of the repository's effective `git config`, read once per command with a single `git config --list --show-scope`. Carries each key's value and the scope that won (`local`, `global`, …). Values written after the snapshot are not visible in it; the answer is a new snapshot, not an invalidation. Settings only — nothing in it asks what the repository currently looks like.
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
The **local stack** after the **Gerrit overlay** and **attention** have been applied — what `ger log` prints, `ger edit` attention targeting searches, and the rebase enricher annotates a todo from. Not a separate fetch: it is the local stack plus what Gerrit knows about it.
_Avoid_: stack view, review stack, enriched commits

**Attention**:
The verdict that a commit needs the author's action — unresolved comments, failed CI, missing Code-Review +2, or a blocked position in the chain. Drives `ger log`'s exit code. A subset (**edit attention**: unresolved comments or failed CI) drives the attention walk.
_Avoid_: needs work, flagged, blocked

**Attention cursor**:
Branch-local walk position for iterative edit-attention rework: a Change-Id (+ timestamp) naming the last stop on that branch. Survives rebase SHA churn; history lives under `.git/ger/`. Not Gerrit state.
_Avoid_: bookmark, progress marker, current attention commit (unqualified)

**Attention walk**:
Tip-ward iteration over edit-attention commits driven by the **attention cursor**: resume on the cursor when it still needs edit attention, otherwise the next above it; `--next` forces tip-ward skip; `--first-attention-commit` resets to the oldest.
_Avoid_: attention loop, rework session

**Review chain**:
A maximal set of open Gerrit changes linked by current-revision parent/child SHAs — Gerrit's relation chain, seen from the server. One chain is one review unit. Distinct from the **local stack**, which is always yours and defined only locally. Usually someone else's; may have no local commits at all.
_Avoid_: stack (unqualified), series, relation chain (in user-facing text)

**Chain top** / **chain base**:
**Chain top** is the member whose current revision is not the parent of any other member — the change whose Gerrit URL the inbox prints. **Chain base** is the member whose current-revision parent is not a member (it sits on the target branch).
_Avoid_: tip, root, HEAD of the chain

**Unreviewed age**:
How long a **review chain** has waited for *your* review. Prefer the attention-set timestamp for self; fall back to "current patch set after my last Code-Review vote". The inbox sort key.
_Avoid_: wait age (that is last activity), stale time

**Wait age**:
Time since the last activity on a **review chain**: `now − max(updated)` over members. Shown next to unreviewed age so you can tell "waiting on me for 3d, last touched 4h ago" from a dead chain.
_Avoid_: using this for "how long since I reviewed"

**Inbox section**:
One Gerrit query plus the **review chains** assembled from it. `ger inbox` currently ships **to review** (open chains where you are a reviewer). *Waiting on others* is planned.
_Avoid_: inbox tab, queue (unqualified)

**Reviewer strategy**:
How reviewers get applied on push: `push` (magic ref option), `lazy` (REST, only where none exist), `overwrite` (REST, replace reviewers on every change; CC stays).
_Avoid_: reviewer mode, assignment policy

**Gerrit push**:
A push of the commits the **ready boundary** allows to `refs/for/<push branch>`.
_Avoid_: gpush

**Vanilla push**:
A plain `git push` to the branch upstream, used when that upstream is not the Gerrit remote. The **ready boundary** and **reviewer strategy** do not apply.
_Avoid_: normal push, non-gerrit push

**Push plan**:
The destination and reviewer options for one push, plus the commits below the **ready boundary**. The commit that holds the rest of the **local stack** back is named beside that list, not included in it. When **Settings** ask for the preview, each listed commit carries its current reviewer and WIP marks beside the ones about to be sent. Replacing reviewers, topic, WIP, private, or the **reviewer strategy** leaves the list as it was.
_Avoid_: push context, gerrit context

**Push refusal**:
The outcome when a push will not run: an empty ready range, a rebase check that refuses, or no destination. An empty ready range names the holding-back commit and whether `--all` would leave commits below the **ready boundary**.
_Avoid_: push error, nothing to push

**CI link**:
A useful URL for a failed build (often a Jenkins console), after a project-specific transform. Shown by `ger log -v` and in JSON as `ci_links`. Distinct from raw Checks **names** (`ci_failures`).
_Avoid_: build URL (unqualified), CI failure (the name alone)

**CI strategy**:
Callable (built-in or under ``.ger/ci/registry.py``, keyed by ``gerrit.project``) that turns Checks rows and/or change messages into **CI links**. Built-in parsing covers common Jenkins Gerrit trigger messages; override per project when needed.
_Avoid_: CI plugin, hook script, config regex

**CI content provider**:
Matches a **CI link** URL (by host/pattern) and knows how to fetch and parse what sits behind it. Distinct from a **CI strategy**, which only discovers and transforms links. Built-in providers (e.g. Jenkins) sit behind this seam; they are not domain concepts of their own.
_Avoid_: link content provider, CI plugin, log scraper (unqualified)

**CI content**:
What a **CI content provider** returns for one **CI link**: **provenance**, **CI findings**, and an optional body (raw or truncated log text). One result shape; views differ by which parts a command asks for.
_Avoid_: CI log (unqualified), build output, console dump

**CI finding**:
One normalized, fingerprintable failure or diagnostic extracted from **CI content** (tool/kind, path, line, message, …). What `ger show` attaches when CI content is enabled, and what `ger ci` prints by default.
_Avoid_: error (unqualified), log line, failure (the Checks name alone)

**Introduced findings**:
**CI findings** on a change that are not present on its parent’s findings. A planned follow-up for richer edit-attention stops; not part of the first `ger ci` / `ger show` slice.
_Avoid_: new errors, delta failures, regression set

### Reviewing and comments

**Review checkout**:
Where `ger review` checks a change out: the **review worktree** (one reusable worktree beside the main one, the default) or **in place** (the current worktree). Set by `ger.reviewCheckout`. Either way the change sits on a `change-<number>` branch.
_Avoid_: review clone, sandbox

**Staged review**:
A review checkout with HEAD detached at the change's parent and the change's diff in the index, so every git tool shows the commit as staged changes. The `change-<number>` branch stays on the change.
_Avoid_: soft reset mode, diff mode

**Review state**:
What is being reviewed in one worktree — change, patchset, commit, staged or not — recorded under that worktree's git dir. Not Gerrit state, and not the **attention cursor**.
_Avoid_: current review (unqualified), session

**Comment state**:
Where a comment is in its life: **note** (local, private, never uploaded), **local draft** (local, meant for Gerrit), **draft** (on Gerrit, visible only to you), **published**. "Private" is not a comment state: in Gerrit it names a kind of *change*.
_Avoid_: private comment, pending comment, unsent comment

**Local comment store**:
The `refs/ger/comments` ref holding every note and local draft of a repository, keyed by **triplet**. Shared by all worktrees. Not a cache: it is the only copy.
_Avoid_: comment cache, comment db, notes (for the store)

**Anchor**:
Where a local comment sits: the blob of the file it was written on, the range in it, and the anchored text. Written once and never updated; showing the comment on another version maps it from the anchor. When the anchored lines themselves changed, the comment is **outdated**: shown next to where they were, with the anchored text. (Not the `outdated` patchset status.)
_Avoid_: position, location (for the stored value)

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

**ChangeView**:
One ChangeInfo payload, parsed once in `core/gerrit/` — labels, revisions and parents, owner, votes, reviewers, freshness. The **Gerrit overlay**, **review chains** and `ger show` read it; nothing above `core/gerrit/` reaches into the raw dict.
_Avoid_: detail, payload (for the parsed value), change dict

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
