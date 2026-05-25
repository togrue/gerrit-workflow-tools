# Gerrit CLI Tools — Version 1 Scope

**Target:** Team adoption (small group of colleagues sharing the same Gerrit workflow)
**Goal:** A safe, low-friction daily driver for stacked Gerrit review — solid enough to recommend to a teammate on day 1.

---

## Core Loop

The three commands every teammate reaches for first. Must be fully polished.

### `ger log`
Stack overview against Gerrit (CI votes, code-review, unresolved comments).

**In scope:**
- `--unresolved-comments` — show full text of unresolved comment chains inline
- `-v` — verbose mode: print Gerrit URLs for commits with non-clean status (CI failures, negative votes, open comments)

**Out of scope (deferred):**
- `-<number>` limiter — not needed in practice

---

### `ger push`
Push the ready prefix of the stack to Gerrit.

**In scope:**
- Interactive confirmation is the default (already implemented); `-y` bypasses it
- `--review` — shortcut flag that jumps directly to the reviewer assignment step, skipping earlier confirmation steps
- Better confirmation output: prominently show target branch, reviewers, topic, and WIP status before confirming

**Out of scope (deferred):**
- Submodule safety checks — team uses submodules, but the correct integration is via hooks (team-specific rules around submodule diffs in commit messages and remote push ordering). Needs further investigation before implementing.

---

### `ger show`
Single commit detail: commit message, Gerrit status, unresolved comments.

**In scope — bugs and UX fixes:**
- Always show the git commit message (currently missing when called with a Change-Id)
- Fix resolved chain detection: a comment chain is resolved when its **last** comment is marked resolved (not just any comment in the chain)
- Show `(no unresolved comments)` explicitly when the change is clean
- Reformat comment chains:
  - URL at the top of the chain (not per-comment)
  - Author + relative timestamp per comment
  - File location or `PATCHSET_LEVEL` prefix

**Out of scope (deferred):**
- `--stat` — file change statistics
- `-p` — full diff output

---

## Supporting Commands

Already implemented. Include in version 1 with the changes noted.

### `ger edit` / `ger reword`
Interactive rebase: edit, reword, or drop a specific commit by SHA or Change-Id. No changes required.

### `ger fix`
Create a fixup commit targeting a specific Change-Id or SHA in the stack.

**Verify before release:**
- Safety check: fail with a clear error if the target change is already merged on Gerrit
- Safety check: fail (or warn with `--force`) if the fixup would produce a merge conflict

### `ger assign` *(new command)*
Assign or update Gerrit metadata on existing changes without re-pushing. Absorbs the planned `ger move` command.

**Scope:**
- Set reviewers on one or more changes
- Set topic
- Set WIP / private status
- Move changes to a different target branch (the `ger move` use case: recovery from pushing to the wrong branch)

Target: SHA range, Change-Id, or current stack

### `ger branch`
Configure branch-local Gerrit target and default reviewers. No changes required.

### `ger rebase`
Interactive rebase with Gerrit status annotations on each commit. No changes required.

### `ger sha` / `ger change-id`
Plumbing: resolve Change-Id ↔ SHA, validate and check for duplicates. No changes required.
Patchset-resolution support in `ger sha` is explicitly deferred.

### `ger fetch-api` / `ger cache`
Developer/debug utilities. No changes required for version 1.

---

## Onboarding Story

Version 1 must include a clear setup path for a new teammate.

- **Bash completion:** `ger bash-completion install` — documented as a recommended step in the setup README
- **Commit-msg hook:** Manual setup documented in the README until `ger hooks` is available (version 1.1)
- **Configuration reference:** `docu/Configuration.md` must be accurate and linked from the setup README
- **Development specification:** `docu/SPEC.md` (per-command specs under `docu/spec/commands/`) is the single source of truth for implemented behavior; keep aligned with code when shipping v1 items

---

## Explicitly Deferred (Version 1.1)

| Feature                        | Reason                                                                        |
| ------------------------------ | ----------------------------------------------------------------------------- |
| `ger hooks install`            | Needs submodule hook story resolved first; manual setup is acceptable for now |
| `ger checkout`                 | Not part of the daily push/review loop                                        |
| Submodule safety in `ger push` | Team-specific rules best implemented as hooks; needs design investigation     |
| `ger show --stat` / `-p`       | `ger edit` and the regular diff tooling cover this                            |
| `ger sha` patchset resolution  | Edge case; not blocking daily workflow                                        |
| Short refs in `ger log`        | Interesting idea, not validated in daily use                                  |
