# Reading `ger log` output

User guide for the default text output of `ger log`. For flags, JSON schema, and exit codes, see the command spec: [spec/commands/log.md](spec/commands/log.md).

---

## What `ger log` shows

One line per **local commit** in your stack (default range: `branch@{upstream}..branch`). Each line compares that commit to its Gerrit change: patchset alignment, CI, code review votes, and unresolved comments.

At the bottom, a **summary** line counts ready commits and outstanding issues.

Exit code **1** means at least one commit still needs your attention; **0** means the stack looks clean from `ger`'s perspective.

---

## Line layout

```text
<sha> <ps> <ver> <cr> <com> # <subject>                    # <attention hints>
```

Example (columns spaced for readability; actual output aligns columns across rows):

```text
a1b2c3d4 p  v+1 cr+2 com  # Add widget API               # submittable
e5f6g7h8 n  v+1 cr0      # Fix edge case in parser        # not-pushed
i9j0k1l2 p  v-1 cr+1 com  # Handle timeout                 # build failed, 1 unresolved comment
m3n4o5p6 -       com      # WIP: experiment                # not-pushed
```

With `--url` or `gerrit.logShowUrl`, a Gerrit web URL may appear at the end of the line (or on the next line with `-v`). When the terminal supports OSC 8 hyperlinks (`--hyperlinks auto`, or `--hyperlinks always`), that URL is shown as a clickable `Open in gerrit` instead of the raw address. JSON still includes the full `gerrit_url`. Use `--hyperlinks never` to keep the copyable URL.

With `--show-change-id`, a truncated Change-Id is appended after the subject.

---

## Status columns

### Patchset (`p`, `n`, `o`, `-`, …)

Single letter: is this local commit aligned with Gerrit?

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

When a commit is **not pushed** (`-` or `n` with no server state yet), Verified / Code-Review / comment columns are blank — there is nothing on Gerrit to show.

Details: [architecture.md](architecture.md#patchset-status-log--show--rebase-annotations).

### Verified (`v+1`, `v-1`, `v0`, `v?`)

Aggregated **Verified** label vote on the current patch set.

| Token | Meaning |
|-------|---------|
| `v+1` | Verified +1 (CI/build passed) |
| `v-1` | Verified −1 (CI/build failed) |
| `v0` | Verified 0 (neutral / no +1 yet) |
| `v?` | Unknown or not applicable |

With `-v` / `--verbose`, failed check names appear on indented lines below the commit (`# failed: …`).

### Code-Review (`cr+2`, `cr+1`, `cr0`, `cr-1`, `cr-2`, `cr?`)

Aggregated **Code-Review** label vote.

| Token | Meaning |
|-------|---------|
| `cr+2` | Approved (+2) |
| `cr+1` | Looks good (+1) |
| `cr0` | No score / 0 |
| `cr-1` / `cr-2` | Negative review |
| `cr?` | Unknown |

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
| `missing Change-Id` | Commit message has no usable Change-Id footer (absent or malformed); JSON reason `missing-change-id` |
| `not-pushed` | Commit not on Gerrit yet |
| `build failed` | Verified −1 or named CI failure |
| `N unresolved comment(s)` | Open review threads |
| `no reviewers` | Change has no reviewers assigned |
| `submittable` | Gerrit reports submittable and no blocking issues above |
| `abandoned` | Change abandoned on Gerrit |
| `merged drift` / `merged (equiv. unknown)` | Merged-state mismatch (see patchset tokens) |

These correspond to `attention_reasons` in `--json` output (e.g. `missing-change-id`, `ci-failed`, `awaiting-review`, `unresolved-comments`). The JSON field is authoritative for automation; the text hints are a human-readable subset.

---

## Subject highlighting

When color is enabled, commit subjects may be highlighted:

- **Stop pattern** (`gerrit.stopPattern`) — marks the ready-boundary tail (commits excluded from default push)
- **Warning pattern** (`gerrit.warningPattern`) — flags WIP-like or single-word subjects

Abandoned changes show a struck-through subject.

Configure patterns in [Configuration.md](Configuration.md).

---

## Summary line

```text
summary: ready 2/6 · CI 1 · comments 1 · on-gerrit 4
```

| Part | Meaning |
|------|---------|
| `ready N/M` | **N** commits are push-ready or cleanly merged; **M** is total commits in range |
| `CI` | Count of commits with failed Verified / CI |
| `comments` | Count of commits with unresolved comments |
| `on-gerrit` | Count of commits that exist on Gerrit |

Only non-zero categories after `ready` are shown.

---

## Verbose mode (`-v`)

Same columns on the first line, plus:

- Gerrit URL on the following indented line (when URLs are enabled)
- Indented `# failed: …` lines listing CI check names

Use verbose mode when the compact line is not enough context.

---

## JSON output

For scripts and tooling:

```bash
ger log --json
```

Each commit is one JSON object with fields such as `sha`, `patchset_status`, `verified`, `code_review`, `comments_unresolved`, `attention_reasons`, and `gerrit_url`. See [spec/commands/log.md](spec/commands/log.md#json-fields).

---

## See also

- [Getting-Started.md](Getting-Started.md) — first-run setup
- [spec/commands/show.md](spec/commands/show.md) — deep dive on one change
- [spec/commands/push.md](spec/commands/push.md) — pushing the ready prefix
- [spec/commands/edit.md](spec/commands/edit.md) — jump to first commit needing attention (`--first-attention-commit`)
