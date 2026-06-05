# Gerrit CLI Tools — Version 1 Scope

**Target:** Team adoption (small group of colleagues sharing the same Gerrit workflow)
**Goal:** A safe, low-friction daily driver for stacked Gerrit review — solid enough to recommend to a teammate on day 1.

**Shipped behavior** lives in [SPEC.md](SPEC.md) and [spec/commands/](spec/commands/). This doc tracks **remaining v1 polish** and **explicit deferrals** only.

**Actionable work list (simplest first, spec gaps called out):** [v1-todos.md](v1-todos.md)

---

## Core loop — remaining work

### `ger log`

See [spec/commands/log.md](spec/commands/log.md).

- `--unresolved-comments` — inline full text of unresolved comment chains
- `-v` refinement — print Gerrit URLs (and extra detail) only for commits with non-clean status (CI failures, negative votes, open comments), not for every row when verbose is on

### `ger push`

See [spec/commands/push.md](spec/commands/push.md).

- `--review` — shortcut to the reviewer-assignment step, skipping earlier confirmation steps

### `ger show`

See [spec/commands/show.md](spec/commands/show.md).

- Always show the git commit message when called with a Change-Id (no local commit)
- Comment chain formatting polish: relative timestamp per comment; `PATCHSET_LEVEL` or `file:line` prefix on each comment line

---

## Other v1 commands

### `ger assign` *(new)*

Assign or update Gerrit metadata on existing changes without re-pushing. Absorbs the planned standalone `ger move` command.

- Set reviewers, topic, WIP/private
- Move changes to a different target branch (wrong-branch recovery)

Targets: SHA range, Change-Id, or current stack. Spec: [spec/commands/assign.md](spec/commands/assign.md).

### `ger fix`

- Fail with a clear error if the target change is already merged on Gerrit
- Fail (or warn with `--force`) if the fixup would produce a merge conflict

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
