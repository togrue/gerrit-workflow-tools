# `ger log`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_log.py` |
| **Requires** | `gerrit.webUrl`, credentials |

Compact overview of the local commit chain vs Gerrit (CI, votes, unresolved comment counts, patchset alignment). Answers: *what needs attention next?*

---

## Usage

```
ger log [options] [REV_RANGE]
```

`REV_RANGE` — optional (e.g. `origin/main..HEAD`). Default: `branch@{upstream}..branch` when upstream exists; otherwise error with hint to set upstream (`git branch --set-upstream-to=…`). On a TTY, `ger log` may prompt via `ensure_branch_upstream_interactive`.

### Change resolution

`ger log` enriches each local commit against the Gerrit change on the **target branch** (stack context). Changeishes in `REV_RANGE` endpoints use the same shared grammar as other commands; resolution goes through **`core/gerrit/change_resolution.py`**. When a footer Change-Id matches multiple server changes, the resolver narrows to the target branch and reports the pick transparently; a duplicate on another branch is ignored for status (may surface as a note on stderr). Full rules: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md). With `--json`, output includes a top-level `stack` object (project, target branch, push branch) and per-commit `resolution_note` when narrowing applied ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)).

---

## Options

| Option | Description |
|--------|-------------|
| `--json` | Machine-readable JSON (one object per commit) |
| `--color WHEN` | `always` \| `auto` \| `never` |
| `--hyperlinks WHEN` | `always` \| `auto` \| `never`. When on, text URLs become a clickable `Open in gerrit` (OSC 8) and that compact link is shown by default. JSON always includes the raw `gerrit_url`. |
| `--url`, `--show-url` | Gerrit web URL per line (forced on). Default: on when hyperlinks are on, otherwise `gerrit.logShowUrl`. |
| `--show-change-id` | Append Change-Id on text lines (default: `gerrit.logShowChangeId`) |
| `-v`, `--verbose` | Expanded layout: indented detail lines; URLs on following line when URLs enabled |
| `--debug-log` | Log git commands to stderr (repeat for more detail) |
| `--follow-merges` | Include merge commits in range (see shared helper in `cli_common`) |

---

## Output (text)

Default: one primary line per commit, optional `# …` detail lines, trailing **summary** line.

**User guide (columns, tokens, examples):** [Reading-ger-log.md](../../Reading-ger-log.md). With hyperlinks on, each line gets a clickable `Open in gerrit` instead of the raw address. `--url` forces the URL column even when hyperlinks are off.

Columns: patchset token (`p`/`n`/`o`/`-`), Verified, Code-Review, comment marker, attention hints, subject. Patchset tokens: [architecture.md](../../architecture.md#patchset-status-log--show--rebase-annotations).

**Summary example:** `summary: ready 2/6 · CI 1 · comments 1 · on-gerrit 4`

Subject highlighting uses `gerrit.stopPattern` / `gerrit.warningPattern` when color is on.

---

## Attention & exit codes

| Code | Meaning |
|------|---------|
| `0` | No commit requires attention |
| `1` | At least one commit requires attention |
| `2` | Invalid usage / range error |
| `3` | Gerrit API error |

Attention rules: shared `determine_attention()` in `core/gerrit_change_status.py` (same family as `ger edit --first-attention-commit`).

A commit with no usable Change-Id footer (missing or malformed) must include `missing-change-id` in `attention_reasons` and show a matching attention hint in text output (see [Reading-ger-log.md](../../Reading-ger-log.md#attention-hints-trailing--)). Prefer this over only `not-pushed` when the root cause is a bad/absent footer — Gerrit overlay cannot identify the change without one.

---

## JSON fields

Per commit: `sha`, `summary`, `pushed`, `patchset_status`, `verified`, `code_review`, `comments_unresolved`, `ci_failures`, `ci_links`, `gerrit_url`, `submittable`, `change_id`, `attention_reasons`, etc. (`patchset_status`: `active` \| `newer` \| `outdated` \| `absent`). `attention_reasons` includes `missing-change-id` when the local footer is absent or invalid. `ci_links` is a list of `{label, url, source}` (`source`: `checks` \| `message`) produced by a repo-local CI strategy when present; otherwise `[]`.

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.logShowUrl` | Force Gerrit URLs in text output even when hyperlinks are off (same as `--url` / `--show-url`). Compact `Open in gerrit` links are already shown by default when OSC 8 hyperlinks are on. |
| `gerrit.logShowChangeId` | Default for `--show-change-id` |
| `gerrit.webUrl`, auth | Required for API |
| `gerrit.stopPattern`, `gerrit.warningPattern` | Subject highlighting |

Full list: [Configuration.md](../../Configuration.md).

---

## See also

- [Reading-ger-log.md](../../Reading-ger-log.md) — user guide for text output
- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger show`](show.md)
- [`ger edit`](edit.md) (`--first-attention-commit`)
- [architecture.md](../../architecture.md)
