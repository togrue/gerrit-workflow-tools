# `ger log`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_log.py` |
| **Requires** | `ger.webUrl`, credentials |

Compact overview of the local commit chain vs Gerrit (CI, votes, unresolved comment counts, patchset alignment). Answers: *what needs attention next?*

---

## Usage

```
ger log [options] [REV_RANGE]
```

`REV_RANGE` — optional (e.g. `origin/main..HEAD`). Commits the upstream already contains and Gerrit has no change for count as merged (`m`), not as `not-pushed`. Default: `branch@{upstream}..branch` when upstream exists; otherwise error with hint to set upstream (`git branch --set-upstream-to=…`). While a rebase is underway, the default is the **local stack** as the rebase has it so far (see [During a rebase](#during-a-rebase)); an explicit `REV_RANGE` is taken as given. On a TTY, `ger log` may prompt via `ensure_branch_upstream_interactive`; with `--json` or `--ai` it never prompts and gives the hint instead.

### Change resolution

`ger log` enriches each local commit against the Gerrit change on the **target branch** (stack context). Changeishes in `REV_RANGE` endpoints use the same shared grammar as other commands; resolution goes through **`core/gerrit/change_resolution.py`**. When a footer Change-Id matches multiple server changes, the resolver narrows to the target branch and reports the pick transparently; a duplicate on another branch is ignored for status (may surface as a note on stderr). Full rules: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md). With `--json`, output includes a top-level `stack` object (project, target branch, push branch) and per-commit `resolution_note` when narrowing applied ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)).

---

## Options

| Option | Description |
|--------|-------------|
| `--json` | Machine-readable JSON (one object per commit) |
| `--ai`, `--format {human,ai}` | `ai`: one plain line per commit for a model to read (see [Output (`--ai`)](#output---ai)) |
| `--color WHEN` | `always` \| `auto` \| `never`; `auto` honours `NO_COLOR` ([Configuration.md](../../Configuration.md#colour)) |
| `--hyperlinks WHEN` | `always` \| `auto` \| `never`. When on, text URLs become a clickable `Open in gerrit` (OSC 8) and that compact link is shown by default. JSON always includes the raw `gerrit_url`. |
| `--url`, `--show-url` | Gerrit web URL per line (forced on). Default: on when hyperlinks are on, otherwise `ger.logShowUrl`. |
| `--show-change-id` | Show full Change-Id on an indented continuation line (default: `ger.logShowChangeId`) |
| `-v`, `--verbose` | Add indented `CI:` pipeline lines and a `Reviewers:` line (`alice, bob (cc: carol)`; omitted when Gerrit lists nobody) below each commit; primary line unchanged. Repeat (`-vv`) to also show Change-Id. |
| `--debug-log` | Log git commands to stderr (repeat for more detail) |
| `--follow-merges` | Include merge commits in range (see shared helper in `cli_common`) |

---

## Output (text)

Default: one primary line per commit, optional `# …` detail lines, trailing **summary** line.

**User guide (columns, tokens, examples):** [Reading-ger-log.md](../../Reading-ger-log.md). With hyperlinks on, each line gets a clickable `Open in gerrit` instead of the raw address. `--url` forces the URL column even when hyperlinks are off.

Columns: patchset token (`p`/`n`/`o`/`-`), Verified, Code-Review, comment marker, attention hints, subject. Patchset tokens: [Reading-ger-log.md](../../Reading-ger-log.md#status-columns).

**Summary example:** `summary: ready 2/6 · CI 1 · comments 1 · on-gerrit 4`

Below the summary, dim **next lines** (`cli_log.next_steps`) name the ger command for a state the stack is in: the upstream moved on after a fetch (`origin/main has 1 new commit; rebase onto it with `ger rebase origin/main``; when a commit would stop that rebase, `; "Refactor parser init" would conflict in a.txt` or `; "Refactor parser init" would become empty: its change is already upstream`; and `; 1 commit is already upstream and dropped` for what the rebase leaves out: previewed without touching the worktree, as `GET /stack` serves it), WIP changes on Gerrit (`ger push --ready`), a merged commit (`ger rebase --onto-remote --drop-merged-equivalent`; once the upstream moved on, the one line above names the rebase instead, `ger rebase origin/main --drop-merged-equivalent`, its `argv`), a **diverged** or outdated change (`ger checkout change:<n> --update`), and what `chain blocked` means (a change below is not submittable yet). One line per state, none when there is nothing to do.

Subject highlighting uses `ger.stopPattern` / `ger.warningPattern` when color is on.

### During a rebase

While a rebase is stopped, the stack is what it has made so far, then what it has still to do:
the commits on `HEAD` above the upstream as they are now — a replayed or amended commit with its
new id, compared with Gerrit by its Change-Id like any other — then the commit it stopped at
while that is not applied, and the commits still to replay, as they were. A note on stderr says
on which branch, where and why it stopped, and how many commits are shown as they were:

```
note: a rebase is in progress on feature, stopped on a conflict at 1a2b3c4 "Add e.txt" in e.txt; 1 commit still to replay, shown as before.
```

The stop reads `to edit it`, `to edit it, and HEAD has moved on from it: the commits above it were
made at the stop` (a split), `on a conflict … in <paths>`, `with its conflicts resolved, ready for
`git rebase --continue`` (nothing unmerged any more) or `between commits`; just `stopped` when
that cannot be told. Where `HEAD` is, by git's own state files:

- At an edit stop, `HEAD` is the commit it stopped at, or an amend of it (on the same commit,
  with the same Change-Id, or the same subject without one): that commit is `current`. With
  commits made on top of it, it stays `current` and they are `done`. Reset away (`git reset
  HEAD~`, to split it), it is not shown at all; the commits made in its place are `done`, and no
  commit is `current`.
- A conflict resolved and committed by hand (`git commit` instead of `--continue`) is applied:
  the new commit is `current`, shown once, and the stop reads `to edit it`. It is found by its
  Change-Id, else its subject (git keeps its `# Conflicts:` lines below the footer). Committed
  with another subject and no Change-Id, it is no telling which commit it is: `HEAD` is not where
  git left it, so the stop reads just `stopped`, and the new commit is `done`.
- On a detached HEAD there is no upstream: the stack is the commits above the rebase's `onto`,
  with no Gerrit state (no push branch to find their changes on), and the note says `on a
  detached HEAD`.

The todo is shown one row per commit it replays: a `fixup` or `squash` line is its own row,
counted in `N commits still to replay`. With `--rebase-merges`, the todo's merge lines are not
shown and its commits are drawn on one line.

A rebase git runs through `am` (`git rebase --apply`, `rebase-apply`) keeps no todo: the stack
is then the branch as it was before, and the note says so; it stops only on a conflict, which
the note names as for any other. A `git am` underway is no rebase: `ger log` shows the branch as
it is, with no note.

---

## Output (`--ai`)

[Output formats](../../SPEC.md#output-formats): the same commits, states and next steps as the text view, in words, with no colour, hyperlinks or padding.

```text
stack: testproj, branch main, 6 commits, ger push sends 1-2
1. 63568e8 Refactor parser init — change 100, V+1, CR+2, submittable
2. 10aa08b Extract command routing — change 101, V-1, build failed (unit-tests)
3. 06da828 test! temporary experiment — change 102, V+1, CR+1, 2 unresolved comments, chain blocked
4. f638488 Cleanup after experiment — change 103, ahead of gerrit, chain blocked
5. 4808c06 Wip: cache — not-pushed
6. 9692134 No footer here — missing Change-Id
chain blocked: a change below is not submittable yet (votes, CI, comments); it clears when that one is
```

The first line names the project, the push branch, the commit count and which commits `ger push` sends (those below the **ready boundary**). Each commit line is numbered oldest first: short SHA, subject, then only the facts that have a value: `change N`, `merged`, the votes cast (`V+1`, `CR+2`: the text view's tokens), and the text view's trailing hints, with the failed Checks named after `build failed`. The `next:` lines (`cli_log.next_steps`) close it.

---

## Attention & exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, whether or not a commit requires attention |
| `1` | `REFUSED` — you declined the upstream prompt |
| `2` | Invalid usage |
| `3` | A revision in the range does not exist |
| `5` | Gerrit API error, or Gerrit could not be asked at all (see below) |

**Gerrit unreachable or credentials rejected:** the local stack is still printed, but every change's Gerrit state is shown as unknown (patchset `~`, hint `Gerrit state unknown`, JSON `patchset_status: "unknown"` and reason `gerrit-unknown`), never as `not-pushed`. One line on stderr says what happened (`gerrit error: Gerrit unreachable at host:port (connection refused)` or `gerrit error: Gerrit at host rejected the credentials (HTTP 401); …`), and the exit code is `5`. A batch that fails this way is not retried change by change. Cached state inside the [trust window](../../adr/0001-offline-is-a-trust-window-not-a-gerritrest.md) is still served as valid; once any change needs Gerrit, the whole overlay is unknown.

Attention rules: shared `determine_attention()` in `core/attention.py` (same family as `ger edit --first-attention-commit`). The trailing hints are a rendering of `attention_reasons`, so a project attention strategy changes both. `ci-failed` means a failed Checks run or `Verified <= -1`.

A commit with no Change-Id footer must include `missing-change-id` in `attention_reasons`, and one whose footer value is not a valid Change-Id `invalid-change-id`, each with a matching attention hint in text output (`missing Change-Id`, `invalid Change-Id`: the words `ger push` and `ger change-id` use for the same commit) (see [Reading-ger-log.md](../../Reading-ger-log.md#attention-hints-trailing--)). Prefer this over only `not-pushed` when the root cause is a bad/absent footer — Gerrit overlay cannot identify the change without one. A `fixup!`/`squash!` commit without a footer reports `unsquashed-fixup` instead (hint `unsquashed fixup commit`): the same test, `stack.is_unsquashed_fixup`, that makes `ger push` stop there.

---

## JSON fields

Top level: `stack`, `rebase` (`null` without a rebase underway; else as `GET /stack` serves it: `branch`, `onto`, `orig_head`, `stop`, `applied`, `current`, `unmerged`, `as_before`, `actions`, `refused` — [serve.md](serve.md#get-stack)), `upstream` (how far the upstream moved on, and where rebasing onto it would stop: `name`, `behind`, `argv`, `conflict`, `preview`, `reason`, `dropped`, as `GET /stack` serves it; `null` with an explicit `REV_RANGE`, without an upstream and during a rebase), `commits`. During a detached HEAD's rebase there is no working branch: `stack.target_branch` and `stack.push_branch` are `null`, and every commit's Gerrit state is unknown.

Per commit: `sha`, `summary`, `rebase` (where the rebase underway stands on it: `done` \| `current` \| `todo`, `null` without one or for a commit it does not replay), `pushed`, `patchset_status`, `verified`, `code_review`, `comments_unresolved`, `ci_failures`, `ci_links`, `ci_pipelines`, `gerrit_url`, `number` (the change number, `null` before the first push), `current_patch_set` (the number of Gerrit's current patchset, `null` before the first push), `last_pushed_votes` (`{verified, code_review}` of the pushed patchset while the commit is `newer` with different code, else `null`; Gerrit may not carry them to the next push. A commit that is only rebased keeps its votes in `verified` and `code_review`), `code_change` (only while `newer`, else `null`: `rebased` when its code is the pushed patchset's by `git patch-id`, which leaves out whitespace and the commit message, so Gerrit usually keeps its votes (whether it does is the project's label rules); `changed` when it is not; `unknown` when that cannot be told here: either side is a merge, a root or an empty commit, or Gerrit's patchset is not in this repository, which shows as diverged), `submittable`, `votes` (`[{label, value, by}]`: every non-zero vote on the current patchset, `by` the voter's name, else username; empty while the commit is `newer` with other code), `reviewers` (`[{name, cc}]`: every account Gerrit lists as reviewer or CC, `cc` true for a CC), `work_in_progress`, `private`, `change_id`, `attention_reasons`, `severity` (the most urgent of the reasons: `error` \| `warning` \| `pending` \| `ok`, from `REASON_SEVERITY` in `core/attention.py`; a reason a project strategy made up is a `warning`), etc. (`patchset_status`: `active` \| `newer` \| `outdated` \| `absent` \| `unknown` \| a merged variant). `attention_reasons` includes `missing-change-id` when the local footer is absent and `invalid-change-id` when it is malformed. `ci_links` is a list of `{label, url, source}` (`source`: `checks` \| `message`) from the project CI registry when present, otherwise from the built-in Jenkins message parser when Verified is −1; otherwise `[]`. `ci_pipelines` lists Checks-plugin rows as `{label, state, url?}` when fetched (verbose log / show).

---

## Configuration

| Key | Effect |
|-----|--------|
| `ger.logShowUrl` | Force Gerrit URLs in text output even when hyperlinks are off (same as `--url` / `--show-url`). Compact `Open in gerrit` links are already shown by default when OSC 8 hyperlinks are on. |
| `ger.logShowChangeId` | Default for `--show-change-id` |
| `ger.webUrl`, auth | Required for API |
| `ger.stopPattern`, `ger.warningPattern` | Subject highlighting |

Full list: [Configuration.md](../../Configuration.md).

---

## See also

- [Reading-ger-log.md](../../Reading-ger-log.md) — user guide for text output
- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger show`](show.md)
- [`ger edit`](edit.md) (`--first-attention-commit`)
- [architecture.md](../../architecture.md)
