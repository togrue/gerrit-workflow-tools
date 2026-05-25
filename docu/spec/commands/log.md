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

`REV_RANGE` — optional (e.g. `origin/main..HEAD`). Default: `branch@{upstream}..branch` when upstream exists; otherwise error with hint to set upstream / `ger branch infer-upstream`.

---

## Options

| Option | Description |
|--------|-------------|
| `--json` | Machine-readable JSON (one object per commit) |
| `--color WHEN` | `always` \| `auto` \| `never` |
| `--url`, `--show-url` | Gerrit web URL per line (default: `gerrit.logShowUrl`) |
| `--show-change-id` | Append Change-Id on text lines (default: `gerrit.logShowChangeId`) |
| `-v`, `--verbose` | Expanded layout: indented detail lines; URLs on following line when URLs enabled |
| `--debug-log` | Log git commands to stderr (repeat for more detail) |
| `--follow-merges` | Include merge commits in range (see shared helper in `cli_common`) |

---

## Output (text)

Default: one primary line per commit, optional `# …` detail lines, trailing **summary** line.

Columns: patchset token (`p`/`n`/`o`/`-`), Verified, Code-Review, comment marker, submittability hint, subject. See [architecture.md](../../architecture.md#patchset-status-log--show--rebase-annotations).

**Summary example:** `summary: ready 2/6 · CI 1 · comments 1 · review 3`

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

---

## JSON fields

Per commit: `sha`, `summary`, `pushed`, `patchset_status`, `verified`, `code_review`, `comments_unresolved`, `ci_failures`, `gerrit_url`, `submittable`, `change_id`, `attention_reasons`, etc. (`patchset_status`: `active` \| `newer` \| `outdated` \| `absent`).

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.logShowUrl` | Default for `--url` |
| `gerrit.logShowChangeId` | Default for `--show-change-id` |
| `gerrit.webUrl`, auth | Required for API |
| `gerrit.stopPattern`, `gerrit.warningPattern` | Subject highlighting |

Full list: [Configuration.md](../../Configuration.md).

---

## V1 scope delta

From [Version 1 Scope](../../Version%201%20Scope.md) — **not yet implemented:**

| Item | Notes |
|------|-------|
| `--unresolved-comments` | Inline full text of unresolved chains |
| `-v` URLs for non-clean only | Partially: verbose expands layout; scope asks URLs only when status is non-clean |
| `-<n>` limiter | Explicitly deferred |

---

## See also

- [`ger show`](show.md)
- [`ger edit`](edit.md) (`--first-attention-commit`)
- [architecture.md](../../architecture.md)
