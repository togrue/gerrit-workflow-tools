# What a run reads once lives on the run, never on the process

A command asks the same few questions of git and of its configuration many times: which
branch is checked out, which is the **working branch**, where the repository's top level is,
what the **stack context** is. Answering each from a subprocess every time was slow, so ger
grew process-wide memos: `lru_cache` over `git rev-parse` and `git log`, a `Worktree` per
directory, a stack context per directory and settings. Each memo then needed a rule for when
it went stale, and every rule was a guess from the outside: clear on any git subcommand not
known to be read-only, clear the worktree on `checkout`, `switch`, `rebase`, `reset`,
`branch -m`…, clear the stack context on `git config` and `branch -u`. `ger serve` cleared
them all per request, and tests cleared them around every test.

## Decision

Nothing below the entry point remembers anything for the process. Each command run and each
request `ger serve` answers opens one **run context** (`RunContext.open`): its **Settings**
snapshot, its `Repository` (worktree, working branch, stack context, each read on first use and
kept for the run) and its Gerrit service. Core takes the run, or the answer it needs (a branch,
a repository root), and passes it down. `git()` runs git every time it is called.

The run keeps what it read even after it changes the repository itself. Work that switches
branches and then needs the new one reads `Worktree.from_cwd` again, at the place that knows it
switched; nothing tries to notice from the outside.

A process-wide memo stays allowed only when its key names something that cannot change, so
there is nothing to invalidate: the tracked paths of a commit by its object id
(`core/ci_paths.py`), cached Gerrit data under its own freshness rules (ADR-0001, ADR-0007). A hint that every use checks is
allowed too: the address a host last answered on (`core/http_connect.py`) is tried first, and
when it does not connect the addresses are raced anew.

## Considered options

**Keep the memos, invalidate better.** Rejected: invalidation from the git subcommand cannot be
right. Config can change from another process, hooks run git themselves, and a long-lived
`ger serve` sees edits made in a terminal beside it. Every rule we wrote was either too broad
(clearing everything on `git show-ref`, so the memo rarely held) or missed a case.

**A memo keyed by HEAD's sha or the index mtime.** Rejected: it costs a git call or a stat to
check, and the keys still miss config edits; the question "is it still valid" stays a guess.

**Pass the run everywhere at once.** Not needed: readers that take a `cwd` still work and read
anew; the hot paths (`ger log`, `ger show`, `ger fix`, the server's stack view) take the run's
answers, and `tests/test_call_budgets.py` holds them to their git-call budgets.

## Consequences

- A run is internally consistent: every reader in it sees the same branch and stack context.
- A second run (the next command, the next request) sees whatever changed, without anyone
  clearing anything. `ger serve` needs no cache clearing; tests need no autouse fixture.
- A function that only has a `cwd` pays one `git rev-parse` per call. Where that shows in a
  budget, the fix is to hand it the run's answer, not to add a memo.
- `ger serve` still serialises requests, now only because a request may move the working tree
  under another.
