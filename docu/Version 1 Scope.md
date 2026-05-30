# Gerrit CLI Tools — Version 1 Scope

**Target:** Team adoption (small group of colleagues sharing the same Gerrit workflow)
**Goal:** A safe, low-friction daily driver for stacked Gerrit review — solid enough to recommend to a teammate on day 1.

**Shipped behavior** lives in [SPEC.md](SPEC.md) and [spec/commands/](spec/commands/). This doc tracks **remaining v1 polish** and **explicit deferrals** only.

**Actionable work list (simplest first, spec gaps called out):** [v1-todos.md](v1-todos.md)

---

## Core loop — remaining work

### `ger log`

Stack overview against Gerrit (CI votes, code-review, unresolved comments). Attention labels and `-v` verbose layout are shipped; see [spec/commands/log.md](spec/commands/log.md).

**Still in scope:**

- `--unresolved-comments` — inline full text of unresolved comment chains
- `-v` refinement — print Gerrit URLs (and extra detail) only for commits with non-clean status (CI failures, negative votes, open comments), not for every row when verbose is on

### `ger push`

Push the ready prefix of the stack to Gerrit. Interactive confirmation, `-y`, and the confirmation summary line (target, reviewers, topic, WIP/private) are shipped.

**Still in scope:**

- `--review` — shortcut to the reviewer-assignment step, skipping earlier confirmation steps

### `ger show`

Single commit detail: commit message, Gerrit status, unresolved comments.

**Still in scope:**

- Always show the git commit message when called with a Change-Id (no local commit)
- Chain resolution: unresolved only when the **last** comment in the chain is unresolved (not any comment in the chain)
- Show `(no unresolved comments)` when the change is clean
- Reformat comment chains: URL once at the top; author + relative timestamp per comment; `PATCHSET_LEVEL` or `file:line` prefix

---

## Other v1 commands

### `ger assign` *(new)*

Assign or update Gerrit metadata on existing changes without re-pushing. Absorbs the planned standalone `ger move` command.

- Set reviewers, topic, WIP/private
- Move changes to a different target branch (wrong-branch recovery)

Targets: SHA range, Change-Id, or current stack. Spec: [spec/commands/assign.md](spec/commands/assign.md).

### `ger fix`

**Verify before release:**

- Fail with a clear error if the target change is already merged on Gerrit
- Fail (or warn with `--force`) if the fixup would produce a merge conflict

---

## Onboarding (remaining)

- **Commit-msg hook:** Manual setup documented in [README.md](../README.md#first-time-setup-change-id-hook) until `ger hooks` ships (v1.1)
- **Configuration reference:** Keep [Configuration.md](Configuration.md) accurate and linked from setup docs
- **Specs vs code:** Update [SPEC.md](SPEC.md) and per-command specs when closing items above

---

## Explicitly deferred (v1.1+)

| Feature | Reason |
| -------- | ------ |
| `ger hooks install` | Submodule hook story first; manual hook setup is acceptable for v1 |
| `ger checkout` | Not part of the daily push/review loop |
| Submodule safety in `ger push` | Team-specific rules fit hooks; needs design ([hooks_implementation.md](hooks_implementation.md)) |
| `ger show --stat` / `-p` | `ger edit` and normal diff tooling cover this |
| `ger sha` patchset resolution | Edge case; not blocking daily workflow |
| `ger log -<n>` limiter | Not needed in practice |
| Short refs in `ger log` | Interesting idea; not validated in daily use |
