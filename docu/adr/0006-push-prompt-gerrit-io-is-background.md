# Push-options prompt Gerrit IO runs off the UI thread

Interactive `ger push` reviewer completion and soft validation talk to Gerrit.
Those REST calls must not run on the prompt-toolkit UI thread: a stalled or
slow Gerrit makes typing and toolbar redraws feel laggy.

## Decision

`ReviewerCatalog` owns a background worker. The UI thread only reads caches and
enqueues work. When a result arrives, the catalog updates its caches and
invalidates the prompt so completions and the toolbar refresh.

All catalog Gerrit REST (warmup, prefix completion, soft validation) shares one
start-to-start rate limit of **one request per 0.5 seconds**. Soft validation
never blocks Enter. REST failures soft-degrade (disable the client, status note,
local seeds still work). Stale replies are dropped via a per-key generation
guard. The toolbar stays silent while lookups are pending (local-first; remote
fills in).

## Considered options

**Blocking REST on the UI thread** (the previous behaviour) was rejected: it is
the lag users feel.

**prompt-toolkit native asyncio** was rejected for this change: higher rewrite
cost than a worker thread plus `invalidate`, for the same UX.

**Warmup burst then live rate limit** was rejected: a shared limiter matches the
literal budget and avoids a burst when the user types during warmup.

**Prefetch-before-open only** was rejected: it either delays the prompt or loses
live prefix discovery for names not in seeds.

## Consequences

- `ReviewerCatalog.from_runtime` returns immediately with local seeds; plugin
  project reviewers and change suggestions warm in the background.
- `push_input_prompt` installs an invalidate callback and closes the catalog when
  the prompt exits.
- The architecture note that threading the catalog through prompt-toolkit was
  “not worth it” is superseded by this ADR.
