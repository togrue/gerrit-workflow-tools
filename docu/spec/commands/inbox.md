# `ger inbox`

| | |
|--|--|
| **Status** | **Partial** — *to review* is implemented; *waiting on others*, watermark, and `--watch` are still planned |
| **Module** | `src/gerrit_workflow_tools/cli_inbox.py` |
| **Requires** | `gerrit.webUrl`, credentials. **No git repository required.** |

What is waiting on you. Groups open Gerrit changes into **review chains** and reports one entry per chain.

Answers: *how long has this chain gone without my review, and when was it last touched?*

`ger log` looks outward from the **local stack**. `ger inbox` looks the other way — it starts from a Gerrit query and never needs local commits. That inversion is the reason this command exists as its own thing rather than a `ger log` flag.

---

## Usage

```
ger inbox [options]
```

Takes no positional arguments. Runs from **any directory**, including outside a clone: identity and credentials come from `gerrit.*` git config (normally global, written by `ger setup`).

This slice implements the **to review** section only.

---

## Concepts

### Review chain

A maximal set of open Gerrit changes linked by parent/child revision relationships — Gerrit's relation chain, seen from the server. One chain is **one review unit**: the thing you decide to start or not start.

Distinct from the **local stack**, which is defined purely locally (`upstream_tip..HEAD`) and is always yours. A review chain is usually someone else's and may have no local commits at all.

**Chain top** — the member whose current revision is not the parent of any other member. **Chain base** — the member whose current-revision parent is not a member (it sits on the target branch). Depth is the member count.

### Chain assembly

Derived from the query result, not from per-change relation calls:

1. Query with `o=CURRENT_REVISION&o=CURRENT_COMMIT` (plus labels and accounts), giving each change's current revision SHA and its parent SHAs.
2. Link child → parent by matching a parent SHA to another change's current revision.
3. Parent SHAs not present in the result set are collected and resolved with **one batched follow-up query** (`commit:<sha> OR commit:<sha> …`) through `GerritService.fetch_review_chains`.

This keeps assembly at two round trips regardless of chain count. A follow-up that finds the parent commit on a *non-current* patch set (a hole in the current-revision chain) flags `partial-chain` in JSON. A parent that does not exist as any open change is the ground the chain sits on, not a gap.

### Ready

A change is **ready to review** when it is open, not WIP, not private, and passes the CI gate (`inbox.requireVerified`, default on). Everything else is filtered out unless `--all` is given.

### Unreviewed age

Time since the chain last started waiting for **your** review. Prefer Gerrit's attention-set `last_update` for self (how long it has been your turn). If you are not in the attention set: the current patch set uploaded after your last Code-Review vote, or change creation if you have never voted.

Rendered as `3d`, `4h`. It is the sort key — longest without your review first — so the list is a priority queue.

### Wait age / last activity

Time since the last activity on the chain: `now − max(updated)` over members. Rendered as `act 4h`. JSON also carries `last_activity` as an ISO-8601 UTC timestamp.

### Inbox watermark

Planned. Per `(host, section, query fingerprint)`: the last run time plus the chain keys reported then. Drives `--since-last`.

**Not cache.** The Gerrit cache is discardable by contract (`ger cache --clear`); losing a watermark instead causes a burst of false "new" notifications. It therefore lives in **state**, not cache:

```
$XDG_STATE_HOME/ger/<host>/inbox-state.json      (fallback ~/.local/state)
```

`ger cache --clear` must not touch it. `ger inbox --forget` resets it.

---

## Sections

| Section | Meaning | Query (default) | Status |
|---------|---------|-----------------|--------|
| **to review** | Open chains where you are a reviewer | `is:open -is:wip -is:private -owner:self reviewer:self` | Implemented |
| **waiting on others** | Your open chains where the ball is elsewhere | `is:open -is:wip owner:self -attention:self` | Planned |

The *to review* section has `label:Verified+1` appended when `inbox.requireVerified` is on, and `--project` filters folded in as `(project:A OR project:B)`.

A chain appears in **to review** if any member matches; the chain is then fetched whole (via assembly), so members you are not a reviewer on still appear as context.

Within the section, chains sort by **unreviewed age**, oldest first (longest waiting for you). Ties break on wait age, then change number.

---

## Options

| Option | Description |
|--------|-------------|
| `--to-review` | Only the *to review* section (currently the default and the only implemented section) |
| `--project P` | Restrict to a Gerrit project; repeatable. Default: all projects on the host (or `inbox.projects`) |
| `--all` | Include chains filtered out as not ready (WIP, private, CI red) |
| `--limit N` | At most N chains |
| `--json` | Machine-readable output (see [JSON](#json)) |
| `--url`, `--show-url` | Chain-top URL per entry (default: **on**) |
| `--no-url` | Omit URLs from text output |
| `--color`, `--debug-log`, `-v` | Standard helpers (see `cli_common`) |

Planned, not in this slice: `--waiting`, `--since-last`, `--watch`, `--forget`, `--sort`, `--query`.

---

## Output (text)

One line per chain; indented lines only for members that individually need something. Status tokens are the **same vocabulary as `ger log`** — patchset column omitted, since a chain has no local commit to align with.

```text
to review (3)
c4321  5c  v+1 cr0       unrevi 3d  act 4h  alice   # feat: rate limiter
  https://gerrit.example.com/c/myproject/+/4321
c4400  1c  v+1 cr+1 com  unrevi 1d  act 1d  bob     # fix: retry backoff
  https://gerrit.example.com/c/myproject/+/4400
c4380  9c  v-1 cr0       unrevi 6d  act 6d  carol   # refactor: split scheduler
  https://gerrit.example.com/c/myproject/+/4380
   └ c4376  v-1      # build failed
   └ c4379  cr0 com  # 2 unresolved comments

summary: 3 chains · 15 changes · oldest unrevi 6d · CI 1 · comments 1
```

| Column | Meaning |
|--------|---------|
| `c<n>` | Chain top's Gerrit change number |
| `<n>c` | Chain depth (member count) |
| `v…` / `cr…` / `com` | Worst label across members; `com` when any member has unresolved comments. Tokens: [Reading-ger-log.md](../../Reading-ger-log.md#status-columns) |
| `unrevi` | Unreviewed age — how long the chain has waited for you |
| `act` | Wait age — last activity on any member |
| owner | Chain-top owner |
| `# subject` | Chain top's subject, highlighted per `gerrit.warningPattern` |
| URL | Gerrit page for the **chain top** (default on) |

**Indented member lines** appear for members with attention reasons — CI failure, unresolved comments, negative vote. A healthy chain stays one line plus its URL.

**Empty sections** print a dim `(nothing to review)` rather than vanishing, so the output shape is stable.

`-v` lists every member, not only those with attention.

---

## JSON

`--json` is the contract for automation. It is the API; the text output is a rendering of it.

```jsonc
{
  "host": "gerrit.example.com",
  "generated": "2026-08-18T09:14:22Z",
  "sections": [
    {
      "name": "to-review",
      "query": "is:open -is:wip -is:private -owner:self reviewer:self label:Verified+1",
      "chains": [
        {
          "key": "gerrit.example.com~myproject~4321",
          "top": { "number": 4321, "change_id": "I…", "subject": "feat: rate limiter",
                   "url": "https://gerrit.example.com/c/myproject/+/4321" },
          "project": "myproject",
          "branch": "main",
          "owner": { "name": "alice", "email": "alice@example.com" },
          "depth": 5,
          "wait_age_seconds": 14400,
          "unreviewed_age_seconds": 259200,
          "last_activity": "2026-08-18T08:00:00Z",
          "verified": -1, "code_review": 0,
          "comments_unresolved": 2,
          "attention_reasons": ["ci-failed", "unresolved-comments"],
          "partial_chain": false,
          "members": [
            { "number": 4317, "subject": "feat: config plumbing", "verified": 1,
              "code_review": 2, "comments_unresolved": 0, "attention_reasons": [] }
          ]
        }
      ]
    }
  ],
  "summary": { "chains": 3, "changes": 15, "oldest_unreviewed_seconds": 518400,
               "oldest_wait_seconds": 518400, "ci_failures": 1, "comments": 1 }
}
```

`attention_reasons` reuses the vocabulary from `ger log` ([Reading-ger-log.md](../../Reading-ger-log.md#attention-hints-trailing--)) so one consumer handles both. Inbox members only emit `ci-failed`, `unresolved-comments`, and `review-issues` — there is no local patchset to align.

---

## Exit codes

Shared table ([exit-codes.md](../exit-codes.md)) — no new codes.

| Code | Meaning |
|------|---------|
| `0` | Ran fine; nothing wants you (empty inbox) |
| `1` | `ATTENTION` — the inbox is non-empty |
| `2` | `USAGE` — bad arguments |
| `5` | `GERRIT` — query failed, auth, unreachable |
| `6` | `CONFIG` — `gerrit.webUrl` or credentials missing |

Consistent with `ger log` / `ger show`: exit `1` reports a *state*, not a failure.

---

## Configuration

| Key | Effect | Default |
|-----|--------|---------|
| `inbox.requireVerified` | Require `label:Verified+1` for a chain to count as ready | `true` |
| `inbox.verifiedLabel` | Label name for the CI gate (sites rename it) | `Verified` |
| `inbox.projects` | Default `--project` list, comma-separated | *(all)* |
| `inbox.toReviewQuery` | Override the *to review* query entirely | *(see table)* |
| `inbox.limit` | Default `--limit` | *(none)* |
| `gerrit.warningPattern` | Reused from `ger log` | |

Site variation in label names and workflow is real, which is why the query is overridable wholesale as well as tunable in parts.

---

## Architecture notes

Settled in [adr/0004-inbox-is-host-scoped.md](../../adr/0004-inbox-is-host-scoped.md): **the inbox is scoped to a Gerrit host, not a working directory.** Nothing in `ger inbox` may build a **stack context**.

Query-driven fetch is a sibling of `GerritService.fetch_gerrit_data`: `GerritService.fetch_review_chains(query)`. Chain assembly is a pure function over ChangeInfo payloads (`core/review_chain.py`), testable against `ChangeStore`.

New binding vocabulary lives in [CONTEXT.md](../../../CONTEXT.md): **review chain**, **chain top**, **unreviewed age**, **wait age**, **inbox section**.

---

## Open questions

- **`-q` / quiet.** A cron notifier would want a quiet flag. Either add `-q` here or promote it to `cli_common` for all commands. Deferred with `--since-last`.
- **Chain key stability.** Keying on the chain top's change number is readable, but the top changes when someone adds a commit on top. Keying on the chain *base* is more stable; decide before the watermark format is written.
- **Relation to `ger review checkout`.** The intended follow-up is materializing a chain into a dedicated review clone. `ger inbox --json` already carries project, branch, top change number. Confirm no extra field is required before freezing the schema.
- **Waiting on others.** Second section, same assembly.

---

## See also

- [`ger log`](log.md) — the same status vocabulary, applied to your local stack
- [`ger show`](show.md) — one change or chain in full, with comments
- [Reading-ger-log.md](../../Reading-ger-log.md) — status tokens and attention hints
- [exit-codes.md](../exit-codes.md) — shared exit-code contract
- [architecture.md](../../architecture.md) — layer model and module map
