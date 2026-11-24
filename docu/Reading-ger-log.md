# Reading `ger log` output

User guide for the default text output of `ger log`. For flags, JSON schema, and exit codes, see the command spec: [spec/commands/log.md](spec/commands/log.md).

---

## What `ger log` shows

One line per **local commit** in your stack (default range: `branch@{upstream}..branch`). Each line compares that commit to its Gerrit change: patchset alignment, CI, code review votes, and unresolved comments.

At the bottom, a **summary** line counts what a push would upload and what needs you, then `next:` lines name the command for each state the stack is in.

When the **ready boundary** falls inside the stack, a plain line marks it, readable without
colour (piped output, `NO_COLOR`):

```text
---- ready boundary: `ger push` sends the commits above ----
```

The commit right below it is the one `ger push` stops at.

`ger log` exits **0** whether or not a commit needs your attention: the attention is in the output (`--json` for scripts), not the exit code. A non-zero exit means `ger log` could not do what was asked ([exit codes](spec/exit-codes.md)).

---

## Line layout

```text
<sha> <ps> <ver> <cr> <com> # <subject>                    # <attention hints>
```

Example (columns spaced for readability; actual output aligns columns across rows):

```text
a1b2c3d4 p  v+1 cr+2      # Add widget API               # submittable
e5f6g7h8 n  v?  cr?       # Fix edge case in parser      # ahead of gerrit
i9j0k1l2 p  v-1 cr+1 com  # Handle timeout               # build failed, 1 unresolved comment
m3n4o5p6 -                # WIP: experiment              # not pushed
```

When the terminal supports OSC 8 hyperlinks (`--hyperlinks auto`, or `--hyperlinks always`), a clickable `Open in gerrit` appears at the end of the primary line. The label is short enough to keep on by default. Without hyperlinks, the raw address appears only with `--url` or `ger.logShowUrl`. JSON still includes the full `gerrit_url`. Use `--hyperlinks never` to keep a copyable URL (with `--url`) or to omit the link.

On a terminal, each row fits its width: a subject too long for it is cut with `…` so the row, its `# …` hints included, stays on one line; the `next:` lines below the summary wrap between words, never inside a `` `command` ``. Piped output (`ger log | grep …`) is never cut.

With `--show-change-id` (or `-vv`), the full Change-Id appears on an indented continuation line below the commit — never truncated, and never on the primary line.

---

## Status columns

### Patchset (`p`, `n`, `o`, `-`, …)

Single letter: is this local commit aligned with Gerrit? `ger log --help` lists every token in short.

| Token | Meaning | Typical action |
|-------|---------|----------------|
| `p` | **Active** — local SHA is Gerrit's current patch set | Review feedback applies to this commit |
| `n` | **Newer** — change exists but local commit is ahead of server | `ger push` to upload |
| `o` | **Outdated** — this SHA was uploaded but is no longer current | Rebase/amend and push again |
| `-` | **Absent** — no Gerrit change for this Change-Id | Push to create the change |
| `a` | **Abandoned** on Gerrit | Close or restore the change in Gerrit |
| `m` | **Merged** (same content as merged change) | Often safe to drop or rebase away |
| `!` | **Merged drift** — merged on server but local content diverged | Investigate; may need manual fix |
| `?` | **Merged (equivalence unknown)** | Check Gerrit; rebase if unsure |
| `~` | **Unknown** — Gerrit could not be asked (unreachable, credentials rejected); the last stderr line says why | Fix the connection or credentials (`ger setup`), then rerun |

When a commit is **not pushed** (`-`), Verified / Code-Review / comment columns are blank — there is nothing on Gerrit to show. A commit marked `n` shows `v?` and `cr?`: the votes on Gerrit belong to the previous patch set, so they say nothing about this commit yet.

Definitions: [CONTEXT.md § The Gerrit side](../CONTEXT.md#the-gerrit-side).

### Verified (`v+1`, `v-1`, `v0`, `v?`)

Aggregated **Verified** label vote on the current patch set.

| Token | Meaning |
|-------|---------|
| `v+1` | Verified +1 (CI/build passed) |
| `v-1` | Verified −1 (CI/build failed) |
| `v0` | A Verified 0 that Gerrit reports while some vote is not 0 (rare) |
| `v?` | No Verified vote yet on this patch set; on an `n` commit, the votes are on the previous patch set |

With `-v` / `--verbose`, CI pipeline names appear on indented `CI:` lines below the commit. Failed pipelines are red, successful ones green. When the repo provides a **CI strategy** under `.ger/ci/` (see [Configuration.md](Configuration.md#domains)), pipeline names become clickable links on one line when hyperlinks are on; otherwise each pipeline is shown as `label url` on its own line.

### Code-Review (`cr+2`, `cr+1`, `cr0`, `cr-1`, `cr-2`, `cr?`)

Aggregated **Code-Review** label vote.

| Token | Meaning |
|-------|---------|
| `cr+2` | Approved (+2) |
| `cr+1` | Looks good (+1) |
| `cr0` | A Code-Review 0 that Gerrit reports while some vote is not 0 (rare) |
| `cr-1` / `cr-2` | Negative review |
| `cr?` | No Code-Review vote yet on this patch set; on an `n` commit, the votes are on the previous patch set |

### Comments (`com` or blank)

| Token | Meaning |
|-------|---------|
| `com` | At least one **unresolved** review comment thread |
| (blank) | No unresolved comments |

Use `ger show <ref>` for the full comment threads.

---

## Attention hints (trailing `# …`)

Right-aligned annotations summarize what needs action. Omitted when nothing stands out.

| Hint | Meaning |
|------|---------|
| `missing Change-Id` | Commit message has no Change-Id footer; JSON reason `missing-change-id` |
| `invalid Change-Id` | Commit message has a Change-Id footer whose value is not a valid Change-Id (`I` + 40 hex digits); JSON reason `invalid-change-id` |
| `unsquashed fixup commit` | A `fixup!`/`squash!` commit with no Change-Id, the commit `ger push` stops at; squash it with `ger rebase --autosquash`. JSON reason `unsquashed-fixup` |
| `not pushed` | Commit not on Gerrit yet |
| `Gerrit state unknown` | Gerrit could not be asked; JSON reason `gerrit-unknown`. `ger log` exits `5` |
| `build failed` | Verified −1 or named CI failure |
| `N unresolved comment(s)` | Open review threads |
| `no reviewers` | Change has no reviewers assigned |
| `ahead of gerrit` | Local commit is newer than Gerrit's current patch set |
| `diverged from Gerrit` | Ahead of Gerrit, but Gerrit's current patch set was uploaded from elsewhere (a web edit, another checkout) and is not in your history; JSON reason `diverged`. `ger push` refuses to replace it; `ger checkout <change> --update` merges it into your commit |
| `outdated patchset` | Gerrit has a newer patch set than this commit |
| `review issues` | A negative Code-Review vote |
| `awaiting review` | Only on the commit that blocks the chain: it waits for votes, and the changes above it wait for it. The last line names it: `chain blocked by 1a2b3c4 (awaiting review)`; the rows above do not repeat it. When another reason is why (`build failed`, `review issues`, …), that hint is the one, and `not submittable` when none says |
| `merged` | The change is merged on Gerrit, or the upstream already contains the commit |
| `WIP` | Change is work-in-progress on Gerrit; JSON reason `wip`. Replaces `no-reviewers` and `awaiting-review`. `ger push --ready` marks the changes ready again; the summary line counts them and says so |
| `private` | Change is private on Gerrit. Information only, not an attention reason |
| `submittable` | Gerrit reports submittable and no blocking issues above |
| `abandoned` | Change abandoned on Gerrit |
| `merged drift` / `merged (equiv. unknown)` | Merged-state mismatch (see patchset tokens) |

These correspond to `attention_reasons` in `--json` output (e.g. `missing-change-id`, `ci-failed`, `awaiting-review`, `unresolved-comments`). The JSON field is authoritative for automation; the text hints are a human-readable subset.

---

## Subject highlighting

When color is enabled, commit subjects may be highlighted:

- **Stop pattern** (`ger.stopPattern`) — marks the ready-boundary tail (commits excluded from default push)
- **Warning pattern** (`ger.warningPattern`) — flags WIP-like or single-word subjects

Abandoned changes show a struck-through subject.

Configure patterns in [Configuration.md](Configuration.md).

---

## Summary line

```text
summary: 4 on Gerrit · 1 to upload · 2 held back · 1 failed CI · 1 commit with comments
next: 1 commit to upload with `ger push`
chain blocked by 1a2b3c4 (build failed)
```

| Part | Meaning |
|------|---------|
| `on Gerrit` | Open changes on Gerrit (merged and abandoned ones are counted apart) |
| `to upload` | Commits `ger push` would upload: below the ready boundary, and new to Gerrit or ahead of it |
| `held back` | Commits at or above the ready boundary, which `ger push` leaves out |
| `merged` / `abandoned` | Changes Gerrit merged or abandoned |
| `failed CI` | Commits with failed Verified / CI |
| `commit(s) with comments` | Commits with unresolved comments (the row's hint counts the threads) |
| `WIP` | Work-in-progress changes; a `next:` line says ``ready them with `ger push --ready` `` (`--all --ready` when one sits above the ready boundary) |

Only counts above zero are shown. The `next:` lines below it name the command for each state the stack is in (an abandoned change, commits to upload, the upstream moved on, …), and the last line names the commit that blocks the chain, if one does.

---

## Verbose mode (`-v` / `-vv`)

The **primary line is identical** to default `ger log` (including inline `Open in gerrit` when hyperlinks are on). Verbosity only adds indented continuation lines:

- `-v`: `CI:` row listing all CI pipelines (failed in red, successful in green), and a `Reviewers:` row naming who is on the change (`alice, bob (cc: carol)`)
- `-vv`: the `-v` rows plus full `Change-Id:` line

Use verbose mode when the compact line is not enough context.

---

## JSON output

For scripts and tooling:

```bash
ger log --json
```

Each commit is one JSON object with fields such as `sha`, `patchset_status`, `verified`, `code_review`, `comments_unresolved`, `attention_reasons`, and `gerrit_url`. See [spec/commands/log.md](spec/commands/log.md#json-fields).

For an agent, `ger log --ai` prints the same stack in words, one line per commit and none of the columns above: see [spec/commands/log.md](spec/commands/log.md#output---ai).

---

## See also

- [Getting-Started.md](Getting-Started.md) — first-run setup
- [spec/commands/show.md](spec/commands/show.md) — deep dive on one change
- [spec/commands/push.md](spec/commands/push.md) — pushing the ready prefix
- [spec/commands/edit.md](spec/commands/edit.md) — jump to first commit needing attention (`--first-attention-commit`)
