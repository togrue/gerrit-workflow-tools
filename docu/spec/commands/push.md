# `ger push`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `cli_push.py` prompts and prints; `core/push.py` prepares, revises, and executes |
| **Requires** | Gerrit mode: target resolution, Change-Ids; attribute preview / REST strategies need `ger.webUrl` + credentials |

Push the commits of the local stack below the **ready boundary** to Gerrit (`refs/for/<target>`), or run plain **`git push`** in **vanilla** mode when upstream is not on `ger.remote`.

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
| `--force` | Push even over a **diverged** change (one whose current patchset was uploaded from elsewhere, a web edit or another checkout, and is not in your history), or on top of an **outdated** one. Without it such a push is refused (check 5) |
| `--publish-comments` | Publish your drafts on the updated changes with the push. Needed with `-y`; on a TTY the prompt asks. Exits `2` while `GER_NO_PUBLISH` is set |
| `-i` | TTY: prompt for reviewers (cannot combine with `-y`) |
| `--all` | Push full stack (ignore stop pattern; Change-Id errors still tighten the tip). What the stop pattern or ready strategy held back goes up as **work-in-progress** (as `--wip-from <boundary>`), unless `--wip` or `--ready` says otherwise for all |
| `--reviewers ACCOUNTS` | Comma-separated reviewers (repeatable; merged, deduped) |
| `--reviewer-strategy` | `push` \| `lazy` \| `overwrite` (see below) |
| `--topic NAME` | Magic ref `%topic=…`. A comma is refused: it separates push options. An empty name is refused too (exit `2`): a push cannot clear a topic, the web UI can |
| `--wip` | Magic ref `%wip` |
| `--wip-from REV` | The commits from `REV` up go `%wip`, those below it ready. Gerrit applies `%wip` to every change one push creates or updates, so this is **two** `git push` runs: the ready part first, then the tip with `%wip` (where the lower commits are already Gerrit's, unchanged). Other options go with both. The preview marks the WIP commits `[WIP]` and the status line says `WIP from <sha> (k of n)`. A `REV` that this push does not send exits `2`, and the message says where the push stops: above `REV`, or above the ready boundary (`… (84e9a26: subject matches stop pattern …); add --all to push past it`). Changes Gerrit already has at that commit are not touched by it. Excludes `--wip` / `--ready` |
| `--ready` | Magic ref `%ready`: mark the changes ready for review again, the way back from `--wip`. Mutually exclusive with `--wip`. The interactive options line cannot say it, so a typed line keeps it unless the line asks for `wip`; the status line shows `WIP: no (--ready)` |
| `--private` | Magic ref `%private` |
| `--no-private` | Magic ref `%remove-private`: make the changes public again, the way back from `--private`. Mutually exclusive with `--private`. As with `--ready`, a typed options line keeps it unless the line asks for `private`; the status line shows `Private: no (--no-private)` |
| `--branch NAME` | Work on local branch NAME: its local stack and upstream (default: the working branch) |
| `--no-rebase-check` | Skip fetch/linear-on-remote check (`ger.pushRemotePolicy`) |
| `--color`, `--debug-log` | Standard CLI helpers. `-v` is accepted and does nothing, so it is not shown in help or completion |
| `--follow-merges` | Include merge commits in stack range |

---

## Push modes

| Mode | When | Behavior |
|------|------|----------|
| **Gerrit** | `branch.*.gerTarget` set, **or** upstream remote == `ger.remote` | Ready range (stop pattern + Change-Id) → `git push <tip>:refs/for/<target>[%options]` |
| **Vanilla** | Upstream on another remote | Plain `git push`; Gerrit-specific flags ignored (warning printed) |

No upstream and no `gerTarget` → error; set upstream (e.g. `git branch --set-upstream-to=<remote>/<branch>`) or `branch.<name>.gerTarget`. On a TTY, every command that needs the upstream asks for it ([SPEC § A missing upstream](../../SPEC.md#a-missing-upstream)); `ger push` asks before its preview, and not under `--dry-run` or `-y`.

---

## Reviewer strategies

| Strategy | Behavior |
|----------|----------|
| `push` (default) | Reviewers on refspec `%r=…` |
| `lazy` | REST: add reviewers only where none assigned |
| `overwrite` | REST: replace reviewers per change; CC accounts stay. People are compared by Gerrit account id, so a wanted reviewer named by username, email, full name or id who is already on the change stays and is not asked for again; only missing ones are added and only unwanted ones removed (a change already matching gets no request). A name that is the id, username or email of someone already on a pushed change resolves without a request; any other name is looked up with `GET /accounts/{name}`. If any wanted name resolves to no single account (Gerrit answers 404 or 400: a group, or an unknown or ambiguous name), it is still added, and that run removes nobody: when Gerrit accepts the add (a group, whose members it adds), everyone already present stays, with a warning `kept <reviewers> on <n> changes: <names> names no single account (a group?), so overwrite cannot tell who to remove`, and the push exits `0`; when Gerrit rejects it (an unknown name), nothing changes on that change and the push warns `could not update reviewers` and exits non-zero. If a lookup fails otherwise (401, a 5xx, Gerrit unreachable), the wanted names are still added, nobody is removed on any change, and the push warns `removed no reviewers: could not look up …` and exits non-zero. A reviewer Gerrit does not let you remove (for a change owner: one with a negative vote) stays too, with a warning; the push still exits `0` |

`lazy` / `overwrite` may run REST assignment after push when git reports no new changes. Topic/WIP/ready/private/remove-private use magic ref options for all strategies.

### A new change in a reviewed chain

`%r=` names reviewers for every change a push sends, so a push that names none gives a change it
creates nobody, though the rest of its chain is under review. When a push creates a change
(`new change` in the preview) in a chain whose other changes have reviewers, and names no
reviewers itself (no `--reviewers`, no `branch.<name>.gerReviewers`, nothing the registry gives),
the new change gets the chain's **people**: the `REVIEWER` accounts of the pushed changes Gerrit
has, in order, without CCs, without you, and without bots, which are accounts Gerrit tags
`SERVICE_USER` and accounts whose only votes there are on the CI label (`ger.inboxVerifiedLabel`,
`Verified`): a CI votes Verified and never Code-Review (`chain_people` in
`core/push_reviewers.py`). They are added over REST right after the push, to the new changes only
(`POST /changes/{id}/reviewers` batch), and the push says so: `added pg-alice, pg-bob to change
2179, as on the rest of the chain`. A failure warns (`pushed, but could not add …`) and exits
non-zero, as other reviewer failures do. The preview names them in a line under the new change,
`new change: reviewers pg-alice, pg-bob, as on the rest of the chain`, and the status line's
`Reviewers` field ends `; new change: +pg-alice, +pg-bob`. The push options line starts with
`chain-reviewers`: Enter keeps them, deleting the word (or `-chain-reviewers`) drops them, and
typing it brings them back. `--no-chain-reviewers` and `ger.pushChainReviewers = false` start
without them; the preview then says `new change: no reviewers (chain-reviewers on the options line
adds pg-alice, pg-bob)` and the status line `; new change: none`, also when nobody but a bot reviews
the chain.

### Changes Gerrit already has at that commit

Gerrit applies magic-ref options only to the changes a push gives a new patch set. A commit that already is its change's current patch set gets them over REST instead (`core/push_unchanged.py`), after the push and also when Gerrit rejects it as `no new changes` (printed as `nothing to push: Gerrit already has these commits`). A commit Gerrit already has as an older patch set of its change (**outdated**: a web edit or a Gerrit rebase came after) is not sent either, and gets nothing over REST, reviewers included: Gerrit keeps its newer patch set. The preview and the `nothing to push` line name each one, dim: `` <sha> outdated: Gerrit keeps patchset 2 of change N (`ger checkout change:N --update`) ``, the next step `ger log` names. Options asked for there warn `push options not applied to <sha>…: Gerrit has newer patchsets of these changes`, not about credentials. Drafts on an outdated change stay drafts, as on an unchanged one. A commit Gerrit has as a patch set of an **abandoned** change is not sent and gets nothing over REST either; it is not listed under `About to push commits:`, and one line names it: `` <sha> not sent: change N is abandoned (restore it in the web UI, or `ger edit --drop <sha>`) ``. A commit that would be a *new* patch set of an abandoned change is sent, and Gerrit refuses the push; a warning says so before it runs. When Gerrit already has every commit, no `git push` runs at all, since Gerrit would refuse it; with nothing to set over REST either, `ger push` only prints that line and exits `0`: no preview, no question, and no `-y` needed off a terminal (`changes_nothing` in `core/push.py`).

| Option | REST call, only where the change differs |
|--------|------------------------------------------|
| `--wip` / `--ready` | `POST /changes/{id}/wip` / `POST /changes/{id}/ready` |
| `--private` / `--no-private` | `POST /changes/{id}/private` / `DELETE /changes/{id}/private` |
| `--topic` | `PUT /changes/{id}/topic` |
| `--reviewers` (strategy `push`) | the reviewer batch, for the named reviewers not yet on the change; `lazy` / `overwrite` reach every change anyway |

The preview is built from the plan (`sent_commits` and `state_updates` in `core/push.py`), and `--dry-run` from the same plan. Each commit is listed once, and a heading only appears over a list that has something in it:

- `About to push commits:` lists only the commits the push sends, the ones Gerrit gets a new patchset for. With Gerrit's view of the changes, each says what it becomes, from what the push loads anyway (no request of its own; `core/push_targets.py`): `new change`, or `change 2101, patchset 3 (code changed)`, where the parenthesis says `rebase only` or `code changed` when the repository has Gerrit's current patch set to compare with (`git patch-id`), and `owner <name>` when the change is not yours (the Gerrit user: `ger.<webUrl>.user`, else `ger.user`). A commit that no longer changes a file Gerrit's current patch set changes gets a yellow line below it, `c2171: no longer changes README.md (patchset 2 did)`: a change lost in a conflict resolution is seen before the reviewer sees it. Only that direction is said (a file a commit newly changes is the point of most fixes); it never refuses, and it is read from the same diff as the patch-id, so it costs nothing (`code_facts.files_dropped`).
- `Gerrit already has these at that commit; set over REST instead:` lists the commits that only get options set, as `<sha> # <subject>  ready,topic=x`.
- Commits Gerrit already has with nothing to set are one dim line under the list: `1 commit Gerrit already has is not sent again`.
- When the push sends nothing and only sets options, there is no `About to push` block: the heading is `No new commits to push; set on Gerrit:`, the question is `Set these on Gerrit? [Y/n/r]:` (the options line says `Enter sets them on Gerrit`), and no `nothing to push` line is printed beside the sets.

`--dry-run` prints the `git push` line only when the real run would push; when it would only set options over REST, `[dry-run] no git push: Gerrit already has these commits` on stderr. Its `[dry-run]` lines come after the preview and the status line, also when both streams go to one pipe. After the push each update prints `set <sha> # <subject>  …`. A failed update fails the command, so it is an error naming the commit and ending in Gerrit's own reason; the same failure on several commits is one line naming them all (`error: could not set r=nosuchuser on 2 commits (abc1234, def5678): Gerrit HTTP 400: Account 'nosuchuser' not found; nosuchuser does not identify a registered user or group`). A name Gerrit has no account for rarely gets that far: under the `push` strategy it is caught before anything is sent (see Confirmation output), so this is what is left when that check could not ask Gerrit. The exit code is `5` (`GERRIT`), as for reviewers. Without Gerrit's view of the changes (no credentials) nothing can be set, and a `no new changes` push says so. The cached state of every updated change is dropped. A push without these options makes no extra call. Hashtags are not a `ger push` option.

Interactive push line (`push_input_prompt`) can set strategy keywords; see module `push_input_line.py`.

### Push-options history

The interactive prompt (the confirmation line, `-i`, and confirm-loop `r`) persists recent lines under
`$XDG_CACHE_HOME/ger/<host>/push_options_history/<project-safe>.txt`, keyed by
`ger.webUrl` host and `ger.project` (see
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
  `branch.*.gerReviewers`.

---

## Pre-push checks (Gerrit mode)

`prepare` runs before `-i`. A push refusal (empty ready range, or a rebase check that refuses) is printed without asking for reviewers. `execute` is the only call that runs `git push`.

0. No rebase underway: the branch ref moves only when a rebase finishes, so mid-rebase `ger push` would send the stack as it was before the rebase. It refuses with exit `2` (`USAGE`) — `--dry-run` too, since the push it previews would not run — and says to finish (`ger rebase --continue`) or `ger rebase --abort` first, the words every ger command uses (`rebase_next_step.FINISH`). A `git am` underway refuses the same way, with its own reason: the branch moves with each patch it applies, so a push would send it half applied (`git am --continue` or `git am --abort`).
1. Target ref resolves locally (fetch if needed).
2. Ready boundary: stop pattern / ready strategy (unless `--all`), tightened by the first missing/malformed/duplicate Change-Id (always, including with `--all`).
3. Optional: `ger.pushRemotePolicy` linearity check (unless `--no-rebase-check`).
4. With credentials, what Gerrit already holds: abandoned changes, commits that already are the current patchset or an older one (outdated, not sent), and your drafts on the pushed changes (one `has:draft` query, then a drafts GET only where it found some). Best effort: a failed lookup finds nothing.
5. No **diverged** change: when a commit is new to its open change (`newer`) and that change's current patchset is not in its history (CONTEXT.md **diverged**, ADR-0015: fetching the patchset does not make it yours), the push would replace somebody's upload with a commit not built on it. It refuses (exit `1`) and names the commit, the change and patchset, and the two ways on: merge it in with `ger checkout change:<n> --update`, or `--force`. Nor a commit sent above an **outdated** one: Gerrit would chain its new patch set on the old patch set below, not on the current one (the relation chain shows it NOT CURRENT, and the stack lacks the web edit). It refuses the same way: `` error: <sha> would be built on patchset 1 of change N; Gerrit has patchset 2. Take it first (`ger checkout change:N --update`), or push anyway with --force ``. With `--force` the tip goes up as asked, and the outdated commit below still gets no patch set.

---

## Confirmation output

Prints: pushable commits, ready-boundary notice when a blocking commit exists, confirm status line, and `git push …`. The status line's `Reviewers` field says what the push does to reviewers, not who the changes will have: `+alice, +bob`, `none added`, or with `overwrite` `set to alice`; then, dim, who reviews the pushed changes already (`, on Gerrit: pg-bob`), and for a change the push creates in a reviewed chain who it gets (`; new change: +pg-bob`, or `; new change: none`; [above](#a-new-change-in-a-reviewed-chain)). The notice is two lines, the same on every path (`--dry-run`, `-y`, the prompt, and a push that has nothing to send, where it comes before `nothing to push`):

```
Stopped at commit "84e9a26  # wip: fmt: locale support, half done", because it matches the stop pattern `^(?:wip\b)`.
... 2 commits from 84e9a26 up held back by the ready boundary; `ger push --all` sends them as work-in-progress
```

Held back means this push leaves them as they are; Gerrit may have them already. When a Change-Id problem set the boundary, `--all` does not pass it, so the second line names the fix instead, which needs no commit-msg hook: `` `ger change-id --fix` gives it a new one `` for an invalid or duplicate Change-Id. A **missing** Change-Id holds nothing back on the branch checked out: before its preview, `ger push` adds one to every commit of the stack that has none (`ger change-id --fix`'s rewrite, message only, as the commit-msg hook would have at commit time), says so (`Adds a Change-Id to 2 commits, as a commit-msg hook would: "…", "…" (kept only if you push)`) and goes on with those commits, which are new commits now. The ids stay only once the push is sent: a push that is then cancelled, refused before it is sent, or interrupted puts the branch back where it was (`git update-ref`, the work tree untouched: the trees are the same) and says `The Change-Ids added to 2 commits are taken back: your commits are as before.` A `fixup!` commit without a footer gets none: it is squashed before it is pushed, and holds the push back there. `ger log` and `GET /stack` judge the ready boundary the same way, so a commit with no Change-Id is not shown as held back on the branch checked out (`adds_change_id` in `GET /stack`). `--dry-run` changes nothing and says `ger push (without --dry-run) adds the missing Change-Ids first`; pushing another branch (`--branch`) only names the fix, `` `ger change-id --fix` adds one (ger push does, on the branch checked out here) ``. Prompt, on a terminal: the push-options input line (`Push options (Enter pushes, Up: history, Ctrl-C cancels):`). Enter on the empty line pushes with the options the status line shows (no reviewers unless `--reviewers`/config gave some); a typed line (`r=alice,bob topic=x wip …`, with completion and history) sets them first; Ctrl-C / Ctrl-D cancels (exit `0`). In a terminal fewer than 12 rows high (a panel dragged low) the line comes without its completion menu and status toolbar, so it fits, after `(the terminal is 2 rows high, too low to show what is pushed: make it taller and Enter pushes; Ctrl-C cancels)`; prompt_toolkit would show only "Window too small..." instead. There Enter does not push: it says the same line again, until the terminal is 12 rows high or more, so a push is never confirmed with its preview off screen.

Each way a push ends is left as its **step outcome**, which `GET /stack` serves as `last_outcome`
([serve](serve.md)) for a client that ran it in a terminal: `pushed 2 commits to refs/for/main:
change 2131, 2132`, `nothing to push: Gerrit already has these commits`, `push cancelled: nothing
sent` (with the Change-Ids taken back, when it added some), `refused: …` (an empty ready range, a
rebase check, no upstream) and Gerrit's refusal (`Gerrit rejected the push: branch no-such-branch
not found`).

The line also answers the prompt itself, and these words are never reviewers (a reviewer named like one is written `r=y`): `y` / `yes` push, like Enter; `n` / `no` / `q` / `quit` cancel, like Ctrl-C; `?` / `help` print the line's grammar and open the line again with what was typed. They can end a line (`r=alice y`). A line that starts filled in (`publish-drafts`, `chain-reviewers`, or `-i`'s reviewers) ends in a space, so what is typed next is a word of its own.

**Reviewers are checked before anything is sent.** Under the `push` strategy, each reviewer the push would send (typed, `--reviewers`, or from config) that is not already on one of the pushed changes is looked up once with `GET /accounts/{name}` when the line is accepted, after the prompt has closed ([ADR-0006](../../adr/0006-push-prompt-gerrit-io-is-background.md)). A name Gerrit has no account for keeps you at the prompt: `` error: Gerrit has no account `nosuch`; nothing was pushed or set. Fix the name, or drop it with -nosuch ``, and the line opens again as typed; nothing of a refused line is applied. Off a terminal (`-y`, `GER_ASSUME_YES`, `--dry-run`) the same message is a usage error (exit `2`), before any `git push` or REST call. `lazy` and `overwrite` add reviewers over REST after the push, where a group is a reviewer too, so their names are left for Gerrit to judge (see the strategies above). Without credentials, or when Gerrit cannot be asked, nothing is checked. Without a terminal on stdout, the question is `Do you want to push these commits? [Y/n/r]:`. A push that sends no commit and only sets options over REST asks `Set these on Gerrit? [Y/n/r]:` instead (see above). No prompt with `--dry-run` / `-y` / non-TTY without `-y`, nor when the push would change nothing on Gerrit (see above).

**Drafts.** When you have drafts on changes the push updates, the push-options input line is the same one, and the `publish-drafts` keyword decides: with it the push adds Gerrit's `publish-comments` push option, without it the drafts stay. The line starts as `publish-drafts` and the status line shows `Drafts publish N`, so Enter pushes and publishes; delete the keyword (or type `-publish-drafts`) to keep them. With `ger.pushPublishDrafts = false` the line starts empty and the status line shows `Drafts keep N`; `--publish-comments` starts it with `publish-drafts` either way. Without a terminal on stdout the question is `Push and publish N drafts? [Y/n/p/r]` instead: yes publishes, `p` pushes and keeps them. Whenever the push does not publish, it sends `no-publish-comments`, so an account preference that publishes on push cannot publish them behind your back. Drafts on commits Gerrit already has (no new patchset) cannot be published by a push; the preview names them (`drafts on … stay drafts`). Details: [reply.md](reply.md#publishing-with-ger-push).

**After the push.** git's own output comes first, untouched; its URLs are whatever host the server thinks it is. Then, with Gerrit's view of the changes, ger's closing lines: each change the push gave a patch set, with the patch set's page built from `ger.webUrl`, and what became of your drafts on them:

```
Pushed to refs/for/main:
    97f681f  # history: add undo  change 2101, patchset 3  http://gerrit.example/c/proj/+/2101/3
2 drafts stay drafts; publish with `ger reply --publish`, or push with --publish-comments
```

(or `published 2 drafts with the push`). That is one request for the pushed changes, whose cached state the push dropped: the fetch the next `ger log` would make, which it then finds current. With `-y` the status line (`Branch | Target | Reviewers | Topic | WIP | Private | Drafts`) is printed too, before the push, as at the prompt.

After success, optional `lastPush/<branch>` marker (`ger.lastPushedBranch`, default on).

---

## Exit codes

`0` includes a dry run, nothing to send, and a push you cancelled at the prompt. `0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract. A failed `git push` returns git's own exit code ([exit-codes.md](../exit-codes.md#what-is-not-remapped)). When Gerrit rejects the push (`! [remote rejected] … (reason)`), git's output is printed untouched and ger adds one line after it with Gerrit's reason, its lines joined by `; `: `error: Gerrit rejected the push: Account 'nosuch' not found; nosuch does not identify a registered user or group`


---

## Configuration

| Key | Effect |
|-----|--------|
| `ger.remote` | Gerrit remote name (default `origin`) |
| `ger.pushShowAttributes` | Reviewer/wip/private preview |
| `ger.lastPushedBranch` | Local `lastPush/<branch>` after push |
| `ger.pushRemotePolicy` | Remote tip linearity |
| `branch.*.gerTarget`, `gerReviewers` | Destination & default reviewers |
| `ger.stopPattern` | Ready boundary (single regex; see [Configuration.md](../../Configuration.md)) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [Configuration.md](../../Configuration.md) — `branch.*.gerTarget`, `gerReviewers`
- [`ger change-id`](sha-change-id.md#ger-change-id)
