# Follow-up cache validity is NoteDb meta, not `ChangeInfo.updated`

Comments and Checks rows are cached above `GerritRest` and were considered fresh for as
long as `ChangeInfo.updated` had not moved. That assumption is false: Gerrit often stores
`updated` at **second precision**. A comment reply or resolve in the same second (typical
of the web UI, and of a `ger show` immediately afterwards) leaves `updated` unchanged,
so the freshness probe keeps the cached ChangeInfo and the comments cache never
refetches. Clearing the SQLite cache was the only way to see the new thread.

## Decision

The probe token is `change_freshness_key(ChangeInfo)`:

1. `meta_rev_id` when Gerrit sends it — the NoteDb meta SHA, which moves on every review
   publish.
2. Otherwise `updated` plus `unresolved_comment_count` and `total_comment_count`, so hosts
   without `meta_rev_id` still see a resolve in the same second.

Follow-ups (`load_comments`, `load_checks`) take that token as `change_updated`. A
changed token refetches even inside the trust window. The 10-second trust window still
skips the *probe*; it is not an unbounded comments TTL.

`LogCommit.updated` stays the timestamp (inbox wait-age). `LogCommit.freshness` is the
cache key.

A certified stack used to skip the probe after a `since:` delta. `since:` is keyed on
`updated`, so a same-second resolve made the delta empty (or named some other change) and
the cached ChangeInfo was served forever — the same sticky comments, even after the trust
window. The delta now only covers ids it actually returned; the rest still go through
`probe_changes_updated`.

## Considered options

Comparing only `updated` is cheaper and was the original design. It cannot see
same-second NoteDb writes. Always refetching comments after the trust window would also
fix `ger show`, but would miss `ger log`'s `unresolved_comment_count` on the cached
ChangeInfo and would pay an extra GET on every show. The meta SHA is one field already on
the probe row (`SKIP_DIFFSTAT`).

## Consequences

A ChangeInfo probe row must carry `meta_rev_id` or the comment-count fields. The live
Gerrit 3.10 / 3.13 query path does. Hosts that omit all three fall back to `updated` alone
and can still miss a same-second resolve.

Expired-trust `ger log` on a certified stack is one `since:` query plus one probe for the
ids the delta did not re-issue, not a delta alone.
