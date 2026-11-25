# `ger serve`

| | |
|--|--|
| **Status** | **First slice** — the endpoints below; no push, vote or publish over HTTP. Gerrit's event stream is followed when it can be |
| **Module** | `src/gerrit_workflow_tools/cli_serve.py`, `src/gerrit_workflow_tools/server/` |
| **Requires** | A git worktree, `ger.webUrl`, credentials |
| **Client** | The VS Code extension in [`vscode/`](../../../vscode/README.md) |

A local HTTP service over the same core the commands use, so an editor can show review state
without parsing terminal output. Every endpoint is *parse request → call core → JSON*, like a
command module; the JSON is the JSON the matching command prints with `--json`, extended only
where a UI needs more.

It is not a second model: a field that exists in `ger log --json` or `ger show --json` has the
same name and meaning here.

---

## Usage

```
ger serve [--port N] [--exit-with-stdin] [--debug-log]
```

Run it in the repository it should report on. It prints **one JSON line** and then serves
until interrupted:

```json
{"url": "http://127.0.0.1:53817", "api": "/api/v1", "token": "…", "repo": "/work/repo", "pid": 4242}
```

When it cannot serve, that line is the reason instead, in the shape of a request's failure
([Errors](#errors)), and it exits with the matching code. Outside a git checkout:

```json
{"error": {"code": "GIT", "message": "not a git repository; run ger inside a clone"}}
```

A client tells these from a crash (no JSON line at all) without reading stderr. A missing
`ger.webUrl` is not one of them: the service starts, and its requests fail with `CONFIG`
until it is set (`ger setup`).

| Option | Meaning |
|--------|---------|
| `--port N` | Listen on port `N`. Default: any free port (read it from the JSON line). |
| `--exit-with-stdin` | Stop when stdin closes, so the service ends with the process that started it. The service keeps that stdin to itself: the git processes it runs get an empty one. |
| `--debug-log` | Standard helpers: diagnostics to stderr. In `ger serve` that is the requests and their git and Gerrit calls. Each request gets one line with how long it ran and how long it queued behind the one before it ([Requests are serialised](#requests-are-serialised)), followed by its git and Gerrit calls with their times. The call lines are approximate when work overlaps: calls run in parallel inside a request (its comments and drafts) each show their own time, not a share of the request's; and a call a background thread makes meanwhile (following the event stream, loading the reviewer catalog) can be listed under the request running at that moment. The request's own line, its run and queue times, is exact. |

### Access

The service can check out branches and rewrite the local stack, so nothing but its client may
reach it:

- It listens on `127.0.0.1` only. There is no option to change that.
- Every request carries `Authorization: Bearer <token>`; the token is new on every start.
- The `Host` header must name loopback, which stops a web page that rebinds its own name.
- A request body must be `application/json`, which a cross-site form cannot send.

### Requests are serialised

One request runs at a time, because a request may move the working tree (a reorder, a rebase
step) under another that reads it. Each request opens its own run: a fresh **Settings**
snapshot and a fresh read of the repository, so an answer always describes the repository as
it is now. A slow request (first CI log fetch) delays the next one.

Gerrit data is served through the **trust window**, as for any command; add `refresh=1` to a
request to bypass it, like `ger --refresh`. What a poll of `/overview` and `/stack` costs the
Gerrit server: [Server-load.md](../../Server-load.md).

---

## Gerrit's event stream

When it can, the service follows Gerrit's **event stream** — `ssh -p <port> <user>@<host>
gerrit stream-events` — so a client learns about review activity when it happens instead of
at its next poll. It is strictly optional: it needs ssh, a key Gerrit accepts and the *Stream
Events* global capability, and many users have only HTTP. Whenever it is not there, the
service behaves exactly as without it: same answers, same Gerrit calls, no failed request.

**Configuration** ([Configuration.md](../../Configuration.md#ger-serve--gereventstream)):
`ger.eventStream` is `auto` (the default), `on` or `off`. `ger.eventStreamHost`,
`ger.eventStreamPort` and `ger.eventStreamUser` override where it connects. Otherwise: the
`ger.remote` URL when it is an ssh URL (`ssh://user@host:29418/…`, `user@host:…`; without
a port, ssh's own config decides), else the host of `ger.webUrl` on port 29418 as
`ger.user`. Nothing is asked over the network to find out. The settings are read once,
when the service starts.

**ssh never prompts.** It runs with `-T`, `BatchMode=yes`, `ConnectTimeout=10` and
keep-alives (a dead connection is noticed within about 90 seconds), stdin closed and, on
Windows, no console window. A missing key, an unknown host key or a password-only account
is a failure that is reported, never a question; ger never touches `~/.ssh`. Only the events
that can move what ger shows are subscribed to (`-s`): `patchset-created`, `comment-added`,
`change-merged`, `change-abandoned`, `change-restored`, `change-deleted`, `reviewer-added`,
`reviewer-deleted`, `vote-deleted`, `topic-changed`, `hashtags-changed`, `wip-state-changed`,
`private-state-changed`, `ref-updated`, `dropped-output`. Gerrit has no event for the
attention set: that, like anything else, still arrives with the next poll.

| `events.state` | Meaning |
|----------------|---------|
| `off` | Not followed: `ger.eventStream` is `off`, no Gerrit host is known, or (tests) a Gerrit was injected without an event source. `reason` says which. |
| `connecting` | ssh is starting. |
| `live` | Following. A connection is live once it has stayed open 5 seconds or delivered an event. |
| `retrying` | The stream dropped (or, with `on`, never came up); `reason` is the last thing ssh said. It reconnects after 1, 2, 4 … seconds, at most 60. |
| `unavailable` | `auto` tried once and the stream never became live (no ssh, `Permission denied`, no capability, refused); not tried again until the service restarts. |

`on` differs from `auto` only in that it keeps retrying a stream that never came up. A
stream that was live is retried in either mode. Each change of state, except `connecting`,
is one line on stderr (`ger serve: …`), and never the same line twice in a row: whoever
started the service has it in its log.

**What an event does.** An event about a change drops what the Gerrit cache holds on that
change — every row under every key, its comments and checks, a "no such change" verdict —
so the next read asks Gerrit. Nothing else: an event never puts data into the cache and
never lengthens the trust window ([ADR-0012](../../adr/0012-events-invalidate-never-populate.md)).
It is applied under the lock requests run under, so a request that read Gerrit just before
the event cannot store its answer after the event dropped it.

Then the **activity sequence** goes up, when the event concerns this repository: the change
was cached here, it is in the repository's Gerrit project or in `ger.inboxProjects`, or it names
you (owner, author, reviewer … with `ger.user` or the ssh user as username). It also goes
up when a branch of those projects moves (`ref-updated` on `refs/heads/…`), when Gerrit says
it dropped events (`dropped-output`), and when the stream becomes live, stops being live or
turns out unavailable — a client then switches between following and polling. Every other
event is ignored after one look at the cache.

**One-shot commands do not use it.** A command lives for seconds and reads Gerrit through the
trust window anyway; an ssh connection would cost more than it saves. Only `ger serve` follows
the stream.

---

## Errors

A failure answers with a status and one shape:

```json
{"error": {"code": "NOT_FOUND", "message": "cannot resolve 'change:9': Gerrit has no such change"}}
```

`code` is the name of the shared exit code ([exit-codes.md](../exit-codes.md)) the same failure
gives on the command line — one reason, one code: a review checkout over changes to tracked
files is `USAGE`, as `ger review` exits `2`. Beside those: `REFUSED` (409; your commits, a
dirty worktree under a stack rewrite or a conflict stand in the way and nothing was changed),
`FORBIDDEN` (401/403) and `INTERNAL` (500, a bug).

A refusal the client can do something about also carries `reason`, a word for it:
`uncommitted-changes` on `/stack/rewrite` means the same call with `autostash` would go through.
A `CONFIG` failure names the setting at fault where it can: `no-web-url` (`ger.webUrl` is not
set) and `insecure-http` (it is an `http://` address and `ger.allowInsecureHttp` is not set), so
a client words its own advice while `message` stays ger's.

| Code | Status |
|------|--------|
| `USAGE` | 400 |
| `NOT_FOUND` | 404 |
| `AMBIGUOUS`, `REFUSED` | 409 |
| `MISSING_CHANGE_ID` | 422 |
| `GERRIT` | 502 |
| `CONFIG`, `GIT`, `INTERNAL` | 500 |

## API version

`/health` reports the contract's version as `api`, `MAJOR.MINOR.PATCH` (`1.3.0`); the URL
prefix carries only the major (`/api/v1`). It moves on its own, not with ger's package version:

| Bump | When |
|------|------|
| **major** | A breaking change: a field or endpoint removed or renamed, or its meaning changed. The prefix moves with it. |
| **minor** | Fields or endpoints added; what was there means what it meant. |
| **patch** | A fix that leaves the contract as it was. |

The VS Code extension declares the API version it needs in one place,
[`vscode/src/compat.ts`](../../../vscode/src/compat.ts), and checks `/health` once each time
the service starts. A service with another major, or the same major and a lower minor, is
refused: the views say which side to update and offer **Update ger**. The patch is ignored.
When the API fits but ger's package version differs from the extension's, the extension only
warns (and not at all for a dev or local version); it offers **Update ger** only when ger is
the older one. Each comparison and its outcome goes to **ger: Show Log**.

---

## Read endpoints

All under `/api/v1`.

### `GET /health`

`name`, `version` (ger's package version), `api` (the [API version](#api-version)), `repo`,
`branch`, `host`; `activity` (the activity sequence) and `events`: the event stream's `state`,
`reason` and `source` (`ssh://user@host:port`, or `null`). `warnings`: what ger warns about in
this repository's configuration, each with `message` (the line every command logs once: old
key names it still answers from, [config](config.md)) and `argv`, the ger command that fixes it
(`["config", "--migrate"]`, `ger` left out), or `null` when only `git -c` names are at fault.

### `GET /activity`

`activity` and `events`, as in `/health`. With `since=N` (the `activity` a client last saw)
and `wait=S` it answers as soon as `activity` differs from `N`, or after `S` seconds (whole
seconds, at most 30), or when the service stops. Without `since` it answers at once.

It is the one request that waits, so it is not serialised: it runs beside the request being
served and touches neither git nor Gerrit. A client long-polls it while `events.state` is
`live`, `connecting` or `retrying`, and refreshes when `activity` moves.

### `GET /overview`

Everything the first screen needs, in one call.

- `sections` — both **inbox sections**, *to review* then *outgoing* (your open chains): `name`,
  `query`, `summary`, and `chains` in the shape of `ger inbox --json`. Each chain member also
  has `local_sha` — the commit of the **local stack** carrying that **Change-Id**, or `null` —
  and each chain has `local`.
- `headline` — the **headline**: `section`, `reason`, its `severity` (`pending` for a chain
  waiting on your review), `chain`, `number`, `subject`, `url` and a sentence in `text`. `null` when nothing wants you. Failed CI is the headline only on a chain
  with a commit of the **local stack**; another chain's failure leaves the headline to the next
  reason. A chain to review is a candidate only while its turn is yours (`your_turn`); it ranks
  beside unresolved comments on an outgoing chain, the one that waited longer first. While a rebase is underway in the checkout, it is the headline, before anything in the
  inbox and also when the inbox could not be read: `section` `local`, `reason` `rebase`, `stop`
  as in [`GET /stack`](#get-stack)'s `rebase`, `severity` `error` for a conflict (else
  `warning`), `chain` `null`, and the commit it stopped at, as the stack shows it: `number`
  (`null` when it is not on Gerrit), `subject`, `sha` and `url`, all `null` when it is between
  commits. `text` says it: `Rebase stopped at 102: Refactor parser init`, `Conflict in 103: …`.
  Stopped on a fixup folding into a commit, the commit is that one and `folding` is `true`
  (else `false`): `Conflict in 2168: stats: add median() (folding a fixup into it)`.
- `local` — the answer of `GET /stack`.
- `projects` — the Gerrit projects both sections are narrowed to: the repository's own
  (from `ger.project` or the remote URL), as `ger inbox` does inside a clone. `all=1` drops
  the filter and the list is empty: every project on the host.
- `generated`, `host`.

The inbox and the local stack fail separately. Either may be `{"error": {…}}` while the other
is shown: a branch without an upstream still shows your inbox. A failed `local` carries
`needs_upstream`: `true` when the branch has no upstream, which `POST /stack/upstream` fixes.

*Activity* is the client's to track: every chain carries `last_activity`, and a client that
remembers what it showed last knows which chains moved. `last_activity_yours` says that last
update was yours, as far as Gerrit's change data tells (a patchset you uploaded, a vote you
gave, at that moment): your own push is no news to you. A comment does not tell whose it was,
so it always counts as news.

### `GET /stack`

The **annotated stack** of the working branch: `ger log --json`, plus per commit `short_sha`,
`updated`, `is_head`, `held_back` (at or above the **ready boundary**) and
`needs_edit` (edit attention). `summary` counts `commits`, `ready`, `attention`, `needs_edit`
and `not_on_gerrit`. `pipelines=1` adds `ci_pipelines`. When Gerrit could not be asked at all,
the commits still come back with `patchset_status: "unknown"`, and `gerrit_unavailable` is the
`{code, message}` saying why. `next` is `ger log --json`'s: the stack's next steps, as
[log.md](log.md#json-fields) describes them.

For a view that draws the stack, each commit also says:

- `diverged` — its patchset status is `newer` *and* Gerrit's current patch set is not in its
  history: somebody else, or another checkout, uploaded it. Told from git alone (the branch, its
  reflog, `lastPush/<branch>`, `refs/ger/taken/`; ADR-0015), so a patch set you fetched without
  building on it still counts as diverged.
- `merge` — it is a merge commit. The stack is first-parent, as Gerrit relates changes: the
  merge is a stack commit, what it merged is not, and is never walked.
- `pinned` — it is the newest merge or below it (`pinned_count`). It cannot be moved, folded
  or dropped here, as `POST /stack/rewrite` refuses and `GET /stack/moves` leaves out; the
  commits above the newest merge can.
- `fixup_of` — for an unsquashed `fixup!`/`squash!` commit, the id of the commit below it that it
  folds into: the nearest one with the subject it names, as `ger fix` writes it
  (`core/stack.py`, `fixup_targets`); `null` for any other commit, and for a fixup whose original
  is not below it.
- `rebase` — while a rebase is underway (`rebase_in_progress`), the stack is the one `ger log`
  shows then: the commits on `HEAD` above the upstream, as they are now (a replayed or amended
  commit has its new id and its new relation to Gerrit), then the commit it stopped at while
  that is not applied, and the todo, as they were. This is where the rebase stands on each
  commit: `done` (replayed), `current` (it stopped here), `todo` (still to come), or `null` for
  a commit the rebase does not replay (below its `onto`).

The top-level `rebase` is `null` without a rebase, else:

- `branch` — the branch being rebased (`null` for a detached HEAD's); `branch` at the top is
  `null` meanwhile, since nothing is checked out.
- `onto`, `orig_head` — the commit it replays onto, and where the branch was when it started
  (the branch still points there).
- `stop` — why it is stopped: `edit` (the commit it is at is applied whole: amend it, then
  continue; also a conflict resolved and committed by hand), `split` (stopped to edit a commit,
  and `HEAD` has moved on from it: the commits above it, or above where it sat once it was reset
  away, were made at the stop; continue goes on from `HEAD`), `conflict` (paths are still
  unmerged), `resolved` (the conflict is resolved and added: ready to continue), `paused`
  (between commits: `exec`, `break`), or `null` when core cannot tell: `HEAD` moved off where
  git stopped on a conflict, onto a commit that is not the one it stopped at as far as core can
  tell (another subject, no Change-Id), or under a rebase through `am`, whatever the commit.
- `applied` — `false` while the `current` commit is only partly there, stopped on a conflict.
- `current` — the id of the commit it stopped at as the stack shows it, else `null` (between
  commits, or reset away at a `split`).
- `folding_into` — stopped on a `fixup` or `squash` not applied yet: the id of the commit it
  folds into (`HEAD`, a `done` commit of the stack), else `null`. The stop is about that
  commit's change: the `fixup!` commit is not on Gerrit.
- `reused` — the paths of this stop's conflict git rerere resolved from a resolution it recorded
  earlier (staged, or in the file and still `unmerged`): nobody has checked them yet
  ([rebase](rebase.md#going-on-with-a-rebase-underway)). `[]` otherwise.
- `unmerged` — the paths the index holds as unresolved conflicts, at whatever stop (a `git stash
  pop` at an `edit` stop can leave some). Each is the literal path relative to the worktree's top,
  as the file is named (`café.txt`), never git's C-quoted form (`"caf\303\251.txt"`).
- `as_before` — `true` when the stack is the branch as it was before the rebase: a rebase git
  runs through `am` (`git rebase --apply`, `rebase-apply`) keeps no todo, so no commit carries
  a `rebase` step. It still stops only on a conflict, and `stop`, `applied` and `unmerged` say
  which. It is about the backend: `ger log` with an explicit range shows that range as it is
  now either way.
- `actions` — the ways on from this stop, in order, by the verdict `ger rebase` refuses by
  (`RebaseProgress.actions`; `ger rebase --continue|--skip` and `ger edit -n` judge the stop as
  this serves it, `stopped_rebase`): `continue` unless paths are still unmerged, `abort` always,
  `skip` at a `conflict` or `resolved` stop, where it leaves out the commit not applied yet (at
  an `edit`, `split` or `paused` stop the commit is in, and `git rebase --skip` would only throw
  away what is not committed). At a `null` stop where the conflict was committed by hand,
  `skip` once the index and work tree hold nothing it would throw away: it keeps `HEAD` and goes
  on. Then a rebase through `am` has nothing left to continue with (git would stop again, "No
  changes"), so `continue` is left out there. A client offers these and no others. `continue`
  can still be refused over unstaged changes, which this does not look at:
  [`GET /rebase/refusal`](#get-rebaserefusalactioncontinue) does, when asked.
- `refused` — for each way on `actions` leaves out, the refusal `ger rebase --<action>` prints
  for it, by action (`{"skip": "--skip leaves out …"}`): what a client says when one is asked for
  anyway.

`ger log`'s [During a rebase](log.md#during-a-rebase) says where `HEAD` is at each stop, how
`fixup`/`squash` and merge lines of the todo are shown, and what a detached HEAD's rebase shows:
then `branch` (here and at the top) is `null`, and so are `stack.target_branch` and
`stack.push_branch`. A `git am` underway is no rebase: `rebase_in_progress` is `false` and
`rebase` is `null`, but a rewrite or a push refuses, saying to finish or abort it.

`upstream` is how far the working branch's upstream moved on past the stack, after a `git
fetch`: `name` (`origin/main`, as the branch config names it), `behind` (the commits it has that
the branch has not, as `git status` counts them), `argv` (the one ger command that rebases onto
it, `ger` left out: `["rebase", "origin/main"]`, with `--drop-merged-equivalent` once a commit of
the stack is merged on Gerrit with the same code: a client runs it as it is), and the rebase
onto it previewed as [`GET /stack/moves`](#get-stackmoves) previews a swap, nothing touched. The
preview replays the commits `git rebase` would: not those whose patch is upstream already, nor
those `--drop-merged-equivalent` drops; `dropped` lists them (`sha`, `summary`). `conflict` is
`null` when every other commit would apply on the upstream as it is now, else the first the
rebase would stop on: `sha`, `summary`, `kind` (`conflict`, with the `files` it conflicts in; or
`empty`, `files` empty: it applies but changes nothing, its change is already upstream under
another patch, and `git rebase -i` stops there for you to drop it with `--skip`). `preview` is
`false` when that cannot be told, `conflict` `null` then too, and `reason` says why in words (a
merge in the stack: `git rebase` does not replay it; or a git older than 2.40, with git's own
error); `reason` is `null` when there is a preview. `upstream` is `null` without a working branch
or an upstream, and while a rebase is underway: that one is finished first. Cost: one `git
rev-list --count --left-right`, and one `git rev-parse --abbrev-ref` when the branch config does
not spell the upstream as a remote branch (`branch.<name>.merge` not under `refs/heads/`, or the
remote `.`); only once the upstream has moved on, also one `git rev-parse` of it, one `git
rev-list --cherry-pick` and one `git merge-tree` per commit up to the first that stops (a git
older than 2.45 takes only commits there: it refuses the second `git merge-tree` once, and then
one `git commit-tree` per commit goes too). Each request simulates it again: the merge also
depends on attributes (`.git/info/attributes`, `.gitattributes`, `core.attributesFile`) and merge
drivers, which change with no object id changing, so nothing could tell a remembered preview is
still true. `ger log --json` serves the same object, and its text ends
with it as a `next:` line.

`push_blocked` is why `ger push` would refuse a Gerrit push now, whatever the stack holds, or
`null`: `reason` is `rebase` (the branch still points at the commits from before it) or `am` (a
`git am` underway), and `message` is the refusal `ger push` prints. A client asks it before it
starts `ger push`, so it can offer to finish the rebase instead.

`staged_review` is the **staged review** in this checkout, or `null`: the **review state** as
[`GET /review`](#get-review) has it (`chain` and `url` `null`), while HEAD still sits at the
change's parent and its `change-<number>` branch on the change. The stack is then somebody
else's change: `ger push`, `ger edit`, `ger reword`, `ger fix` and `ger rebase` refuse there, and
the working files hold the change at `commit`. Only a review staged *here* counts: from the main
worktree, a review in the review worktree is `GET /review`'s, not this. Cost: nothing while no
review is recorded here, else one `git rev-parse`.

### `GET /stack/moves`

The conflict preview for reordering: which neighbours of the local stack can swap places.
Nothing is touched — no rebase starts, the worktree and the branch stay as they are.

- `head` — the stack this answer is for.
- `swaps` — one entry per adjacent pair above the newest merge commit (all of them without
  one), oldest first: `lower`, `upper` (commit ids) and
  `conflict`: `null` when the two can swap, else `sha` and `summary` of the first commit
  that would not apply, and the `files` it conflicts in.
- `available` — `false` (with `reason`, and no `swaps`) when this git cannot simulate the
  moves; it takes git 2.40. Nothing is known then, and a rewrite still finds out.

Cost: a fixed handful of git calls, whatever the stack's length — every pair's merges go to
one batched `git merge-tree --stdin`. A git whose `merge-tree` cannot take that batch gets
the same answer from one `merge-tree` per pick, which grows with the stack.

### `GET /stack/upstream`

The working branch's upstream, and what to offer when it has none — `ger` cannot show a
local stack without one. `branch` (`null` on a detached HEAD), `upstream` (now, or `null`),
`suggested` and `why`, and `candidates`: the suggestion first, then upstreams you picked
before, then every remote-tracking branch, at most 200.

The suggestion is the branch of the Gerrit remote the branch was created from, as its reflog
tells (`git checkout -b topic origin/main`); else the remote branch nearest to it, by commits
ahead plus behind. Two git calls: all remote branches are measured by one `for-each-ref`
(git 2.41; older git measures them one by one).

### `GET /rebase/refusal?action=continue`

Why `ger rebase --<action>` (`continue`, `abort` or `skip`; `continue` when not given) would
refuse now, in its words, as `refused`; `null` when it would run. This is the one check every ger
"go on" makes before git runs (`ger rebase --continue|--skip`, `ger edit -n`): the stop as
`rebase.refused` judges it, and for `continue` also the work tree, which `GET /stack` does not
look at: unstaged changes to tracked files (`you have unstaged changes to a.txt; amend them in
(…) or git stash them, then ger rebase --continue`). Without a rebase it says there is none. A
client asks it just before it starts the step in a terminal, so a refusal is said where the
client is, not only in the terminal. Cost: two git calls (`update-index --refresh`, `diff-files`)
beyond reading the stop.

### `GET /chains/<number>`

The **review chain** an open change sits in, base to top, in the `ger inbox --json` chain shape.

### `GET /show`

`ger show --json` for one or more targets: overlay state, `comment_chains`, and with `ci=1` the
`ci_content` of each failed **CI link**. A `rev` naming a **sibling** on another branch than the
local commit with its Change-Id pushes to is that change, `local_commit: false`, with the commit in
`resolution.local_sibling` ([show](show.md)). A commit ahead of the patchset CI built, with
other code, keeps that patchset's `ci_content`, marked with its `patch_set` and its findings' lines
mapped onto the local commit ([while you fix it](../ci-content.md#while-you-fix-it)).

Each comment also carries its whole text with the Gerrit fix suggestions written out below it
(`render/suggested_fixes.py`), so a viewer shows them without rendering them itself: `text`
plain, as `ger show` prints them without its box and colours, and `markdown` with each
replacement fenced. A ```` ```suggestion ```` block is in the message already and is not repeated.

For a commit of the local stack (`local_commit: true`), an editor draws the threads in the working
files, which may hold another version of a commented file than the patchset the comment was written
on (a comment on an earlier commit, seen at the tip; a commit changed since its push). So each open
Gerrit chain there also carries `worktree`: where its lines are in the working file, mapped from the
version the comment was written on (Gerrit's `commit_id`) as local comments are re-anchored
([comment](comment.md#re-anchoring), `core/anchor_mapping.py`, `WorktreeMap`): `start_line`, `line`,
and `outdated` when those lines changed or are gone there (`line` is then the one just before where
they were). It is `null` where that cannot be told: the version is not in this repository, the
working file is gone or binary, a resolved chain, a local chain (whose own anchor is mapped onto the
shown commit), or no line. Each replacement a comment of such a chain suggests carries `worktree`
too, its `range` in the working file while the lines it covers are there unchanged (else `null`),
and `original`, the text it replaces in the version commented on: what a client checks the file
against before it applies the edit. Cost, once per commented file: a `git rev-parse`, a `git
hash-object -w` of the working file (so it diffs as a blob), a `git diff` (remembered by blob pair),
and a `git cat-file` where an edit is suggested.

Each commit also says what a new comment on it becomes by default, in `new_comment`: `draft`
when Gerrit has that very commit as a patchset (or the change is only on Gerrit), else
`local-draft` — never pushed, or rewritten here since. A `note` is always possible.

Each commit says whose change it is and what publishes your drafts on it: `own` (you own the
change; `null` when it is not on Gerrit or Gerrit will not say who you are) and
`drafts_published_by`: `push` for your own commit of the local stack (`ger push` publishes the
drafts on the changes it sends), `vote` for any other change on Gerrit (`ger review --vote`,
`POST /vote`), `null` when it is not on Gerrit.

| Parameter | Meaning |
|-----------|---------|
| `rev` | A changeish. Repeatable. |
| `stack=1` | The whole local stack. |
| `comments` | `unresolved` (default), `all`, `resolved`. |
| `context=N` | Source lines around each comment. |
| `ci=1` | Fetch **CI content** (cached per build). |
| `chain=1` | The `rev` values are one chain, base first. |
| `pipelines=1` | Add `ci_pipelines`. |
| `markdown=1` | Add `markdown`: what `ger show --ai` prints for the commit, with the same `comments`, `context` and `ci`. A client hands it to an agent without running `ger` itself. |
| `files=1` | Add `files`: what `GET /diff` without `since` or `patchset` answers for the commit, fetched the same way. One request instead of two, and the commit is resolved once — as `git show` gives the commit with its diff. |

With `ci=1` and `stack=1` or `chain=1`, each commit also carries `ci_introduced`: the
fingerprints of its **introduced findings**. It is `null` where that cannot be told — the
commit is not on Gerrit or is ahead of the patchset CI ran on, or its parent's CI log could
not be fetched. A single `rev` without `chain=1` never gets `ci_introduced`: without its parent
there is nothing to compare with. A commit's entry turns on its parent alone, so a client after
one commit's asks for `rev=<parent>&rev=<commit>&chain=1` (for the chain's base alone, with
`chain=1`, its parent, the target branch, is taken as clean), not the whole chain: every other commit adds its
build logs and its comments to the answer.

### `GET /diff?rev=<changeish>`

The files a change touched against its first parent: `commit`, `base`, `subject`,
`local_commit` and `files` (`path`, `status`, `old_path`, `insertions`, `deletions`). A change
that is not in the repository is fetched from `ger.remote` first.

With `since=reviewed` the answer is the **review delta** instead: the current patchset against
the newest one you posted a vote or comment on. `since=<n>` compares with patchset `n`. The
shape is the same — `base` is the reviewed patchset's commit, so `/file` serves both sides —
plus `since`: `patch_set`, `current_patch_set`, `up_to_date`, `identical` and `rebased`. `patch_set` is
`null` (and `files` empty) when you have not reviewed the change. When the patchsets sit on
different parents, `rebased` is true and `files` holds only files the change touches; inside
those, edits that came in from underneath still show.

What is compared can be chosen:

| Query | Shows |
|-------|-------|
| `patchset=<n>` | patchset `n` of the change against its parent, instead of the commit `rev` names |
| `since=reviewed&patchset=<n>` | the patchset you reviewed against patchset `n` |
| `since=reviewed&local=<commit>` | the patchset you reviewed against the local commit carrying the change |
| `since=current&local=<commit>` | Gerrit against local: the current patchset against that commit |

`since.to_patch_set` is the patchset on the right-hand side, `null` when that is a local commit
(`local_commit` is then true). `since.identical` is true when both sides are the same commit: a local
commit that is the patchset it is compared with. A patchset is compared with an older one only; a local commit
with any patchset Gerrit has. Without `since`, the answer also carries the commit's `message`
(the whole commit message), `message_text` (the message without its Change-Id footer, for an
editor to show), `change_id` (that footer's Change-Id, `null` without a valid one; `message_text`
is then the whole message) and `author`.

### `GET /patchsets?rev=<changeish>`

The patchsets Gerrit has of one change, oldest first: `number`, `current` (the current
patchset's number), `reviewed` (the newest you posted on, or `null`) and `patchsets`, each
with `number`, `commit` and `created`. Asks Gerrit, outside the trust window.

### `GET /file?rev=<commit>&path=<path>`

`content` of a file at a commit id from `/diff`; `null` when the file is not there (added or
deleted on that side).

### `GET /agent/runbook`

The **agent runbook**: the attention walk over the local stack as work an agent can do
unattended. Fetches **CI content**. `rev` (a commit id or Change-Id of the stack) narrows it
to one commit.

- `markdown` — the rules (never publish, never add commits, leave holds alone), the loop per
  commit (`ger edit <Change-Id>`, fix, amend, continue, `ger reply … --done`), then one section
  per commit with the comment chains to address and the findings to fix.
- `commits` — per commit needing edit attention: `comments`, `held`, `findings`, `inherited`,
  `ci_unread`, `agent_work`.
- `totals` — `commits` (with work for an agent), `comments`, `held`, `findings`.

Two things are kept from the agent. A comment chain on **hold** is listed as left for a human.
A finding a commit merely inherits is listed as such and not to be fixed there; where the
origin cannot be told, the finding counts as to fix. A commit with nothing but holds,
inherited findings or a negative vote is not a step: it is listed as waiting for a human.

Commits are named by **Change-Id**, because their ids change as the commits below are amended.

In `GET /show`, every comment chain carries `held`.

### `GET /review`

The **review state**, as `ger review --status --json`, under `review`; `null` when none. With
`chain=1` its `chain` is the review chain's open changes, base first, as `ger review` prints it
(one Gerrit request); without, `null`.

### `GET /vote?change=<changeish>`

What a vote on the change would send, for a client to show before it asks: nothing is sent.
With `chain=1`, every open change of the change's review chain, base first. `vote` (repeatable,
as `ger review --vote` takes it: `+1`, `CR-1`, `Verified=+1`) fills each change's `ruled_out`;
a vote no change takes is `USAGE`, as on the command line.

- `changes` — per change: `number`, `change` (its key), `subject`, `patch_set` (the current one:
  what `POST /vote` names back), `url`, `owner` (a name), `own` (you own it), `permitted` (what
  you may vote, per label; `{}` when Gerrit did not say), `ruled_out`, `yours` (your votes on its
  current patchset, per label), `keeps` (with `chain=1` and `vote`: the votes of yours it keeps,
  per label; the vote does not go there), and `drafts`: each draft
  of yours a publish sends, with `path`, `line`, `location` (`path:line`, in words for the
  commit message and the patchset level), `text` (its first line), `transition` (`resolves`,
  `reopens` or `null`: what it does to its chain) and `suggested_edit` (it suggests an edit: a
  ```` ```suggestion ```` block or a fix suggestion, which its first line may not show).
- `drafts` — how many drafts go out, on the changes that take the votes.
- `options` — every vote some change lets you give, Code-Review first, each label's highest
  value first: `label`, `value`, `meaning` (the project's words for it, "Looks good to me, but
  someone else must approve"; empty when Gerrit did not say), `takes` (the change numbers that
  take it: a chain vote skips the others) and `keeps` (with `chain=1`: of those, the changes that
  carry your vote on its label and keep it). Where Gerrit did not say what you may give,
  Code-Review -2 to +2 is offered and Gerrit itself refuses what you may not.

---

## Actions

`POST`, JSON body. Only `/vote` publishes to Gerrit; a client asks first, as `ger review --vote` does.

| Endpoint | Body | Does |
|----------|------|------|
| `/review/open` | `change`, optional `patchset`, `mode`, `stage`, `force`; or `step` instead of `change` | `ger review CHANGEISH`: the **review checkout**. `step` (`1` or `-1`) is `ger review --next` / `--prev`: the change above or below in the review chain of the review going on, at its current patchset, in that review's mode; at the end of the chain it is `NOT_FOUND`, with `ger review`'s words. Answers `review` (with `chain`) and `moved_from`. |
| `/review/done` | — | `ger review --done`. |
| `/vote` | `change`, `patch_sets`, optional `chain`, `votes`, `message` | `ger review --vote`: publish your drafts on the change (with `chain`, on every open change of its chain), with `votes` (`{label: value}`; `{}` or none: the drafts go out without a vote, as `ger reply --publish CHANGEISH`) and `message` on each. With `chain`, a change that carries your vote on a label keeps it: the vote goes to the others, and your drafts and the message still go there. `patch_sets` (`{change number: patchset}`) is what `GET /vote` showed: when the changes or a patchset are not that any more, nothing is sent and it is `REFUSED`. A change that rules a vote out is skipped; when none takes it, or there is nothing to send (no draft, no vote, no message), or `GER_NO_PUBLISH` is set, it is `USAGE`. Answers as `ger review --vote --json` for several changes: `changes` (each `change`, `number`, `drafts`, `labels` (the votes that went there), `keeps` (your votes it kept), `message`, `status`) and `skipped` (`change`, `number`, `ruled_out`). |
| `/comments/reply` | `rev`, `comment_id`, `message`, optional `resolved` | `ger reply`: a **draft** reply in that chain. Publishing stays with `ger push` and `ger review --vote`. |
| `/comments/new` | `rev`, `location`, `message`, optional `state`, `resolved`, `commit` | `ger comment`: start a chain. `location` is `PATH`, `PATH:LINE`, `PATH:START-END` or `PATH:L.C-L.C`, lines from 1, or one of Gerrit's paths: `/COMMIT_MSG:LINE` (the commit message) and `/PATCHSET_LEVEL` (the change itself), drafts only, as `ger comment` takes them. `state`: `draft` (on Gerrit), `local-draft` or `note` (both only here); the default is a draft where Gerrit has the change, else a local draft. `commit` (a commit id, as `/diff` answers it) is the version of the change whose lines were shown, when a viewer shows another one than `rev` names (an older patchset, or Gerrit's current one beside your rewritten commit): `location` is checked against it and the comment goes on it, a draft on that patchset by default. It is `NOT_FOUND` when it is neither the commit `rev` names nor a patchset Gerrit has of the change. Answers `ger comment --json`. |
| `/comments/edit` | `rev`, `comment_id`, `message`, optional `resolved` | `ger comment --edit`: give one of your unpublished comments a new text: a **draft** on Gerrit (a chain you started or a reply), a **local draft** or a **note**. It stays where it is; without `resolved` it stays as open as it was. Answers `action` (`updated`), `state`, `id`, `change` and `message`: what it said before. A published comment, or an id that is not yours, is not found. |
| `/comments/discard` | `rev`, `comment_id` | `ger comment --discard`: delete one of your unpublished comments. Answers the same, with `action` `discarded`: its text is not lost silently. |
| `/comments/hold` | `root_id`, `held` | Put a comment chain on **hold**, or release it. Local: nothing goes to Gerrit. |
| `/stack/rewrite` | `steps`, oldest first; optional `autostash` | Reorder, fold or drop commits of the local stack. Answers the new `/stack` plus `previous_head` and `stash_kept`. |
| `/stack/reword` | `rev`, `message`, `keep_change_id` | Give one commit of the local stack a new commit message. Answers the new `/stack` plus `previous_head`. |
| `/stack/undo` | `head`, `previous_head` | Take that rewrite or reword back. |
| `/stack/upstream` | `upstream` (a remote-tracking branch, `origin/main`) | Set it as the working branch's upstream, and remember it for next time. Answers `branch` and `upstream`. |

### Rewriting the stack is all or nothing

`steps` must name every commit of the local stack exactly once (full or abbreviated ids),
in the order wanted. A step is a commit id — the commit is kept — or `{sha, action}`:

| `action` | The commit is |
|----------|---------------|
| `pick` | kept |
| `fixup` | folded into the commit below it; that commit's message stays |
| `fixup-take-message` | folded into the commit below it; this commit's message replaces the other |
| `drop` | removed |

The stack is rewritten with one non-interactive rebase. **Change-Ids** travel with their
commits, so Gerrit sees the same changes in a new order after the next push. Folding never
joins two messages — a message with two **Change-Id** footers is not a Gerrit change — so
one of the two changes lives on and the other is left open on Gerrit for you to abandon, as
is the change of a dropped commit.

`/stack/undo` puts the branch back on `previous_head`, keeping uncommitted edits. It works
only while the branch is still on `head`, where the rewrite left it; after a new commit it
is `REFUSED` and the reflog is the way back.

With `autostash`, uncommitted changes to tracked files are stashed for the rewrite and put
back after it — or after its refusal. When they no longer fit the rewritten commits (the file
they edit went with a dropped commit), git leaves them in `git stash` and the answer says
`stash_kept: true`.

`/stack/reword` changes a message and nothing else: every tree stays, the commits from the
reworded one up are recreated on their new parents, and the index and the worktree are not
touched — so uncommitted changes are no obstacle and nothing can conflict. A message that
lost the commit's **Change-Id** footer gets it back, as the last trailer; one that names another
Change-Id is taken as written. With `keep_change_id: true` (a `message` edited as `/diff`'s
`message_text`), every `Change-Id:` line in it is dropped and the commit keeps its own. It is
`REFUSED` for an empty message (one that was only a Change-Id included), a commit outside the
stack, a rebase in progress or a detached HEAD.

A rewrite is `REFUSED`, with the branch exactly as it was, when the worktree has uncommitted
changes (and no `autostash`: `reason` is `uncommitted-changes`), a submodule is staged at another
commit or a file or symlink is put where one is recorded (with or without `autostash`; no
`reason`: the rebase would stop on the staged one and delete the file, and alone the autostash
saves neither. With other tracked changes the autostash would save it with the index,
but the rewrite refuses it all the same rather than rely on what the replay and the unstash make of
it. It is checked before uncommitted changes, so with both and no `autostash` the client gets this
refusal first, not `uncommitted-changes`),
a rebase is in progress, HEAD is detached, it would move, fold, drop or fold into the newest
merge commit or a commit below it (git rebase cannot replay a merge, so the rewrite starts on
top of it), or a commit does not apply in its new place. The last is found out before anything is touched, with the same
simulation as `GET /stack/moves`, and the message names the commit and the files it
conflicts in. Where git is too old for that, the rebase is tried and aborted.

A rewrite replays the stack where it sits: onto the newest merge commit, else onto the commit
the stack forked off its upstream at. An upstream that has moved on since is not rebased onto
(that is `ger rebase`'s to do, which `GET /stack` offers as `upstream.argv`), and
`GET /stack/moves` previews against the same commit.

---

## What stays on the command line

Anything that prompts or needs a human in front of it: `ger push`, `ger edit`, `ger rebase`.
A client starts those in a terminal. The vote is served (`GET /vote`, `POST /vote`): its one
question, "publish this?", is asked by the client with what `GET /vote` answered.
