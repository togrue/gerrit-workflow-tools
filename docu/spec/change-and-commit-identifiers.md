# Expected behavior: identifying changes & commits

|  |  |
|--|--|
| **Status** | **Implemented** (Phases 0–6; see [plans/gerrit-native-change-resolution.md](../plans/gerrit-native-change-resolution.md)) |
| **Audience** | Users, contributors, and AI agents driving `ger` |
| **Supersedes** | The "ambiguity → hard error, no heuristics" stance in [plans/gerrit-native-change-resolution.md](../plans/gerrit-native-change-resolution.md) — see [Relationship to the resolution plan](#relationship-to-the-resolution-plan) |

This document describes how `ger` **should** behave when the user (or an agent)
points a command at "a change" or "a commit". It is written from the user's
point of view first; implementation notes are secondary. Remaining gaps (e.g.
[`ger assign`](commands/assign.md)) are called out explicitly.

The guiding goals:

1. **One input model.** Anywhere a command takes a target, the user can supply
   *anything that unambiguously identifies a change or a commit* — a git ref, a
   SHA, a Change-Id, a Gerrit change number, a triplet, or a Gerrit URL.
2. **Robust against duplicate Change-Ids.** The same `Change-Id` can legitimately
   exist on multiple branches. This must never break a command or cause it to act
   on the wrong change silently.
3. **Smart, but transparent.** When a bare Change-Id is ambiguous, `ger` guesses
   the change on your configured target branch — and *says so*, with the escape
   hatch to override.
4. **Automatable.** Every resolution is deterministic, inspectable, and available
   as machine-readable JSON, so an AI agent can safely automate stack reworks.

---

## 1. The core concept: a *changeish*

Borrowing git's "commit-ish" idea, `ger` accepts a **changeish**: a single string
that identifies either a local commit, a Gerrit change, or both. Every command
that today says "`REV`", "`REF_OR_CHANGE`", or "Change-Id" accepts the same
grammar.

A changeish resolves to some or all of:

- a **local commit** (SHA in the current repository), and/or
- a **Gerrit change** (a specific `project~branch~Change-Id`, i.e. a concrete
  `ChangeInfo` row on the server).

Not every input yields both. `HEAD~2` is a local commit that may or may not be on
Gerrit; `change:120045` is a Gerrit change that may or may not be checked out
locally. Commands use whichever side they need and report clearly when the other
side is missing (e.g. `ger show change:120045` prints the server view even with
no local commit, exactly as it does today for a Change-Id-only argument).

### 1.1 Identifier kinds

| Kind | Example | Prefix needed? | Identifies |
|------|---------|----------------|------------|
| **Git revision** | `HEAD`, `HEAD~2`, `a1b2c3d`, `feature/x`, `origin/main` | no (default) | local commit |
| **Change-Id** | `I8f3c…` (`I` + 40 hex) | no (self-identifying) | Gerrit change(s) by footer id |
| **Triplet** | `myproject~main~I8f3c…` | no (contains `~`) | exactly one Gerrit change |
| **Change number** | `change:120045` | **yes** — `change:` | exactly one Gerrit change |
| **Change ref** | `refs/changes/45/120045/3` | no (namespaced) | one Gerrit patch set |
| **Gerrit URL** | `https://gerrit.example.com/c/myproject/+/120045` | no (has scheme) | one Gerrit change |
| **Raw query** | `q:topic:my-feature status:open` | **yes** — `q:` | Gerrit change(s) via search |

The prefixes exist for exactly one reason: **to stop a Gerrit change number from
being confused with an abbreviated git SHA.** `120045` is a perfectly valid short
SHA *and* a plausible change number. Rather than guess, `ger` treats a bare token
as a **git revision first** and requires an explicit prefix to mean "this is a
Gerrit change number / query". See [§2.2](#22-disambiguation-rules).

### 1.2 Prefix scheme (recommended)

| Prefix | Meaning | Notes |
|--------|---------|-------|
| `change:<n>` | Gerrit change number | Canonical. Mirrors Gerrit's own query language, so it reads naturally and is familiar to AI models trained on Gerrit docs. |
| `cl:<n>` | alias for `change:<n>` | Short form for interactive use ("changelist"). Optional. |
| `q:<query>` | Raw Gerrit search | Power/agent use. Must resolve to a single change unless the command is inherently multi-result. |
| `rev:<gitrev>` / `git:<gitrev>` | Force git interpretation | Escape hatch for the rare case where a ref name collides with a prefix or looks like a Change-Id. |

Triplets, Change-Ids, `refs/changes/…`, and URLs are **self-identifying** and need
no prefix — their shape is unambiguous.

> **Design note — the one open decision.** The `:` sigil (`change:`, `q:`) is
> chosen because it is shell-safe (no quoting needed in bash/zsh/PowerShell),
> matches Gerrit's query grammar, and is unambiguous against git revs (git refs
> can't contain `:` in a bare token here). Alternatives considered: `#120045`
> (needs shell quoting), `!120045` (bash history expansion), `@120045` (reads as
> a git reflog-ish token). If a different sigil is preferred, only this table and
> the parser change; the rest of the behavior is unaffected.

---

## 2. Resolution algorithm

Given a changeish string `S` and the current repo/branch context, `ger` resolves
it as follows.

### 2.1 Classify

1. Has an explicit prefix (`change:`, `cl:`, `q:`, `rev:`, `git:`) → that kind, no
   guessing.
2. Starts with a URL scheme (`http://`, `https://`) or is a `refs/changes/…` ref →
   parse out the change number / patch set.
3. Contains `~` → **triplet**.
4. Matches `^[iI][0-9a-fA-F]{40}$` → **Change-Id**.
5. Otherwise → **git revision** (default). Resolved with `git rev-parse` semantics.

### 2.2 Disambiguation rules

- A **bare integer** (`120045`) is a **git revision**, never a change number. To
  mean the change, write `change:120045`.
- A **triplet**, **change number**, **change ref**, and **URL** each identify
  **exactly one** Gerrit change. No ambiguity is possible; if the change does not
  exist, that is a not-found error, not an ambiguity error.
- A **bare Change-Id** may match **zero, one, or many** Gerrit changes. This is the
  hard case, handled in [§3](#3-duplicate-change-ids-the-important-case).

### 2.3 The stack context

Several rules below need to know *which Gerrit change you most likely mean*. That
comes from the **stack context** of the current (or `--branch`) branch:

| Field | Source (in precedence order) |
|-------|------------------------------|
| `project` | `gerrit.project` → parsed from the `gerrit.remote` URL |
| `target branch` | `branch.<name>.gerritTarget` → upstream ref when its remote is `gerrit.remote` |
| `push branch` | the branch Gerrit would receive `refs/for/<target>` |

Resolved by `resolve_stack_context()` in `core/gerrit/change_resolution.py`
(including `branch.<name>.gerritTarget` when set). See
[Configuration.md](../Configuration.md#change-identity-triplet-resolution).

When the stack context cannot be resolved (no project, or no target branch), a
command that *needs* it fails with the same actionable error as `ger push`
("set upstream …" / "configure `gerrit.project`") — never a silent wrong pick.

---

## 3. Duplicate Change-Ids: the important case

**Problem:** the same `Change-Id: I…` footer can appear on changes targeting
different branches (e.g. a change cherry-picked from `main` to a release branch).
A bare `Change-Id` therefore is **not** a unique Gerrit key. The shared resolver
in `core/gerrit/change_resolution.py` selects among matches using stack context.
Stack overlay **batch-fetches** by Change-Id (optionally project-scoped) and keys
the disk cache by triplet / `ChangeInfo.id`, never collapsing multiple matches
silently.

### 3.1 Expected behavior

When a bare Change-Id (typed directly, or read from a local commit's footer)
matches **more than one** open Gerrit change:

1. **Prefer the target branch.** Filter matches to the stack context's target
   branch ([§2.3](#23-the-stack-context)). If **exactly one** remains, select it.
2. **Announce the guess.** Print a one-line, dimmed transparency note to stderr
   (and include it structured in `--json`), e.g.:

   > `note: Change-Id I8f3c… matches 2 changes (branches: main #120045, release-2.1 #119870); using #120045 on 'main' (your push target). Override with a triplet or change: number.`

3. **Still ambiguous?** If more than one match remains *on the target branch*
   (rare — e.g. an abandoned change plus a new one), prefer **open/active** over
   `ABANDONED`/`MERGED`. If that still leaves more than one, **stop and list the
   candidates** (each with its number and triplet) so the next invocation can be
   exact. This is a resolution error (exit code for ambiguity), not a silent pick.
4. **None on the target branch?** If the Change-Id matches only changes on *other*
   branches:
   - **Read-only overlay** commands (`ger log`, `ger show HEAD`) treat the local
     commit as **`absent`** on the target branch (Gerrit-correct: it hasn't been
     pushed to `refs/for/<target>` yet) and note that the Change-Id exists
     elsewhere.
   - **Explicitly targeted** commands (`ger show I…`) report the change(s) found on
     other branches transparently, and if there is exactly one, use it while noting
     the branch mismatch. If several, list them.

### 3.2 Principles

- **Never overwrite silently.** Two changes with the same Change-Id are two rows,
  cached and displayed independently. The disk cache keys on the **triplet /
  `ChangeInfo.id`**, never on the bare Change-Id.
- **The guess is always visible.** Any time `ger` narrows an ambiguous input, it
  tells you what it picked and why, and how to override. Silence is only allowed
  when the input was already unique.
- **The override is always available.** A triplet or `change:` number bypasses all
  heuristics.

---

## 4. Per-command expectations

All commands share the changeish grammar. Command-specific notes:

| Command | Target arg today | Expected changeish behavior |
|---------|------------------|-----------------------------|
| [`ger log`](commands/log.md) | `REV_RANGE` | Enrich each local commit against the change **on the target branch** (triplet-resolved). A duplicate Change-Id on another branch is ignored for status; may surface as a note. |
| [`ger show`](commands/show.md) | `REV` | Full changeish. Resolves local commit + one Gerrit change; applies §3 when the arg is a bare Change-Id. `--json` includes the `resolution` block ([§5](#5-machine-readable-resolution-for-automation)). |
| [`ger push`](commands/push.md) | `REV` (until-boundary) | Pushes to `refs/for/<target>`; the target branch is authoritative, so pushed changes are inherently target-scoped. Duplicate-Change-Id warnings from `--check-duplicates` remain **local-git** checks. |
| [`ger fix`](commands/fix.md) | `REF_OR_CHANGE` | Full changeish → resolve to a single commit SHA (fetch `refs/changes/…` when the change isn't local). Ambiguous bare Change-Id follows §3. |
| [`ger edit`](commands/edit.md) / `reword` | `REV` in stack | Changeish restricted to the local stack; resolves to one stack commit. Change numbers/triplets map to the stack commit sharing that Change-Id, else error. |
| [`ger rebase`](commands/rebase.md) | `REV` base | Changeish for the base commit; enrichment uses target-branch resolution. |
| [`ger assign`](commands/assign.md) *(planned)* | `<targets>` | Accepts one or many changeishes (or the implicit stack). Mutations act on **resolved triplets**, so each target is unambiguous before any REST write. |
| [`ger sha`](commands/sha-change-id.md) | `<change-id>` | Stays **local-git only**: Change-Id → local SHA. A duplicate **in local history** now exits `8` (`DUPLICATE_CHANGE_ID`), distinct from Gerrit-side ambiguity (`4`). |

### 4.1 Helper: `ger resolve`

A small, **side-effect-free** command that reports what a changeish resolves to.
Implemented as [`ger resolve`](commands/resolve.md) — same core as every other
resolving command.

```
ger resolve <changeish> [--json]
```

Text output: the resolved local SHA (if any), the selected Gerrit change
(number + triplet + branch + status), and any ambiguity note. Exit codes match
[§6](#6-exit-codes). With `--json`, prints the `resolution` block below and
nothing else.

---

## 5. Machine-readable resolution (for automation)

Every command that resolves a changeish exposes, under `--json`, a `resolution`
object so an agent never has to scrape human text:

```json
{
  "resolution": {
    "input": "I8f3c…",
    "kind": "change-id",
    "selected": {
      "number": 120045,
      "triplet": "myproject~main~I8f3c…",
      "branch": "main",
      "change_id": "I8f3c…",
      "status": "NEW"
    },
    "selected_reason": "target-branch",
    "ambiguous": true,
    "alternatives": [
      { "number": 119870, "triplet": "myproject~release-2.1~I8f3c…", "branch": "release-2.1", "status": "NEW" }
    ],
    "local_sha": "a1b2c3d4…"
  }
}
```

- `kind` — one of `git-rev`, `change-id`, `triplet`, `change-number`,
  `change-ref`, `url`, `query`.
- `selected_reason` — `unique`, `target-branch`, `prefer-open`, or `explicit`.
- `ambiguous` — `true` whenever more than one Gerrit change matched the input,
  even if one was ultimately selected. Agents can gate on this.
- `alternatives` — every other match, fully qualified, so the agent can re-issue
  an exact request.
- `local_sha` — present when the changeish maps to a commit in the working repo.

### 5.1 Automation contract

- **Deterministic:** identical repo state + config + server state ⇒ identical
  resolution. No time-based or ordering-dependent picks.
- **Never prompts under automation:** with `--json`, on a non-TTY, or with
  `--yes`, `ger` never blocks on interactive input; ambiguity becomes a non-zero
  exit plus structured `alternatives`.
- **Exact round-trips:** any `triplet` or `number` printed in `alternatives` is a
  valid changeish that resolves back to exactly that change.
- **Stable exit codes** distinguish *not found* from *ambiguous* from *API error*
  ([§6](#6-exit-codes)), so an agent can branch on the failure mode.

This is what lets an agent automate a rework loop safely: `ger log --json` to see
the stack → for each commit needing work, `ger resolve --json` (or read the
`resolution` block) → act on the exact triplet → if `ambiguous` and no confident
pick, escalate instead of guessing.

---

## 6. Exit codes (resolution-related)

A consistent family across commands, so tooling can rely on them:

Exit codes are shared across every command: see **[exit-codes.md](exit-codes.md)**. The ones resolution produces:

| Code | Meaning |
|------|---------|
| `0` | Resolved to exactly one target (no attention required, where applicable) |
| `1` | Resolved, but attention/no-op condition |
| `2` | Usage error — malformed changeish, unknown prefix |
| `3` | Not found — the changeish resolved to nothing |
| `4` | **Ambiguous** — multiple matches survived narrowing; `alternatives` lists them |
| `5` | Gerrit API error (unreachable, auth, bad response) |
| `6` | Required git configuration missing (`gerrit.webUrl`, credentials) |
| `7` | A git command failed |

`3` no longer covers Gerrit and git failures; those are `5` and `7`.

`ger show`, `ger fix`, and `ger resolve` use exit code `4` for ambiguity.
`ger sha` uses `3` for duplicate Change-Ids in **local** history (unrelated to
Gerrit branch ambiguity).

---

## 7. Relationship to the resolution plan

[plans/gerrit-native-change-resolution.md](../plans/gerrit-native-change-resolution.md)
lays out the *implementation* path to triplet-native identity (REST layer,
service layer, cache v2). This document is the **behavior contract** those phases
should satisfy, with two deliberate refinements:

| Plan said | This spec refines to |
|-----------|----------------------|
| "Ambiguity → error, not silent pick." | Ambiguity → **prefer the target-branch change, transparently**, and only error when still ambiguous after narrowing. The plan's core insight (never *silently* collapse) is preserved; the UX is friendlier. |
| "No SHA/heuristic disambiguation — out of scope." | Still no *SHA* heuristics. But **branch-aware** narrowing (target branch, prefer-open) is in scope and is the primary usability win. |
| Bare Change-Id is a search term only. | Unchanged — but the search is scoped by stack context and its result is reported, not hidden. |

Everything else in the plan (triplet as the canonical key, cache keyed on
`ChangeInfo.id`, `branch.<name>.gerritTarget` finally read in code) is a
prerequisite for the behavior described here.

---

## 8. Remaining gaps

| Area | Status |
|------|--------|
| [`ger assign`](commands/assign.md) | Planned — not in `cli_ger.py` yet |
| Full `resolution` JSON block on every resolving command | Implemented on `ger show`, `ger fix`, and `ger resolve`; `ger log` emits `stack` + per-commit `resolution_note`; `ger push` / `ger edit` do not emit the §5 block |

---

## See also

- [plans/gerrit-native-change-resolution.md](../plans/gerrit-native-change-resolution.md) — implementation phases
- [architecture.md](../architecture.md) — stack, target branch, patchset status, Change-Id
- [spec/commands/](commands/) — per-command detail
- [Configuration.md](../Configuration.md) — `gerrit.project`, `branch.*.gerritTarget`, `gerrit.remote`
