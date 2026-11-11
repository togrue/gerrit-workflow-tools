# `ger comment`

| | |
|--|--|
| **Status** | **Partial**: Gerrit drafts, notes, local drafts, `--discard` and re-anchoring ship. [Later slices](#later-slices) (uploading, orphans, `ger log`) are planned |
| **Module** | `cli_comment.py`; the LOCATION grammar in `core/comment_anchor.py`; which patchset in `core/comment_target.py`; writing in `core/comment_write.py`; Gerrit drafts on `CommentApi` in `core/gerrit/comments.py`; the local comment store in `core/local_comments.py`; chain assembly in `core/comment_chains.py`; re-anchoring in `core/anchor_mapping.py` |
| **Requires** | A git repository. `gerrit.webUrl` and credentials, except for `--note`, `--local` and `--discard` of a local comment, which work without Gerrit configured |

Write a new comment on a line, a range or a file of a change. `ger reply` answers a chain that already exists; `ger comment` starts one.

A comment has one of four **comment states**:

| State | Lives in | Seen by | Reaches Gerrit |
|-------|----------|---------|----------------|
| **note** | the local comment store | only you | never |
| **local draft** | the local comment store | only you | when uploaded (planned, see [Later slices](#later-slices)) |
| **draft** | Gerrit (All-Users) | only you | already there; private until published |
| **published** | Gerrit | everyone | — |

`ger comment` writes the first three. Publishing is `ger reply --publish`, `ger push` or `ger review --vote`, unchanged.

---

## Usage

```
ger comment LOCATION (-m MSG ... | -F FILE) [--change CHANGEISH] [--resolved] [--note | --local] [--json]
ger comment --discard ID [--change CHANGEISH] [--json]
```

### Which change

`--change` takes the full changeish grammar ([change-and-commit-identifiers.md](../change-and-commit-identifiers.md)). Without it the comment goes on the change being reviewed in this worktree ([`ger review`](review.md)), else on `HEAD`'s commit. A commit without a valid Change-Id exits `9`.

### LOCATION

| Form | Example | Gerrit fields |
|------|---------|---------------|
| `PATH` | app.py | file comment, no line |
| `PATH:LINE` | `app.py:42` | `line` |
| `PATH:START-END` | `app.py:40-42` | `range` over whole lines, `line` = END |
| `PATH:L.C-L.C` | `app.py:40.5-42.17` | `range` to the character, `line` = end line |

Lines and columns count from 1, as editors show them. Columns are inclusive: `40.5-42.17` starts at the fifth character of line 40 and ends after the seventeenth of line 42. ger converts to Gerrit's 0-based, end-exclusive comment range.

`PATH` is relative to the repository top level. A path that exists relative to the current directory is accepted too and rewritten to its top-level form, so a path copied from an editor whose terminal sits in a subdirectory works.

When the commit is available locally, the location is checked against it: a path that is not in the commit exits `3`, a line past the end of the file or a backwards range exits `2`. This is what makes the anchor precise. A change that exists only on Gerrit is not checked; Gerrit rejects a bad line itself.

### Message and state

| Option | Effect |
|--------|--------|
| `-m MSG` | Message text. Repeat for paragraphs, as with `git commit -m` |
| `-F FILE` | Read the message from a file; `-F -` reads stdin |
| `--resolved` | Start the chain resolved (an FYI). Default: unresolved, as in the web UI |
| `--note` | Write a **note**: local and private, never uploaded |
| `--local` | Write a **local draft** even though the commit is on Gerrit |

An empty message exits `2`. `--note` and `--local` exclude each other.

### Where it goes

Without `--note` or `--local`, the commit decides:

- **The commit is one of the change's patchsets on Gerrit** (its SHA is in the change's revisions): a Gerrit **draft** on that patchset. That may be an older patchset than the current one. The line numbers belong to that patchset, so that is where they are true.
- **Anything else**: a **local draft**. That covers a change Gerrit does not have yet, and a commit you rewrote locally (`ger log` shows `v?`): its lines may not exist in any patchset.

A change named by `--change` that has no local commit gets a Gerrit draft on its current patchset.

### Discarding

`--discard ID` deletes one of your comments by id: a note or local draft from the local store, else your Gerrit draft with that id on the target change. Its text is printed, so nothing is lost silently. An unknown id exits `3`.

---

## The local comment store

Notes and local drafts live in git, under one ref: **`refs/ger/comments`** ([ADR-0010](../../adr/0010-local-comments-live-in-a-git-ref.md)).

- **One store per repository.** Worktrees share every ref outside `refs/worktree/`, so a note written in the review worktree shows in your main checkout too.
- **History.** Each write is a commit on that ref. A bad write can be undone with `git log refs/ger/comments` and `git update-ref`.
- **Layout.** `changes/<triplet>.json` (the triplet URL-quoted into one path segment) holds the comments of one change. `anchors/<blob>` points at each file version a comment was written on, which keeps that blob safe from `git gc`.
- **Writes never touch your index or working tree.** They are built with plumbing in a temporary index file, so they work in any worktree, mid-rebase, or with a staged review.
- **Concurrent writers.** The ref moves with a compare-and-swap (`git update-ref <ref> <new> <old>`). A writer that lost the race re-reads the ref and applies its one change again on top, up to five times. Nothing is lost when two worktrees, or the CLI and a server, write at once.
- **Never pushed.** ger never pushes the ref. Copying it to another clone is up to you.

### What a local comment records

The change's **triplet**: its key, built from the stack context for a commit Gerrit does not know yet. The commit it was written on. The path, line and range. The **anchor**: the blob SHA of the file in that commit, and the anchored text. The message, its unresolved flag, and when it was written.

The anchor never changes after the comment is written ([ADR-0011](../../adr/0011-comment-anchors-are-immutable.md)). Showing the comment on a later version of the file maps it from the anchor, never from a previous mapping.

### Re-anchoring

When a local comment is shown on a commit whose file is not the anchored blob, ger maps it there. The mapping is computed on read and never written back, so worktrees holding different versions of a change each see the comment in the right place without fighting over the store.

- **One diff, straight from the anchor.** `git diff -U0 --inter-hunk-context=0` from the anchored blob to the shown one, so no user config merges nearby hunks. Never chained through an earlier mapping, so ten rebases map exactly as one.
- **Clean move.** A line outside every hunk shifts by the line-count change of the hunks above it. A range shifts at each end, so an insertion inside it widens it. The comment shows at its new line.
- **Outdated.** A hunk that changes or deletes any anchored line makes the comment **outdated**. It sits on the line just before that hunk in the shown version (line 1 when the hunk starts the file), and the anchored text is shown with it. It is never dropped.
- **Renames.** When the anchored path is not in the shown commit, `git diff -M` from the anchor's commit finds where it went. When that commit is gone, a file in the shown commit with the anchored blob is taken. A file that cannot be found leaves an outdated file comment on the old path. Both lookups cover the whole tree, whatever directory ger runs in.
- **File comments** follow a rename and are never outdated while the file exists.
- **No lines to map to.** A shown file that is binary or empty, or a diff git cannot make, leaves an outdated file comment.
- **Cached** by (anchored blob, shown blob), in memory and bounded. Both are content-addressed, so a cached diff is never stale. A diff git failed to make is not cached.

---

## Changes to `ger show`

Local comments join the commit's chains. They show for commits Gerrit does not have, too, which until now showed no chains at all.

- **Human:** each local comment is a chain of its own, headed `note (private)` or `local draft (not on Gerrit)`, after the Gerrit chains at the same location, at the line [re-anchoring](#re-anchoring) maps it to. A comment that moved to another line adds `(moved from L)` to the heading, `L` being where it was written (`old/path:L` after a rename). An outdated one adds `(outdated: lines changed since)` and shows the anchored text as context lines, numbered as they were.
- **Markdown / `--ai`:** the same, headed `**Note (private)**` or `**Local draft (not on Gerrit)**`, with the same heading suffix and context block.
- **JSON:** every comment gains `state`: `note`, `local-draft`, `draft` or `published`. `draft` stays true for Gerrit drafts only. A local chain's `root_id` is the local comment's id, and its `url` is `null`. Every chain gains `anchor_moved`, true for a local comment shown on another version of the file or at another path than it was written on, and `outdated`, true when its anchored lines changed. `path`, `line` and `start_line` are where the comment is shown; `anchored_path`, `anchored_start_line`, `anchored_line` and `anchored_text` are where it was written and the text it covered (`null` for Gerrit chains).

`--comments unresolved|all|resolved` filters local comments on their own flag.

Attention is unchanged: local comments are yours, and nothing in them waits on a reviewer.

---

## Output

**Human**, one line, then the comment URL when there is one:

```
draft on src/app.py:42 (patchset 3)
  https://host/c/proj/+/123/comment/9f8e7d6c_1a2b3c4d/
local draft on src/app.py:40-42 (commit 1a2b3c4, not on Gerrit) id 5d1f0c2e9b7a4e61
note on src/app.py (commit 1a2b3c4) id 0b6e2d7f8c9a4b13
```

**JSON** (`--json`):

```json
{
  "action": "created",
  "state": "local-draft",
  "id": "5d1f0c2e9b7a4e61",
  "change": "proj~main~I1234...",
  "commit": "1a2b3c4d...",
  "path": "src/app.py",
  "line": 42,
  "range": {"start_line": 40, "start_character": 0, "end_line": 42, "end_character": 18},
  "patch_set": null,
  "unresolved": true,
  "url": null
}
```

`--discard` prints `discarded <state> on <location>: "<first line>"`, or JSON with `"action": "discarded"` and the message.

---

## Exit codes

| Code | When |
|------|------|
| `0` | Written or discarded |
| `2` | Bad usage: no message, a malformed or backwards LOCATION, a line past the end of the file, `--note` with `--local` |
| `3` | The path is not in the commit; the changeish or discard id resolves to nothing |
| `5` | Gerrit rejected the draft |
| `7` | Writing the local store failed (including five lost races in a row) |
| `9` | The commit has no Change-Id |

---

## Later slices

Specified here so the store's shape is fixed now. Not implemented yet.

1. **Uploading local drafts.** `ger push` maps each local draft onto the commit it pushes and creates it as a Gerrit draft on the new patchset, recording the Gerrit draft id so a second push updates instead of duplicating. Whether it is then published follows the [`ger reply`](reply.md#publishing-with-ger-push) rules, `GER_NO_PUBLISH` included. `ger comment --upload` does the same without a push. A note is never uploaded; `ger comment --promote ID` turns it into a local draft.
2. **Orphans.** A triplet in the store that is neither in the local stack nor on Gerrit is **orphaned**, usually after a squash. `ger log` counts them. ger offers to move them to the stack commit whose files contain the anchored text; `ger fix` and squashes made by `ger rebase` move them directly. Only `ger comment --gc` deletes, and it prints what it removes.
3. **`ger log`** shows a count of local drafts per commit next to the comment token.
