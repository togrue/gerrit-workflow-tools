# `ger serve`

| | |
|--|--|
| **Status** | **First slice** — the endpoints below; no push, vote or publish over HTTP. Gerrit's event stream is followed when it can be |
| **Module** | `src/gerrit_workflow_tools/cli_serve.py`, `src/gerrit_workflow_tools/server/` |
| **Requires** | A git worktree, `gerrit.webUrl`, credentials |
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
`gerrit.webUrl` is not one of them: the service starts, and its requests fail with `CONFIG`
until it is set (`ger setup`).

| Option | Meaning |
|--------|---------|
| `--port N` | Listen on port `N`. Default: any free port (read it from the JSON line). |
| `--exit-with-stdin` | Stop when stdin closes, so the service ends with the process that started it. The service keeps that stdin to itself: the git processes it runs get an empty one. |

### Access

The service can check out branches and rewrite the local stack, so nothing but its client may
reach it:

- It listens on `127.0.0.1` only. There is no option to change that.
- Every request carries `Authorization: Bearer <token>`; the token is new on every start.
- The `Host` header must name loopback, which stops a web page that rebinds its own name.
- A request body must be `application/json`, which a cross-site form cannot send.

### Requests are serialised

One request runs at a time. Core memoises git answers per process, and each request starts by
dropping that memo and taking a fresh **Settings** snapshot, so an answer always describes the
repository as it is now. A slow request (first CI log fetch) delays the next one.

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
`gerrit.remote` URL when it is an ssh URL (`ssh://user@host:29418/…`, `user@host:…`; without
a port, ssh's own config decides), else the host of `gerrit.webUrl` on port 29418 as
`gerrit.user`. Nothing is asked over the network to find out. The settings are read once,
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
was cached here, it is in the repository's Gerrit project or in `inbox.projects`, or it names
you (owner, author, reviewer … with `gerrit.user` or the ssh user as username). It also goes
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

| Code | Status |
|------|--------|
| `USAGE` | 400 |
| `NOT_FOUND` | 404 |
| `AMBIGUOUS`, `REFUSED` | 409 |
| `MISSING_CHANGE_ID` | 422 |
| `GERRIT` | 502 |
| `CONFIG`, `GIT`, `INTERNAL` | 500 |

---

## Read endpoints

All under `/api/v1`.

### `GET /health`

`name`, `version`, `api`, `repo`, `branch`, `host`; `activity` (the activity sequence) and
`events`: the event stream's `state`, `reason` and `source` (`ssh://user@host:port`, or `null`).

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
- `headline` — the **headline**: `section`, `reason`, `chain`, `number`, `subject`, `url` and a
  sentence in `text`. `null` when nothing wants you.
- `local` — the answer of `GET /stack`.
- `generated`, `host`.

The inbox and the local stack fail separately. Either may be `{"error": {…}}` while the other
is shown: a branch without an upstream still shows your inbox. A failed `local` carries
`needs_upstream`: `true` when the branch has no upstream, which `POST /stack/upstream` fixes.

*Activity* is the client's to track: every chain carries `last_activity`, and a client that
remembers what it showed last knows which chains moved.

### `GET /stack`

The **annotated stack** of the working branch: `ger log --json`, plus per commit `short_sha`,
`updated`, `reviewers`, `is_head`, `held_back` (at or above the **ready boundary**) and
`needs_edit` (edit attention). `summary` counts `commits`, `ready`, `attention`, `needs_edit`
and `not_on_gerrit`. `pipelines=1` adds `ci_pipelines`. When Gerrit could not be asked at all,
the commits still come back with `patchset_status: "unknown"`, and `gerrit_unavailable` is the
`{code, message}` saying why.

For a view that draws the stack, each commit also says:

- `diverged` — its patchset status is `newer` *and* Gerrit's current patch set is a commit this
  repository never had: somebody else, or another checkout, uploaded it. Told from the object
  database alone, so a patch set you fetched without building on it does not count.
- `merge` — it is a merge commit. The stack is first-parent, as Gerrit relates changes: the
  merge is a stack commit, what it merged is not. A stack with one cannot be rewritten here.
- `rebase` — while a rebase is underway (`rebase_in_progress`), the stack is the branch as it
  was before, and this is where the rebase stands on each commit: `done` (replayed), `current`
  (it stopped here), `todo` (still to come), or `null` for a commit the rebase does not replay.
  The top-level `rebase` is `null` without one, else `{applied}`: `false` while the `current`
  commit is only partly there, stopped on a conflict.

### `GET /stack/moves`

The conflict preview for reordering: which neighbours of the local stack can swap places.
Nothing is touched — no rebase starts, the worktree and the branch stay as they are.

- `head` — the stack this answer is for.
- `swaps` — one entry per adjacent pair, oldest first: `lower`, `upper` (commit ids) and
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

### `GET /chains/<number>`

The **review chain** an open change sits in, base to top, in the `ger inbox --json` chain shape.

### `GET /show`

`ger show --json` for one or more targets: overlay state, `comment_chains`, and with `ci=1` the
`ci_content` of each failed **CI link**. A `rev` naming a **sibling** on another branch than the
local commit with its Change-Id pushes to is that change, `local_commit: false`, with the commit in
`resolution.local_sibling` ([show](show.md)).

Each commit also says what a new comment on it becomes by default, in `new_comment`: `draft`
when Gerrit has that very commit as a patchset (or the change is only on Gerrit), else
`local-draft` — never pushed, or rewritten here since. A `note` is always possible.

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

With `ci=1` and `stack=1` or `chain=1`, each commit also carries `ci_introduced`: the
fingerprints of its **introduced findings**. It is `null` where that cannot be told — the
commit is not on Gerrit or is ahead of the patchset CI ran on, or its parent's CI log could
not be fetched. A single `rev` never gets `ci_introduced`: without its parent there is nothing
to compare with.

### `GET /diff?rev=<changeish>`

The files a change touched against its first parent: `commit`, `base`, `subject`,
`local_commit` and `files` (`path`, `status`, `old_path`, `insertions`, `deletions`). A change
that is not in the repository is fetched from `gerrit.remote` first.

With `since=reviewed` the answer is the **review delta** instead: the current patchset against
the newest one you posted a vote or comment on. `since=<n>` compares with patchset `n`. The
shape is the same — `base` is the reviewed patchset's commit, so `/file` serves both sides —
plus `since`: `patch_set`, `current_patch_set`, `up_to_date` and `rebased`. `patch_set` is
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
(`local_commit` is then true). A patchset is compared with an older one only; a local commit
with any patchset Gerrit has. Without `since`, the answer also carries the commit's `message`
(the whole commit message) and `author`.

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
origin cannot be told, the finding counts as to fix. A commit with nothing but holds and
inherited findings is not a step: it is listed as waiting for a human.

Commits are named by **Change-Id**, because their ids change as the commits below are amended.

In `GET /show`, every comment chain carries `held`.

### `GET /review`

The **review state**, as `ger review --status --json`, under `review`; `null` when none.

---

## Actions

`POST`, JSON body. None publishes anything to Gerrit.

| Endpoint | Body | Does |
|----------|------|------|
| `/review/open` | `change`, optional `patchset`, `mode`, `stage`, `force` | `ger review CHANGEISH`: the **review checkout**. Answers `review` and `moved_from`. |
| `/review/done` | — | `ger review --done`. |
| `/comments/reply` | `rev`, `comment_id`, `message`, optional `resolved` | `ger reply`: a **draft** reply in that chain. Publishing stays with `ger push` and `ger review --vote`. |
| `/comments/new` | `rev`, `location`, `message`, optional `state`, `resolved` | `ger comment`: start a chain. `location` is `PATH`, `PATH:LINE`, `PATH:START-END` or `PATH:L.C-L.C`, lines from 1. `state`: `draft` (on Gerrit), `local-draft` or `note` (both only here); the default is a draft where Gerrit has the change, else a local draft. Answers `ger comment --json`. |
| `/comments/hold` | `root_id`, `held` | Put a comment chain on **hold**, or release it. Local: nothing goes to Gerrit. |
| `/stack/rewrite` | `steps`, oldest first; optional `autostash` | Reorder, fold or drop commits of the local stack. Answers the new `/stack` plus `previous_head` and `stash_kept`. |
| `/stack/reword` | `rev`, `message` | Give one commit of the local stack a new commit message. Answers the new `/stack` plus `previous_head`. |
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
lost the commit's **Change-Id** footer gets it back; one that names another Change-Id is
taken as written. It is `REFUSED` for an empty message, a commit outside the stack, a rebase
in progress or a detached HEAD.

A rewrite is `REFUSED`, with the branch exactly as it was, when the worktree has uncommitted
changes (and no `autostash`: `reason` is `uncommitted-changes`),
a rebase is in progress, HEAD is detached, the stack contains a merge, or a commit does not
apply in its new place. The last is found out before anything is touched, with the same
simulation as `GET /stack/moves`, and the message names the commit and the files it
conflicts in. Where git is too old for that, the rebase is tried and aborted.

---

## What stays on the command line

Anything that prompts or needs a human in front of it: `ger push`, `ger edit`, `ger rebase`,
`ger review --vote`. A client starts those in a terminal.
