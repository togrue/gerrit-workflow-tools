# `ger show`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_show.py` |
| **Requires** | `ger.webUrl`, credentials |

One or more commits/changes: local commit message (when resolvable), Gerrit status line, inline comments (unresolved by default).

---

## Usage

```
ger show [options] [REV ...]
```

Each `REV` is a **changeish** (git ref, Change-Id, triplet, `change:<n>`, URL, `q:…`) or a git-style range `A..B` / `A...B` whose endpoints are changeish values that resolve to local commit SHAs. Default with neither `REV` nor `--stack`: `HEAD`.

### Change resolution

Single changeishes go through **`core/gerrit/change_resolution.py`** (same as other resolving commands). Bare Change-Ids that match multiple Gerrit changes are narrowed to the stack **target branch** with a transparency note; override with a triplet or `change:<n>`. Full rules: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md). With `--json`, output includes a `resolution` block ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)). Ambiguity after narrowing exits `4`.

**Ranges:** each endpoint is resolved to a local SHA (git-rev directly; Change-Id / Gerrit keys via the local stack or a locally present current revision). Then `git log` expands the range (oldest first). Endpoints that do not resolve to a local commit are errors.

**`--stack`:** includes every commit in `upstream_tip..HEAD`. May be combined with other `REV` args; duplicates (same Change-Id, else same SHA) keep the first occurrence. When the working branch (or `--branch`) has no upstream, prompt to set one on a TTY via `require_branch_upstream` (same helper as `ger log`); otherwise print the setup hint every command shares and exit `6` (`CONFIG`: the upstream is missing git configuration) rather than failing as a git error. Declining the prompt exits `1`. Without `--stack`, a target that needs the stack's target branch (`HEAD`, the default) asks the same way when the branch has no upstream; a change number never asks ([SPEC § A missing upstream](../../SPEC.md#a-missing-upstream)).

**Multiple targets:** `resolve_show_targets` in `core/gerrit_show.py` builds the ordered, deduped list. Human and Markdown print one block per commit. JSON with a single target keeps the flat object shape; multiple targets wrap as `{ "commits": [ … ] }`.

---

## Options

| Option | Description |
|--------|-------------|
| `--stack` | Include the local stack (`upstream_tip..HEAD`) |
| `--first-attention` | Show the oldest stack commit with **edit attention** (unresolved comments or CI failed; a Code-Review vote alone does not count). Does not move the attention cursor. Mutually exclusive with `--next-attention`; incompatible with `--stack` / REV |
| `-n` / `--next-attention` | Show the next edit-attention commit tip-ward of the cursor (or of a single REV as AFTER). Read-only; does not move the cursor |
| `--comments {unresolved,all,resolved}` | Which inline comment chains to print (default: `unresolved`) |
| `--context` | Include source lines around each comment (`enable-context` on `GET …/comments`; padding 2) |
| `--context-padding N` | Extra source lines before/after the comment (implies `--context`) |
| `--ci` / `--no-ci` | Attach **CI findings** from failed **CI links** (fetches CI logs). Default: `ger.showCiContent`, off. See [CI content](#ci-content) |
| `--json` | JSON payload |
| `--format {human,markdown,ai}` | Output format (default: human); `ai` is the Markdown view |
| `--ai` | Alias for `--format ai`: here the Markdown view ([Output formats](../../SPEC.md#output-formats)) |
| `--color`, `--hyperlinks`, `--debug-log` | Standard helpers (`--hyperlinks`: `always` \| `auto` \| `never`) |
| `-v` | Also fetch each change's CI pipelines (Checks) and list them under the commit |

`--json`, `--format`, and `--ai` are mutually exclusive.

**Markdown / `--ai`:** no ANSI or OSC 8; full comment bodies; headings per change and per `path:line` thread with blockquoted replies — suited for pasting into an AI review session.

`ger show --help` ends with examples, and points to `ger log --help` for the status columns.

---

## Behavior (current)

1. If `--stack`, ensure the stack branch has an upstream (TTY prompt when missing).
2. Resolve targets (`resolve_show_targets`: changeishes, ranges, optional `--stack`).
3. Fetch labels, patchset status, attention via `GerritService` / `gerrit_change_status`. A commit of the **local stack** is judged within the whole stack, as `ger log` judges it (`core/annotated_targets.py`, `annotate_targets`): a commit the chain below keeps from being submitted carries `chain-blocked`, and the one that blocks says why in its hint (`# awaiting review`). The three commands agree on every commit (`tests/test_attention_consistency.py`). Past the trust window this costs one light query for the rest of the stack. A local commit the upstream already contains, with no change on Gerrit, counts as merged (`m`, `# merged`), as in `ger log` with a range.
4. Load comment chains (and optional CI content) via `load_shown_commits`. Follow-up REST uses an unambiguous **triplet** (`project~push branch~Change-Id`): when resolution already selected a change, that triplet; otherwise `GerritService.cache_key` builds the push-branch triplet from the commit's Change-Id. Bare Change-Ids are never used for `…/comments`, drafts or any other `/changes/{id}` request — Gerrit 404s them when the same Change-Id exists on multiple branches ([change-and-commit-identifiers.md §3.2](../change-and-commit-identifiers.md#32-principles)). With `--stack`, each commit is shown as the change on the stack's own push branch, its sibling on another branch is not.
5. **Human / Markdown, multi-target:** every commit is listed. A commit with no matching comment chain (per `--comments`) prints only its headline, subject and a dim `(no unresolved comments)`, `(no comments)`, or `(no resolved comments)`.
6. **Human, per printed commit:** headline `commit <sha> <status cols>  # <attention>` (same tokens/colors as `ger log`, without the column padding: `commit 1a2b3c4 p v+1 cr+2  # submittable`), a dim `attention: …` line with every reason from `attention_reasons`, for a blocked commit a dim `chain blocked by 1a2b3c4 (awaiting review)` naming the commit that blocks it (the `attention:` line then leaves `chain-blocked` out, and is omitted when that was its only reason), then `Author: Name <email>, 3h ago (2026-11-24 04:39)` (the author date's age, then the date), `url: …` (full Gerrit URL, or a clickable `Open in gerrit` when `--hyperlinks` is on), `votes: Code-Review +2 Alice, -1 Bob · Verified +1 CI` (who voted what on the current patchset; omitted without votes, and while the commit is `newer` with other code; `--ai` prints it as `- Votes: …`), indented commit message, then each matching chain in a rounded box (`╭─ path:line ─…─ <id> ─╮` / `│` / `╰─…╯`): the top border names where it is (`Patchset-level comment`, `Commit message:12` for Gerrit's special paths) and, on the right, the chain's id, the one `ger reply <id>` takes. Each comment's author line adds its patchset and age: `alice · PS1 · 2h ago`. The commit message is wrapped to `ger.wrapWidth` (default 120). A box is fitted to the terminal, at most 100 columns wide, indent and borders included (`render/comments.py` `box_width`; piped, the same 100-column cap), within `ger.wrapWidth`; comment bodies, context lines and the path header are wrapped to it: break on spaces and tabs, and slice a token that has no space, and every box of a commit takes the width of the widest. The path header keeps the tail of an over-long path. The chain URL is left whole: the box's last row when it fits, else on its own line under the box. Unresolved boxes are yellow and say whose turn they are in the header: `(your turn)` (somebody else spoke last), `(draft to publish)` (you answered in a draft), `(their turn)` (your published comment is the last word); resolved boxes are grey with `(resolved)` in the header (so the state survives `--color=never`). Authors are flat inside the box (no reply gutter); the chain URL takes the same hyperlink shortening. `--comments all` prints unresolved chains first, then resolved. With `--context`, dimmed `line_number  text` rows from Gerrit `context_lines` sit at the top of the box (once per chain).
7. **Markdown / `--ai`:** the facts of the human view, in words: `## <sha> — <subject>`, then `- attention: …`, `- Chain blocked by 1a2b3c4 (awaiting review)` for a blocked commit, `- Patchset: pushed: Gerrit's current patchset` (the words of `ger log --help`'s legend), `- Change is work in progress and private` when it is, `- Gerrit: …`, `- Votes: …`, `- Reviewers: alice, bob (cc: carol)`; then `### Unresolved comments` and/or `### Resolved comments` according to `--comments`, each thread a `####` heading beneath (`` `path:line` ``, or `Patchset-level comment` in words). Resolved thread headings append `(resolved)`. Under each thread heading, ``Reply with `ger reply <id>` ``; each author adds the patchset and age (`**alice** · PS1 · 2h ago`). `--context` adds a fenced source block under the thread heading.

**Comment resolution:** Comments are grouped into chains via Gerrit `in_reply_to` (thread root = chain id). A chain is **resolved** when the **last** comment in the chain has `unresolved: false`. Default `--comments unresolved` lists only open chains. `--comments all` lists both (unresolved first). `--comments resolved` lists only closed chains. See `build_comment_chains()` / `collect_comment_chains()` in `comment_chains.py`. JSON `comment_chains[]` includes `"root_id"` (the id that names the chain), `"resolved": bool`, `"turn"` (`yours` \| `draft` \| `theirs`, `null` for a local chain or when Gerrit does not say who you are: `chain_turn()` in `comment_chains.py`; the last *published* comment decides, so a draft never makes a chain theirs) and `"start_line"`, the first line of a range comment. Each comment carries its `"patch_set"` and `"draft": bool`.

**Drafts:** your unpublished drafts (`GET …/drafts`) join the chain they reply to, always after its published comments, since publishing stamps them last. They are fetched on every run and never cached: drafts live in All-Users, so the change's freshness token does not move when one changes ([ADR-0007](../../adr/0007-follow-up-cache-key-is-note-db-meta.md)). Human output shows a draft in **bright blue** under `draft (you)`; Markdown heads it `**Draft reply (unpublished)**`. When your newest draft changes the chain's state, the label adds `-> resolves` or `-> reopens`. `"resolved"` and the `--comments` filter stay Gerrit's published state; JSON adds `"resolved_after_publish"`, the state once your drafts are published. Drafts are written with [`ger reply`](reply.md). A range shows as `path:start-end`. `--context` fetches `GET …/comments?enable-context=true&context-padding=N` and caches that payload under a separate context key so it cannot be served as a no-context hit. JSON comments include `context_lines` (`line_number`, `context_line`) when present.

**Suggested edits:** a reviewer suggests a change to the code in one of two ways, and `core/suggested_edits.py` reads both into one shape. A **suggestion block** is a ```` ```suggestion ```` fence the web UI's "Suggest edit" writes into the comment's text, and the common case: it replaces the lines the comment is on (a comment on the whole file has none). The block ends at the first line that is only ```` ``` ````, as the web UI reads it; CRLF line endings are read as LF. A **fix suggestion** is Gerrit's `CommentInfo.fix_suggestions` (`FixSuggestionInfo`: `fix_id`, `description`, `replacements` of `path`, `range`, `replacement`). Gerrit stores fix suggestions on a human comment only when the server enables the experiment `GerritBackendFeature__allow_fix_suggestions_in_comments` (`[experiments] enabled` in `gerrit.config`; without it 3.14 refuses them), and robot comments are gone from 3.14. The comment's text is always shown as written, so a suggestion block appears once, in it. A fix suggestion is written out below the text: human output a green `suggested fix: <description>`, then `<location> becomes:` (`insert at <location>:` for an empty range) and the new text as `+ ` lines, never wrapped; `<location>: delete` for an empty replacement. Markdown quotes a `**Suggested fix:**` and fences the new text with a fence longer than any run of backticks in it. The location is a plain range with 1-based lines and columns, not a `ger comment` LOCATION: `path:5` or `path:5-7` for whole lines, otherwise `path:L.C-L.C` from the first character replaced to the end given as the number of characters of the end line covered (`path:5.3-7.0` runs up to the start of line 7), and `path:5.3` for an insertion before column 3. JSON comments carry `"suggested_edits"` when there are any: `fix_id` (`null` for a block), `description`, `in_message` (`true` for a block) and `replacements` in Gerrit's shape plus `location`. A block whose text a fix suggestion on the same file already carries is not listed twice. Applying a suggested edit is not offered: there is no apply path yet (Gerrit's `POST …/fixes/{fix_id}/apply` makes a change edit, not a local change).

**Change-Id-only:** A change that matches a commit in the local stack renders as that commit, however it was addressed — provided that commit pushes to it (same project and push branch). A **sibling** on another target branch (`ger show proj~dev~I…` or `change:<dev's number>` from a branch pushing to `main`) is not that commit's change: it renders like any change with no local commit — its own number, patchset, votes, comments and URLs, in text, Markdown and JSON — and a note on stderr names the local stack commit with the same Change-Id and the branch it targets (`resolution.local_sibling` in JSON). When there is no local commit, the Author/date/message block is replaced by the change's subject.

**Exit code:** `0` whether or not a listed target has attention reasons; `1` (`REFUSED`) only if the user declines the upstream prompt.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, whether or not a target has attention reasons |
| `1` | `REFUSED` — you declined the upstream prompt |
| `3` | The changeish names no commit or change (Gerrit answered 404, or no such git revision) |
| `4` | Ambiguous changeish after narrowing |
| `5` | Gerrit failed: unreachable, credentials rejected, or another HTTP error. A local commit is still shown, its Gerrit state unknown, as in [`ger log`](log.md#attention--exit-codes) |

---

## Configuration

| Key | Effect |
|-----|--------|
| `ger.warningPattern` | Subject highlighting on status line |
| `ger.showCiContent` | Default for `--ci` (off) |
| `ger.ciCredential.<host>` | Secret for fetching CI content from that host |

---

## CI content

Off by default: `ger show` never fetches CI logs unless asked. `--ci` (or `ger.showCiContent=true`, overridden by `--no-ci`) fetches the **CI content** behind each listed commit's failed **CI links** and attaches its **CI findings** — findings only, never the log body (use [`ger ci --full`](ci.md) for that).

- **Human:** a dim `CI findings:` block after the commit message, before the comments: `label  url  (build, cached)` and one `tool  path:line  message` line per finding, warnings as a count below the errors. A commit with findings is not collapsed in a multi-commit view.
- **Markdown / `--ai`:** a `### CI findings` section with a `#### <label>` per label.
- **JSON:** a `ci_content` array (same entries as `ger ci --json`, without `body`). The key is absent when CI content is off.

**Fetch failure is soft:** the Gerrit and comment output is unchanged and the link's `error: …` is reported in the CI section (JSON: `provenance.error`). The exit code stays the attention code. Design: [ci-content.md](../ci-content.md).

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger log`](log.md)
- [`ger edit`](edit.md)
- [ci-content.md](../ci-content.md) — CI findings enrichment design
- [`ger ci`](ci.md)
- [architecture.md](../../architecture.md)
