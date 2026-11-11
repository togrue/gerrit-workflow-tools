# `ger push`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `cli_push.py` prompts and prints; `core/push.py` prepares, revises, and executes |
| **Requires** | Gerrit mode: target resolution, Change-Ids; attribute preview / REST strategies need `gerrit.webUrl` + credentials |

Push the **ready prefix** of the local stack to Gerrit (`refs/for/<target>`), or run plain **`git push`** in **vanilla** mode when upstream is not on `gerrit.remote`.

---

## Usage

```
ger push [options] [REV]
```

`REV` — push only through this commit, a changeish (must be before the ready boundary unless `--all`).

### Change resolution

When `REV` is given, it is resolved as a **changeish** the way `ger edit`, `ger reword` and `ger fix` resolve theirs (`resolve_stack_changeish` in **`core/gerrit/change_resolution.py`**): a git rev, a Change-Id, `change:N` or a triplet names the commit of that change in the local stack, with the same `cannot resolve '…'` messages and notes. Post-push reviewer REST and attribute preview resolve stack commits to Gerrit **triplets** (not bare Change-Ids). Push destination is inherently target-branch-scoped (`refs/for/<target>`). Shared grammar and target-branch narrowing for duplicate Change-Ids are defined in [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Print preview only. Writes nothing, so a missing upstream is an error instead of a prompt |
| `-y`, `--yes` | Skip confirmation (required when stdin is not a TTY) |
| `--publish-comments` | Publish your drafts on the updated changes with the push. Needed with `-y`; on a TTY the prompt asks. Exits `2` while `GER_NO_PUBLISH` is set |
| `-i` | TTY: prompt for reviewers (cannot combine with `-y`) |
| `--all` | Push full stack (ignore stop pattern; Change-Id errors still tighten the tip) |
| `--reviewers ACCOUNTS` | Comma-separated reviewers (repeatable; merged, deduped) |
| `--reviewer-strategy` | `push` \| `lazy` \| `overwrite` (see below) |
| `--topic NAME` | Magic ref `%topic=…`. A comma is refused: it separates push options |
| `--wip` | Magic ref `%wip` |
| `--ready` | Magic ref `%ready`: mark the changes ready for review again, the way back from `--wip`. Mutually exclusive with `--wip`. The interactive options line cannot say it, so a typed line keeps it unless the line asks for `wip`; the status line shows `WIP: no (--ready)` |
| `--private` | Magic ref `%private` |
| `--no-private` | Magic ref `%remove-private`: make the changes public again, the way back from `--private`. Mutually exclusive with `--private`. As with `--ready`, a typed options line keeps it unless the line asks for `private`; the status line shows `Private: no (--no-private)` |
| `--branch NAME` | Push a different local branch than current |
| `--no-rebase-check` | Skip fetch/linear-on-remote check (`gerrit.push.remotePolicy`) |
| `--color`, `--debug-log` | Standard CLI helpers. `-v` is accepted and does nothing, so it is not shown in help or completion |
| `--follow-merges` | Include merge commits in stack range |

---

## Push modes

| Mode | When | Behavior |
|------|------|----------|
| **Gerrit** | `branch.*.gerritTarget` set, **or** upstream remote == `gerrit.remote` | Ready range (stop pattern + Change-Id) → `git push <tip>:refs/for/<target>[%options]` |
| **Vanilla** | Upstream on another remote | Plain `git push`; Gerrit-specific flags ignored (warning printed) |

No upstream and no `gerritTarget` → error; set upstream (e.g. `git branch --set-upstream-to=<remote>/<branch>`) or `branch.<name>.gerritTarget`. On a TTY, several commands prompt interactively via `ensure_branch_upstream_interactive`.

---

## Reviewer strategies

| Strategy | Behavior |
|----------|----------|
| `push` (default) | Reviewers on refspec `%r=…` |
| `lazy` | REST: add reviewers only where none assigned |
| `overwrite` | REST: replace reviewers per change; CC accounts stay |

`lazy` / `overwrite` may run REST assignment after push when git reports no new changes. Topic/WIP/ready/private/remove-private use magic ref options for all strategies.

### Changes Gerrit already has at that commit

Gerrit applies magic-ref options only to the changes a push gives a new patch set. A commit that already is its change's current patch set gets them over REST instead (`core/push_unchanged.py`), after the push and also when Gerrit rejects it as `no new changes` (printed as `nothing to push: Gerrit already has these commits`). When Gerrit already has every commit, no `git push` runs at all, since Gerrit would refuse it; with nothing to set over REST either, `ger push` only prints that line and exits `0`: no preview, no question, and no `-y` needed off a terminal (`changes_nothing` in `core/push.py`).

| Option | REST call, only where the change differs |
|--------|------------------------------------------|
| `--wip` / `--ready` | `POST /changes/{id}/wip` / `POST /changes/{id}/ready` |
| `--private` / `--no-private` | `POST /changes/{id}/private` / `DELETE /changes/{id}/private` |
| `--topic` | `PUT /changes/{id}/topic` |
| `--reviewers` (strategy `push`) | the reviewer batch, for the named reviewers not yet on the change; `lazy` / `overwrite` reach every change anyway |

The preview is built from the plan (`sent_commits` and `state_updates` in `core/push.py`), and `--dry-run` from the same plan. Each commit is listed once, and a heading only appears over a list that has something in it:

- `About to push commits:` lists only the commits the push sends, the ones Gerrit gets a new patchset for.
- `Gerrit already has these at that commit; set over REST instead:` lists the commits that only get options set, as `<sha> # <subject>  ready,topic=x`.
- Commits Gerrit already has with nothing to set are one dim line under the list: `1 commit Gerrit already has is not sent again`.
- When the push sends nothing and only sets options, there is no `About to push` block: the heading is `No new commits to push; set on Gerrit:`, the question is `Set these on Gerrit? [Y/n/r]:` (the options line says `Enter sets them on Gerrit`), and no `nothing to push` line is printed beside the sets.

`--dry-run` prints the `git push` line only when the real run would push, else `[dry-run] no git push: Gerrit already has these commits` on stderr. After the push each update prints `set <sha> # <subject>  …`. A failed update fails the command, so it is an error naming the commit and ending in Gerrit's own reason; the same failure on several commits is one line naming them all (`error: could not set r=nosuchuser on 2 commits (abc1234, def5678): Gerrit HTTP 400: Account 'nosuchuser' not found; nosuchuser does not identify a registered user or group`). The exit code is `5` (`GERRIT`), as for reviewers. Without Gerrit's view of the changes (no credentials) nothing can be set, and a `no new changes` push says so. The cached state of every updated change is dropped. A push without these options makes no extra call. Hashtags are not a `ger push` option.

Interactive push line (`push_input_prompt`) can set strategy keywords; see module `push_input_line.py`.

### Push-options history

The interactive prompt (the confirmation line, `-i`, and confirm-loop `r`) persists recent lines under
`$XDG_CACHE_HOME/ger/<host>/push_options_history/<project-safe>.txt`, keyed by
`gerrit.webUrl` host and `gerrit.project` (see
[ADR-0005](../../adr/0005-push-options-history-is-host-and-project-scoped.md)).

- Cap: 20 entries per (host, project), newest first, exact-line dedupe.
- Stored: reviewers, topic, `wip` / `private`. **Not** stored: strategy.
- History is only a suggestion: the line never starts as a history entry. Up/Down
  recall entries into the line (no completion menu open; with one, the arrows walk the
  menu and Down past its last item returns to the typed text). Down past the newest
  entry returns to the line you had. Editing a recalled entry makes it the current line:
  Up then restarts at the newest entry and Down returns to the edit.
- If `--reviewer-strategy` is set, that strategy is merged into the visible
  line and Up/Down recall for the session; accept still strips strategy
  before save.
- A line is saved after the push succeeds, never at the prompt, so a cancelled push or a rejected line leaves no entry.
- Missing host/project identity: no read/write of history.
- Initial line: empty for the confirmation line. `-i` and confirm-loop `r` start with
  `r=…` from `--reviewers`, else `.ger/reviewers` / host registry, else
  `branch.*.gerritReviewers`.

---

## Pre-push checks (Gerrit mode)

`prepare` runs before `-i`. A push refusal (empty ready range, or a rebase check that refuses) is printed without asking for reviewers. `execute` is the only call that runs `git push`.

0. No rebase underway: the branch ref moves only when a rebase finishes, so mid-rebase `ger push` would send the stack as it was before the rebase. It refuses with exit `2` (`USAGE`) — `--dry-run` too, since the push it previews would not run — and says to finish (`git rebase --continue`) or `git rebase --abort` first.
1. Target ref resolves locally (fetch if needed).
2. Ready boundary: stop pattern / ready strategy (unless `--all`), tightened by the first missing/malformed/duplicate Change-Id (always, including with `--all`).
3. Optional: `gerrit.push.remotePolicy` linearity check (unless `--no-rebase-check`).
4. With credentials, what Gerrit already holds: abandoned changes, commits that already are the current patchset, and your drafts on the pushed changes (one `has:draft` query, then a drafts GET only where it found some). Best effort: a failed lookup finds nothing.

---

## Confirmation output

Prints: pushable commits, ready-boundary notice when a blocking commit exists (`Stopped at commit … because …`), confirm status line, and `git push …`. Prompt, on a terminal with no drafts to publish: the push-options input line (`Push options (Enter pushes, Up: history, Ctrl-C cancels):`). Enter on the empty line pushes with the options the status line shows (no reviewers unless `--reviewers`/config gave some); a typed line (`r=alice,bob topic=x wip …`, with completion and history) sets them first; Ctrl-C / Ctrl-D cancels (exit `0`). Without a terminal on stdout, or with drafts, the question is `Do you want to push these commits? [Y/n/r]:`. A push that sends no commit and only sets options over REST asks `Set these on Gerrit? [Y/n/r]:` instead (see above). No prompt with `--dry-run` / `-y` / non-TTY without `-y`, nor when the push would change nothing on Gerrit (see above).

**Drafts.** When you have drafts on changes the push updates, the status line shows `Drafts keep N` and the prompt becomes `Push and publish N draft(s)? [Y/n/p/r]`. Yes adds Gerrit's `publish-comments` push option; `p` pushes and keeps them. Whenever the push does not publish, it sends `no-publish-comments`, so an account preference that publishes on push cannot publish them behind your back. Drafts on commits Gerrit already has (no new patchset) cannot be published by a push; the preview names them (`drafts on … stay drafts`). Details: [reply.md](reply.md#publishing-with-ger-push).

After success, optional `lastPush/<branch>` marker (`gerrit.lastPushedBranch`, default on).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, dry-run OK, or user cancelled at prompt |
| `1` | Nothing to push (empty ready prefix), or you declined the upstream prompt |
| `2` | Usage: a rebase in progress, invalid option value (`--topic a,b`), non-interactive without `-y`, `-i` without a terminal, `REV` at or after the ready boundary (the message names the boundary and, when the stop pattern or a ready strategy set it, `--all`) |
| `3` | `REV` names no commit, no change, or a change with no commit in the local stack |
| `4` | `REV` names a Change-Id that more than one stack commit carries |
| `5` | Push succeeded (or had nothing to send), but Gerrit refused what is set over REST: `lazy` / `overwrite` reviewers, or an option on a change Gerrit already had (printed as an error with Gerrit's reason) |
| `6` | The branch has no upstream: the same message and hint as `ger log` |
| other | `git push` exit code, passed through as [exit-codes.md](../exit-codes.md#what-is-not-remapped) records. When Gerrit rejects the push (`! [remote rejected] … (reason)`, exit `1` from git), git's output is printed untouched and ger adds one line after it with Gerrit's reason, its lines joined by `; `: `error: Gerrit rejected the push: Account 'nosuch' not found; nosuch does not identify a registered user or group` |

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.remote` | Gerrit remote name (default `origin`) |
| `gerrit.pushShowAttributes` | Reviewer/wip/private preview |
| `gerrit.lastPushedBranch` | Local `lastPush/<branch>` after push |
| `gerrit.push.remotePolicy` | Remote tip linearity |
| `branch.*.gerritTarget`, `gerritReviewers` | Destination & default reviewers |
| `gerrit.stopPattern` | Ready boundary (single regex; see [Configuration.md](../../Configuration.md)) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [Configuration.md](../../Configuration.md) — `branch.*.gerritTarget`, `gerritReviewers`
- [`ger change-id`](sha-change-id.md#ger-change-id)
