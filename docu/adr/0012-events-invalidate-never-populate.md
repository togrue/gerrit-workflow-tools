# Gerrit events invalidate the cache; they never populate it or widen the trust window

`ger serve` can follow Gerrit's event stream (`ssh … gerrit stream-events`). An event says a
change moved. The obvious next steps are to *use* that knowledge: skip the freshness probe
while the stream is live (nothing can have changed without an event), lengthen the **trust
window**, or write the event's `change` attribute into the cache as a cheaper ChangeInfo.

## Decision

An event only ever narrows trust. `apply_event` drops every cached row of the change it names
(`GerritCache.forget_change`, keyed by change number, so every key the change was stored under
goes, follow-ups and "no such change" verdicts included). It writes nothing else into the
cache and changes no freshness rule: the trust window, the probe and ADR-0007's freshness key
behave exactly as without events. What the stream adds for a client is *when* to look again
(the activity sequence of `ger serve`), not *what* is true.

Inside `ger serve`, an event is applied under the same lock as a request, so a request that
read Gerrit before the event cannot store its now-stale answer after the event dropped it.

## Considered options

**Skip the probe / unbounded trust while live.** Rejected: the stream is lossy by design.
Gerrit drops output for slow readers (`dropped-output`), the connection can die silently
until the keep-alive notices (up to ~90 s), events are filtered by visibility, and there is no
event for some state ger shows (the attention set). Trusting the cache because no event came
would widen the window on exactly the evidence that is least reliable. ADR-0001: never widen
the window to be faster.

**Store the event's `change` attribute.** Rejected: it is not a ChangeInfo (different field
names, no labels in our shape, no `meta_rev_id`), so it would need a second normalisation of
Gerrit state beside `ChangeView`, and a row built from it could not say whether it is stale.

**Re-fetch the change on every event.** Rejected: it spends REST calls on changes nobody is
looking at. Dropping the row costs one SQLite statement; the next read pays only if someone reads.

## Consequences

- With the stream off, unavailable or dropped, `ger` behaves exactly as before: same calls,
  same answers (pinned by a test). Events are strictly optional.
- A refresh a client makes after an event still goes through the probe when the row was not
  dropped; correctness never depends on the event having arrived.
