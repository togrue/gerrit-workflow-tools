# Inbox is scoped to a Gerrit host, not a working directory

Every existing `ger` command takes a `cwd` and reads project identity from the clone's remote — the **stack context** `(project, target branch, push branch)`. `ger inbox` answers a different question: *what on this Gerrit host is waiting on me?* That set is not "the local stack viewed from the other end". It is usually someone else's chain, and the command must run from any directory, including outside a clone.

## Considered options

Folding inbox into `ger log --inbox` (or enriching the local stack with "also fetch my review queue") is the obvious-looking alternative. It was rejected because the two commands start from opposite ends:

- `ger log` starts from local commits and overlays Gerrit. No local stack, nothing to print.
- `ger inbox` starts from a Gerrit query and never looks at local commits. Building a stack context would fail outside a clone, and would silently restrict the queue to one project/branch when the user asked for the host.

Reusing `GerritService.fetch_gerrit_data(commits)` as a "query variant" was also rejected: that method resolves stack context and keys the cache by triplet. Inbox must not call `resolve_stack_context`. The sibling is `GerritService.fetch_review_chains(query)`.

## Consequences

- Credentials and `gerrit.webUrl` still come from git config (`Settings.from_cwd` is directory-aware and reads global config outside a repo). That is host identity, not stack context.
- Chain assembly is a pure function over ChangeInfo payloads (`core/review_chain.py`). Tests drive it through `ChangeStore` with no clone.
- Host-scoped is about *where it may run*, not about *what it shows*. Run inside a clone, the
  inbox is narrowed to that clone's Gerrit project (`core/inbox.py:project_scope`), because
  chains of other projects are noise next to the code you have open. The project comes from
  `gerrit.project` or the remote URL — still not a stack context: no upstream, branch or triplet.
  `ger inbox --all` (and `GET /overview?all=1`) show the whole host; outside a clone the inbox
  is the host, or `inbox.projects`.
- `ger cache --clear` remains unrelated to any future inbox watermark: the watermark is *state* (what you have already been notified about), not a discardable replica of Gerrit.
