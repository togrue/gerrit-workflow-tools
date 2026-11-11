# What ger costs the Gerrit server

`ger` is meant to be cheap for the server it talks to: many people run it, and `ger serve`
polls for an editor all day. This page says what each command asks, and how to check that on
a server you run.

## The rules

- A cached change is served without asking for the **trust window** (10 seconds).
- Past it, Gerrit is asked **lightly** (one query with `SKIP_DIFFSTAT`, naming the changes),
  and details are fetched only for changes whose freshness token moved.
- Listings (`ger inbox`, the editor's *Review chains*) are asked on every call, lightly.
- No request asks for the diffstat (`SKIP_DIFFSTAT` everywhere): the line counts `ger` shows
  come from git, and counting them makes Gerrit open the commits of every row.
- What cannot change is asked once: your account id, whether the host has the Checks plugin,
  commits that are merged history.

## How old can what you see be

Less asking must not mean older answers. What bounds each thing:

| What you see | At most this old |
|--------------|------------------|
| A change of your stack: votes, patchsets, status, comment counts | the trust window (10 s); past it Gerrit confirms or replaces it |
| "Not on Gerrit" for a commit | the trust window, also when the change was pushed from elsewhere |
| The same Change-Id on another branch (a **sibling**) | as the stack: checked with the same request |
| What a listing contains (`ger inbox`, *Review chains*) | asked every time |
| Details of a listed change | exact: taken from the cache only when Gerrit's freshness token is unchanged |
| Comments | exact, by the same token |
| Your drafts | asked every time |
| Anything you changed through `ger` (push, vote, reply, reviewers, topic) | dropped from the cache by the write, under every key |
| The editor's views | the last poll (`ger.pollSeconds`), a Gerrit event, or a local git change, whichever came last |
| The commit below a review chain | a merged change: a week (it stays merged); no change Gerrit shows: 15 minutes |
| Your account id, whether the host has the Checks plugin | a week |

`ger --refresh <command>` (the editor's *Refresh*) skips all of it and asks for everything.

Known limits, where the freshness token (ADR-0007) cannot see a change:

- A project that changes its submit rules: `submittable` of an untouched change stays as it
  was until the change moves.
- A Gerrit that sends no `meta_rev_id`: the token is `updated` plus the comment counts, so a
  vote within the same second as the last fetch can be missed until the next update.
- Checks-plugin rows are cached by the same token; a check that finishes without voting or
  commenting does not move it.
- A clock set back does not stretch any of the above: a time in the future is not trusted.

Why: [ADR-0001](adr/0001-offline-is-a-trust-window-not-a-gerritrest.md),
[ADR-0007](adr/0007-follow-up-cache-key-is-note-db-meta.md),
[ADR-0013](adr/0013-gerrit-is-asked-lightly-and-only-for-what-moved.md).

## What a command asks

For a stack of eight changes, nothing changed on Gerrit, as a Gerrit 3.10 logged it:

| Command | Requests |
|---------|----------|
| `ger log`, first ever | 1, with full details |
| `ger log` again within 10 s | 0 |
| `ger log` later | 1 light |
| `ger edit -n`, `ger push --dry-run` after a `ger log` | 0 to 2 (drafts are always asked) |
| `ger show REV` | 3: where the Change-Id exists, comments (only when the change moved), drafts |
| `ger inbox` | 1 light |
| Editor poll (`ger serve`: overview and stack) | 2 to 3 light, every `ger.pollSeconds` while the window has focus; with Gerrit's event stream live, every 15 minutes |

A change that moved adds one request for its details, and its comments when you look at it.

## Looking at the requests

What `ger` sent, from the client:

```bash
ger log --debug-log
```

What the server received. Gerrit writes every request to `logs/httpd_log` in its site
directory: client, time, request line, status, bytes, milliseconds. To see the lines one
command caused on a Gerrit in a container you can reach over ssh:

```bash
scripts/gerrit-requests.sh --ssh myhost --container gerrit -- ger log
```

It prints the status, the time Gerrit took, the size and the request, for the lines that
were added to the log while the command ran, so use a server (or a moment) where nobody else
is active. Without `--ssh` it runs `docker` locally. The integration tests' throwaway
Gerrit works the same way ([tests/integration/README.md](../tests/integration/README.md)).

Things worth looking for in a log:

- the same request twice in one command;
- `o=SUBMITTABLE`, `o=DETAILED_LABELS` or `o=ALL_REVISIONS` on a request that repeats;
- a query without `change:` or `commit:` terms and without `owner:`/`reviewer:` (it scans a project);
- requests that return the same bytes on every poll.

The budgets are tests: `tests/test_call_budgets.py` and the listing tests in
`tests/test_inbox.py` fail when a command starts asking more.
