# Offline / cache-only is a trust window, not a `GerritRest` implementation

`GerritRest` is the seam for single-round-trip Gerrit operations, and the SQLite cache sits **above** it — `GerritCache.load_changes` drives freshness by calling `probe_changes_updated` and then fetching. Offline or cache-only operation is therefore expressed as an unbounded **trust window** on `GerritService` (plus skipping the freshness probe), not as a third implementation of `GerritRest`.

## Considered options

A `CacheOnlyRest` implementing `query_changes` / `get_change` by reading SQLite directly is the obvious-looking alternative, and it was the initial preference. It was rejected because it sits on the wrong side of the cache:

- `GerritCache` would still run above it, so every read would pass through two caches.
- To stop the layer above re-fetching, the implementation would have to fabricate `updated` timestamps — an implementation lying about freshness to control a policy that isn't its concern.

Freshness is a policy question, and `GerritService` already owns the knobs for it (`refresh`, `trust_window_seconds`).

## Consequences

`GerritRest` has two implementations rather than three: `HttpGerritRest` and `ChangeStore`. If you are looking at the seam and thinking "there is only one production implementation, we should add a cache-only one" — that is this decision, and the reason it doesn't work is not visible from the code.
