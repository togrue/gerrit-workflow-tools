# `ger reply` *(planned)*

| | |
|--|--|
| **Status** | **Partial**: `ger reply CHAIN` (write, update, discard), `ger reply --publish` and publishing with `ger push` are implemented; the guided reply is planned (see [Delivery](#delivery)) |
| **Module** | `cli_reply.py`; draft round trips on `CommentApi` in `core/gerrit/comments.py`; chain merging in `core/comment_chains.py` |
| **Requires** | `gerrit.webUrl`, credentials |

Answer an inline comment chain as a **draft**, and later publish your drafts. This closes the author's review loop from the terminal: `ger show` reads the chains, you (or an agent) fix the code, `ger reply` answers each chain, and the answers reach reviewers when you push.

The main point of the design: **writing and publishing are separate steps.** Gerrit keeps a draft private to its author until it is published. An agent can write every reply, a human reads them in `ger show` or the web UI, and only an explicit publish sends anything to reviewers.

---

## The loop it enables

Three commands, the same for a human and an agent:

```text
ger edit     go to the next commit that needs work, and see its comments
ger reply    answer the comments on the commit you're on
ger push     publish the fixes and the answers together
```

A human:

```text
ger edit                    -> stops at a commit and lists its open chains
  ...fix the code, git commit --amend...
ger reply                   -> per chain: [d]one, [r]eply in $EDITOR, [s]kip
ger edit -n                 -> next commit
ger push                    -> "Push and publish 3 drafts? [Y/n]"
```

An agent, with `GER_NO_PUBLISH=1` in its environment:

```text
ger edit --json             -> the stop, with its open chains, ids and code context
  ...fix the code, run the tests, git commit --amend...
ger reply <id> --done       -> draft "Done", resolving the chain
ger reply <id> -F -         -> draft a longer answer from stdin; the chain stays open
ger edit -n --json          -> next commit, until exit 10 (nothing left)
```

The agent stops there and reports. The human reads the drafts in `ger show --stack` and runs `ger push`.

Publishing with the push matters. A "Done" that arrives before the patchset that does it sends the reviewer to old code. Gerrit's `publish-comments` push option publishes the drafts in the same update, so there is one notification and the fix is already there when the reviewer looks.

---

## Usage

```
ger reply                                      (TTY) step through the open chains of HEAD's change
ger reply [--change CHANGEISH] CHAIN (-m MSG ... | -F FILE | --done) [--resolve | --unresolve] [--json]
ger reply [--change CHANGEISH] CHAIN --discard [--json]
ger reply --publish [REV ... | --stack] [-m MSG] [--dry-run] [-y] [--json]
```

### Guided reply

`ger reply` with no `CHAIN` on a TTY walks the open chains of HEAD's change, the commit an edit stop leaves you on. For each one it prints the chain and asks `[d]one  [r]eply  [s]kip  [q]uit`. *Reply* opens your editor (the same editor lookup git uses) with the chain quoted as `#` lines, which are dropped from the message, as in `git commit`. A chain that already has your draft shows it and offers `[k]eep` in place of *skip*. Without a TTY and without `CHAIN`, it exits `2`.

### Naming a chain

`CHAIN` is one of:

| Form | Example | Change comes from |
|------|---------|-------------------|
| Comment id | `9f8e7d6c` | `--change`, else a lookup across the local stack |
| Comment URL | `https://host/c/proj/+/123/comment/a1b2c3d4_5e6f7a8b/` | The URL. Giving `--change` as well exits `2` |

Any comment in the chain names the whole chain, whether it is the root or a reply. That way an id copied from any line of `ger show --json` works.

**Without `--change`**, the id is looked up in the comment chains of every change in the local stack (`upstream_tip..HEAD`). These are the same comment fetches `ger show --stack` makes, served from the follow-up cache. Writes go to the stack's triplet, never to a bare Change-Id that could name several branches. On a detached HEAD with no branch to take a stack from, only HEAD's change is searched. One match is used. No match exits `3`, and the message names the changes it searched. More than one match exits `4` and lists the candidates. Comment ids are UUIDs, so this should only happen on corrupt data.

**With `--change`**, the full changeish grammar applies ([change-and-commit-identifiers.md](../change-and-commit-identifiers.md)), and only that change is searched. This is how you reply on a change that is not in your local stack, for example one you are reviewing.

### Writing a reply

| Option | Effect |
|--------|--------|
| `-m MSG` | Message text. Repeat it for more paragraphs, as with `git commit -m` |
| `-F FILE` | Read the message from a file. `-F -` reads stdin, which is what agents should use for multi-line text |
| `--done` | Shorthand for `-m Done --resolve`, the web UI's quick reply |
| `--resolve` / `--unresolve` | Set the chain's state once the draft is published. Without either, a new draft inherits the state of the comment it answers (Gerrit's default), so an open chain stays open, and an updated draft keeps its own state |

An empty message exits `2`. Combining `--done` with `-m`, `-F` or `--unresolve` also exits `2`.

**Where the draft goes:**

- in reply to the chain's newest comment (`in_reply_to`), so the draft decides the chain's state. Gerrit stores a chain's resolved state on its chronologically last comment.
- on the patchset of that newest comment, with the root's path, side, line and range. This is what the web UI does, and it keeps the reply next to the code that was discussed.

**At most one draft per chain.** If you already have a draft anywhere in the chain, `ger reply` updates that draft instead of adding a second one. That covers drafts you started in the web UI too. The output shows the text it replaced, so nothing is lost silently, and re-running the same command is harmless. That matters for agents, which retry.

**`--discard`** deletes your draft in the chain and prints its text. A chain with no draft exits `3`.

### Publishing

`ger reply --publish` publishes **all your drafts on each target change**, on every patchset. This matches the web UI's *Send*: it includes drafts you wrote in the browser, not only those `ger reply` made. The targets are the `REV` changeishes, or the whole local stack with no `REV`. Changes without drafts are skipped and listed as such.

| Option | Effect |
|--------|--------|
| `-m MSG` | Top-level change message posted with the drafts. It is posted only on changes that have drafts |
| `--dry-run` | List what would be published and exit `0`. Allowed under the publish guard |
| `-y`, `--yes` | Skip confirmation. Required when stdin is not a TTY, the same rule as `ger push`, and with `--json`, which never prompts |

Publishing first prints every draft that is about to go out, grouped by change. Then it asks `Publish N draft(s) on M change(s)? [y/N]` (default **no**: this one sends mail). Declining, or a run without `-y` that cannot prompt, publishes nothing and exits `1`. Each published change prints `published N draft(s) on <subject>`.

Each change is one REST call. A failure on one change is reported, the others are still tried, and the exit is `5` if any failed. A partial publish is listed per change, never summarised away. A publish drops the change's cached rows, so the next `ger show` sees the published chains at once.

```text
ger reply --publish --dry-run
Add retry helper  https://host/c/proj/+/123
  src/retry.py:42 -> resolves: "Done"
  src/retry.py:80: "The table is 8 entries..."
```

With `--json`: `{"changes": [{"change": <triplet>, "url": ..., "drafts": N, "status": ...}]}`, where `status` is `published`, `dry-run`, `no-drafts` or `failed` (then with `error`).

### Publishing with `ger push`

`ger push` looks for your drafts on the changes it is about to push: one `has:draft` query for those change numbers. When there are any:

- **On a TTY**, the status line gains `Drafts keep N` and the prompt becomes `Push and publish N draft(s)? [Y/n/p/r]`. Yes adds Gerrit's `publish-comments` push option ([Gerrit: publish-comments](https://gerrit-review.googlesource.com/Documentation/user-upload.html#publish-comments)), which publishes your drafts on every change the push updates. Answering `p` pushes without publishing.
- **With `-y`**, drafts are published only when `--publish-comments` is also given. Scripts and agents never publish by accident.
- Drafts on a change the push does **not** update (behind the ready boundary, or with nothing new) stay drafts. The preview lists them by change.

**Account preference.** Gerrit has an account preference that publishes drafts on every push (publish_comments_on_push in the account preferences). With it on, every push would publish. `ger push` therefore passes `no-publish-comments` whenever there are drafts and it is not publishing, so the answer above is the only thing that decides. Without drafts it sends neither option.

### The publish guard

With `GER_NO_PUBLISH=1` in the environment, `ger reply --publish` and `ger push --publish-comments` exit `2` without contacting Gerrit, and `ger push` passes `no-publish-comments` without asking. Agent settings set it so that "the human publishes" holds even when an agent is told otherwise. It guards against mistakes; it is not a security boundary, since a process can change its own environment.

---

## Changes to `ger show`

Drafts are shown inside their chain, so the human review step reads them where they belong.

- **Human:** the draft is the last entry in the chain's box, headed `draft (you)` and coloured **blue**, the draft colour. If the draft changes the state, the header also says `-> resolves` or `-> reopens`.
- **Markdown / `--ai`:** the same entry, headed `**Draft reply (unpublished)**`.
- **JSON:** each chain gains its `root_id`. Each comment gains its patchset and a draft flag. Each chain says what its state becomes once the drafts are published:

```json
{
  "root_id": "a1b2c3d4_5e6f7a8b",
  "path": "src/app.py",
  "line": 42,
  "resolved": false,
  "resolved_after_publish": true,
  "comments": [
    {"comment_id": "a1b2c3d4_5e6f7a8b", "patch_set": 3, "draft": false, "author": "Rita Reviewer", "message": "..."},
    {"comment_id": "f0e1d2c3_b4a59687", "patch_set": 3, "draft": true, "message": "Done"}
  ]
}
```

`resolved` stays Gerrit's published truth. `--comments unresolved` keeps filtering on it, so an answered but unpublished chain is still listed. Its draft is what shows that it was answered.

---

## Changes to attention (`ger log`, the attention walk)

When every unresolved chain on a commit has a draft that resolves it, the comments token (`com`) turns **blue** in `ger log` and `ger show`, and that commit's reason `unresolved-comments` becomes `unpublished-drafts`:

- `ger log` still exits `1`. Something still waits on you: publishing.
- **Edit attention** does not count `unpublished-drafts`. The code is fixed and nothing is left to edit, so `ger edit --first-attention-commit` and `ger show --first-attention` move past the commit. Without this, the walk would keep landing on chains the author has already answered.
- A chain that still has no resolving draft keeps `unresolved-comments`, so a commit with both reasons stays in the walk.

This follows the same rule as a locally rewritten commit (`v?`, issue #39): ger lets your local, unpublished state shape *your* attention, and never what it reports as Gerrit's state.

---

## Cost and the trust window

Drafts live in Gerrit's All-Users repository, not in the change's NoteDb meta ref. So `meta_rev_id` does not move when a draft changes, and the follow-up cache key ([ADR-0007](../../adr/0007-follow-up-cache-key-is-note-db-meta.md)) cannot vouch for a cached draft. **Drafts are therefore never cached.** They are fetched live, as cheaply as possible:

- **`ger show`:** one drafts GET per shown change.
- **`ger log`:** one `has:draft` query restricted to the stack's change numbers. Then a drafts GET only for the changes it returns. A stack with no drafts costs one query.
- **Offline / cache-only:** drafts are not fetched. `ger log` and `ger show` print `drafts not checked (offline)` and keep `unresolved-comments`. They do not guess.

Writing or discarding a draft needs no invalidation: drafts are never cached, and the published comments did not change. A publish does change them, so it invalidates the change's cached entries with `invalidate_changes`, as `set_reviewers` does today. The meta ref moves anyway, but invalidating makes the next `ger show` refetch straight away, inside the 10-second trust window too.

---

## REST round trips

`GerritRest` gains one method per call below. Batching, retries and invalidation stay on `GerritService` (`CommentApi`), as for every other call.

```text
GET    /changes/{id}/drafts                                    list my drafts, all patchsets
PUT    /changes/{id}/revisions/{ps}/drafts                     create (CommentInput, in_reply_to set)
PUT    /changes/{id}/revisions/{ps}/drafts/{draft-id}          update
DELETE /changes/{id}/revisions/{ps}/drafts/{draft-id}          discard
POST   /changes/{id}/revisions/current/review                  publish:
         {"drafts": "PUBLISH_ALL_REVISIONS", "message": optional}
GET    /changes/?q=has:draft+(change:N+OR+...)                 which pushed changes have drafts
```

The comment GET today already returns the published `patch_set`. The model keeps it: `InlineComment` gains its patchset and a draft flag, and `build_comment_chains` takes the drafts file map next to the published one. That keeps chain assembly in its one owner, `core/comment_chains.py`, so there is no second threading pass for drafts.

---

## Output

**Human**, one line per action, then the comment URL:

```text
draft reply on src/app.py:42 (change 123, patchset 3) -> resolves
  https://host/c/proj/+/123/comment/a1b2c3d4_5e6f7a8b/
updated draft on src/app.py:42 (replaced: "Will do, after the refactor")
```

**JSON** (`--json`): one object per action.

```json
{
  "action": "created",
  "change": "proj~main~I1a2b...",
  "root_id": "a1b2c3d4_5e6f7a8b",
  "draft_id": "f0e1d2c3_b4a59687",
  "patch_set": 3,
  "unresolved": false,
  "replaced_message": null,
  "url": "https://host/c/proj/+/123/comment/a1b2c3d4_5e6f7a8b/"
}
```

`action` is `created`, `updated`, `discarded` or `published`. Publish emits one object per target change, each with `drafts` (a count) and `status` (`published`, `no-drafts` or `failed`, with `error` set when it failed).

---

## Exit codes

Shared table: [exit-codes.md](../exit-codes.md). No new codes.

| Code | When |
|------|------|
| `0` | Draft written, discarded or published; `--dry-run` |
| `1` | Publish declined, or non-TTY without `-y` |
| `2` | Empty message, conflicting flags, `CHAIN` with `--publish`, a comment URL together with `--change`, no `CHAIN` without a TTY, publishing while `GER_NO_PUBLISH` is set |
| `3` | Chain or change not found; `--discard` with no draft |
| `4` | Comment id matched more than one change; ambiguous changeish |
| `5` | Gerrit rejected a write, including a partial publish |
| `6` | Missing `gerrit.webUrl` or credentials |

---

## Out of scope

- **Comments on commits that are not on Gerrit.** A Gerrit draft sits on a patchset, so it cannot exist before the first push. Push the commit as WIP instead: reviewers are not notified about WIP changes, and once the change exists, drafts and `ger reply` work as usual. A local comment store would need its own line anchoring, its own drift handling, and a way to turn local notes into Gerrit drafts at push time: a second comment system beside Gerrit's.
- **Starting a new chain** (commenting on a line nobody has commented on). That is reviewing, not answering. It needs a file/line grammar and diff awareness this spec does not add.
- **Votes.** No `--label`. The author loop does not need them, and a vote is not a draft: it publishes at once.
- **Batch input** (many replies from one JSON document). One call per chain is fine for agents: each is one PUT, and the chain lookup is served from the cache.
- **Fix suggestions** (Gerrit's suggested-edit payload on a comment).
- **Bot tags.** No `autogenerated:` tag. These replies are the author's own words, written with help, and they should not be hidden behind the web UI's bot filter.

## Considered options

| Rejected | Chosen instead |
|----------|----------------|
| Post comments directly with a review call (no drafts) | Drafts first. Writing is safe to hand to an agent, and publishing stays a human step |
| Publish with an explicit list of draft ids, so only ger-made drafts go out | All of your drafts on the change, like the web UI's *Send*. A list would leave browser drafts behind with nothing pointing at them |
| A separate `ger publish` command | `ger reply --publish` and `ger push --publish-comments`. Publishing belongs to the push in the normal loop, and a standalone verb would mostly be used by mistake before pushing |
| Use Gerrit's official MCP server for writes | It does not know the local stack, so it cannot find a chain by comment id across the stack or adjust edit attention. The draft calls are small, and keeping them in `GerritService` keeps the trust window in one place |
| A new exit code for "already has a draft" | Update the draft and show what it replaced. The rerun case is the common one, and failing it would make agents wrap every call in error handling |

## Delivery

Built in this order. Each step works on its own.

| # | Step | Status |
|---|------|--------|
| 1 | Comments keep their patchset and a draft flag; `ger show --json` adds `root_id` and `patch_set` | Done |
| 2 | Drafts are read live and merged into their chains; `ger show` displays them | Done |
| 3 | `ger reply CHAIN` writes, updates and discards drafts | Done |
| 4 | `ger reply --publish` and the publish guard | Done |
| 5 | `ger push` publishes drafts with the push | Done |
| 6 | Blue `com` token and `unpublished-drafts` attention | |
| 7 | `ger edit` lists the open chains on every stop, `--json` for agents | |
| 8 | Guided `ger reply` | |
| 9 | Agent skill | |

## Docs to update when this ships

- [SPEC.md](../../SPEC.md): move `ger reply` from *Planned commands* into the registry.
- [CONTEXT.md](../../../CONTEXT.md): define **comment chain** (the term the code already uses) and **draft reply**.
- [show.md](show.md), [log.md](log.md), [push.md](push.md): the sections above.
- [exit-codes.md](../exit-codes.md): nothing; no new codes.
