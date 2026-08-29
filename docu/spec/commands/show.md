# `ger show`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_show.py` |
| **Requires** | `gerrit.webUrl`, credentials |

One or more commits/changes: local commit message (when resolvable), Gerrit status line, unresolved inline comments.

---

## Usage

```
ger show [options] [REV ...]
```

Each `REV` is a **changeish** (git ref, Change-Id, triplet, `change:<n>`, URL, `q:…`) or a git-style range `A..B` / `A...B` whose endpoints are changeish values that resolve to local commit SHAs. Default with neither `REV` nor `--stack`: `HEAD`.

### Change resolution

Single changeishes go through **`core/gerrit/change_resolution.py`** (same as other resolving commands). Bare Change-Ids that match multiple Gerrit changes are narrowed to the stack **target branch** with a transparency note; override with a triplet or `change:<n>`. Full rules: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md). With `--json`, output includes a `resolution` block ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)). Ambiguity after narrowing exits `4`.

**Ranges:** each endpoint is resolved to a local SHA (git-rev directly; Change-Id / Gerrit keys via the local stack or a locally present current revision). Then `git log` expands the range (oldest first). Endpoints that do not resolve to a local commit are errors.

**`--stack`:** includes every commit in `upstream_tip..HEAD`. May be combined with other `REV` args; duplicates (same Change-Id, else same SHA) keep the first occurrence. When the working branch (or `--branch`) has no upstream, prompt to set one on a TTY via `require_branch_upstream` (same helper as `ger log`); otherwise print a setup hint and exit `1` rather than failing as a git error. Without `--stack`, missing upstream is not prompted.

**Multiple targets:** `resolve_show_targets` in `core/gerrit_show.py` builds the ordered, deduped list. Human and Markdown print one block per commit. JSON with a single target keeps the flat object shape; multiple targets wrap as `{ "commits": [ … ] }`.

---

## Options

| Option | Description |
|--------|-------------|
| `--stack` | Include the local stack (`upstream_tip..HEAD`) |
| `--json` | JSON payload |
| `--format {human,markdown}` | Output format (default: human) |
| `--ai` | Alias for `--format markdown` |
| `--color`, `--hyperlinks`, `--debug-log`, `-v` | Standard helpers (`--hyperlinks`: `always` \| `auto` \| `never`) |

`--json`, `--format`, and `--ai` are mutually exclusive.

**Markdown / `--ai`:** no ANSI or OSC 8; full comment bodies; headings per change and per `path:line` thread with blockquoted replies — suited for pasting into an AI review session.

---

## Behavior (current)

1. If `--stack`, ensure the stack branch has an upstream (TTY prompt when missing).
2. Resolve targets (`resolve_show_targets`: changeishes, ranges, optional `--stack`).
3. Fetch labels, patchset status, attention via `GerritService` / `gerrit_change_status`.
4. **Human, multi-target:** only commits with unresolved comment chains are printed (clean commits are omitted). If every target is clean, print a single dim `(no unresolved comments)`.
5. **Human, per printed commit:** headline `commit <sha> <status cols>  # <attention>` (same tokens/colors as `ger log`), then `Author: … [date]`, `url: …` (full Gerrit URL, or a clickable `Open in gerrit` when `--hyperlinks` is on), indented commit message, then each unresolved chain in a yellow rounded box (`╭─ path:line ─…╮` / `│` / `╰─…╯`). Authors are flat inside the box (no reply gutter); chain URL is the last inner line (same hyperlink shortening).
6. **Markdown / `--ai`:** headings per change; unresolved section still lists all targets (including clean).

**Comment resolution:** Comments are grouped into chains via Gerrit `in_reply_to` (thread root = chain id). A chain is **resolved** when the **last** comment in the chain has `unresolved: false`; only unresolved chains are listed. See `build_comment_chains()` / `collect_unresolved_comment_chains()` in `comment_chains.py`.

**Change-Id-only:** When there is no local commit, the Author/date/message block is skipped.

**Exit code:** attention (`1`) if **any** listed target has attention reasons (including omitted clean commits that still need attention), or if the user declines the `--stack` upstream prompt.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, no attention reasons |
| `1` | Success but attention required |
| `3` | Gerrit / git resolution error |
| `4` | Ambiguous changeish after narrowing |

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.warningPattern` | Subject highlighting on status line |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger log`](log.md)
- [`ger edit`](edit.md)
- [architecture.md](../../architecture.md)
