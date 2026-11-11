# Gerrit is asked lightly, and in full only for what moved

Reading the access log of a Gerrit server (`httpd_log`) next to what `ger` does showed where
the load was. The **trust window** made a warm `ger log` free, but past it, and in the
polling `ger serve` does for an editor, most requests bought nothing:

- every command sent a `project:P since:"…"` query with all detail options. It returns every
  change anybody touched in the project since the watermark, in full, up to 500, and
  [ADR-0007](0007-follow-up-cache-key-is-note-db-meta.md) had already made its answer
  insufficient: the freshness probe ran after it anyway;
- a probe that found a row unchanged did not renew its trust, so the next load, even in the
  same command, probed again (`ger edit -n`: four requests where one says everything);
- each poll of the overview asked both inbox queries with all detail options
  (`SUBMITTABLE` makes Gerrit evaluate submit rules for every row), the caller's account, and
  the commit below each chain, and threw the answers away.

## Decision

One rule for every read of changes: **ask Gerrit lightly what is there, and fetch details only
for what the cache does not have current.**

- A light request is a query with `SKIP_DIFFSTAT` only (`LIGHT_QUERY_OPTIONS`). Every
  ChangeInfo carries the freshness token of ADR-0007 (`change_freshness_key`), which is all
  the cache needs to decide. `GerritCache.reconcile` is that decision, in one place.
- A row Gerrit just confirmed is as good as a row just fetched: its trust window starts again.
- There is one detail shape (`LOG_QUERY_OPTIONS`, now with `CURRENT_COMMIT`), so a change
  fetched for the stack serves a review chain and the other way round. The separate inbox
  option set is gone.
- Listings (`ChangeApi.query`: both inbox sections, a chain by numbers) are still asked on
  every call, because what matches a query is not something the cache can know. They are
  asked lightly. Up to 25 moved changes are fetched by number; past that, the query is asked
  once more in full.
- Facts that do not change are remembered: the caller's account id (per `gerrit.user`, a
  week), and commits that are no open change, so the ground a review chain sits on is not
  looked up on every poll: a merged change for a week, a commit Gerrit shows no change for
  only a quarter of an hour, because it may be a change the user cannot see yet.
- The `since:` delta, its watermark and the per-scope certification are removed.

Measured on Gerrit 3.10 with an eight-change stack (requests as the server logged them):

| | before | after |
|--|--|--|
| `ger log`, trust window expired | 2 (one with full details) | 1 light |
| `ger edit -n` right after | 4 | 0 |
| editor poll (overview + stack), nothing changed | 6, about 54 KB, 3 with full details | 2-3 light, about 14 KB |
| `ger show` of a stack commit | 5 | 3 |
| `ger inbox`, second run | 2 | 1 light |

## Considered options

**Keep the delta and fix its hole.** A per-row floor instead of one watermark could make an
empty delta trustworthy. It would still ask for the whole project's recent changes to learn
about one stack, which is the wrong question on a busy server.

**Cache listings inside the trust window.** Rejected for now: an inbox that is ten seconds
stale after you voted reads as broken, and the light listing is already cheap.

**Trust the light listing for `submittable`.** This is what the decision does, and it is the
same assumption the stack has always made: submit requirements and labels move the NoteDb
meta ref. A project that changes its submit rules shows the old verdict until the change
moves or `--refresh` is used.

## Consequences

A cold start costs one request more than before for a listing (light, then in full). After
that, the cost of polling follows what changed, not how much you have open.

The numbers above are pinned by `tests/test_call_budgets.py` and `tests/test_inbox.py`.
[Server-load.md](../Server-load.md) says how to read them off a server again.
