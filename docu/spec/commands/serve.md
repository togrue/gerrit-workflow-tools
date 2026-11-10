# `ger serve`

| | |
|--|--|
| **Status** | **First slice** — the endpoints below; no push, vote or publish over HTTP, no event stream |
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
request to bypass it, like `ger --refresh`.

---

## Errors

A failure answers with a status and one shape:

```json
{"error": {"code": "NOT_FOUND", "message": "no matching Gerrit change for 'change:9'"}}
```

`code` is the name of the shared exit code ([exit-codes.md](../exit-codes.md)) the same failure
gives on the command line — one reason, one code. Beside those: `REFUSED` (409; your edits, a
dirty worktree or a conflict stand in the way and nothing was changed), `FORBIDDEN` (401/403)
and `INTERNAL` (500, a bug).

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

`name`, `version`, `api`, `repo`, `branch`, `host`.

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
is shown: a branch without an upstream still shows your inbox.

*Activity* is the client's to track: every chain carries `last_activity`, and a client that
remembers what it showed last knows which chains moved.

### `GET /stack`

The **annotated stack** of the working branch: `ger log --json`, plus per commit `short_sha`,
`updated`, `reviewers`, `is_head`, `held_back` (at or above the **ready boundary**) and
`needs_edit` (edit attention). `summary` counts `commits`, `ready`, `attention`, `needs_edit`
and `not_on_gerrit`. `pipelines=1` adds `ci_pipelines`.

### `GET /chains/<number>`

The **review chain** an open change sits in, base to top, in the `ger inbox --json` chain shape.

### `GET /show`

`ger show --json` for one or more targets: overlay state, `comment_chains`, and with `ci=1` the
`ci_content` of each failed **CI link**.

| Parameter | Meaning |
|-----------|---------|
| `rev` | A changeish. Repeatable. |
| `stack=1` | The whole local stack. |
| `comments` | `unresolved` (default), `all`, `resolved`. |
| `context=N` | Source lines around each comment. |
| `ci=1` | Fetch **CI content** (cached per build). |
| `chain=1` | The `rev` values are one chain, base first. |
| `pipelines=1` | Add `ci_pipelines`. |

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

### `GET /file?rev=<commit>&path=<path>`

`content` of a file at a commit id from `/diff`; `null` when the file is not there (added or
deleted on that side).

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
| `/stack/reorder` | `order`: commit ids, oldest first | Reorder the local stack. Answers the new `/stack`. |

### Reordering is all or nothing

`order` must name every commit of the local stack exactly once (full or abbreviated ids).
The stack is rewritten with one non-interactive rebase; **Change-Ids** travel with their
commits, so Gerrit sees the same changes in a new order after the next push.

It is `REFUSED`, with the branch exactly as it was, when the worktree has uncommitted changes,
a rebase is in progress, HEAD is detached, the stack contains a merge, or a commit does not
apply in its new place (the rebase is aborted and the message names the commit).

---

## What stays on the command line

Anything that prompts or needs a human in front of it: `ger push`, `ger edit`, `ger rebase`,
`ger review --vote`. A client starts those in a terminal.
