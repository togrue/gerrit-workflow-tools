# ger log

**Status:** Implemented

Compact, actionable overview of the local commit chain vs Gerrit. Answers: *What do I need to do next?*

Shows only commits that require attention by default (CI failures, negative votes, unresolved comments, missing or non-matching Gerrit patch set, blocked by an earlier commit). All commits visible with `--full`.

Requires `gerrit.webUrl` in git config.

---

## Usage

```
ger log [options] [REVSET]
```

`REVSET` — optional commit range (e.g. `origin/main..HEAD`). Default is merge-base..`HEAD`.

---

## Options

| Option | Description |
|--------|-------------|
| `--full` | Show all commits, not just attention-required |
| `--oneline` | One line per commit; detail lines inlined (default: `gerrit.logOneline`; use `--no-oneline` to force full rows) |
| `--no-compact` | When `gerrit.logCompact` is on, show full status rows instead of minimal single-character columns |
| `--url`, `--show-url` | Print each change’s Gerrit web URL (default: `gerrit.logShowUrl`) |
| `--show-change-id` | Append Change-Id on each text line (default: `gerrit.logShowChangeId`) |
| `--json` | Machine-readable JSON output |
| `--color WHEN` | Colorize output: `always`, `auto`, or `never` |
| `--debug-log` | Log git commands to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

**git config defaults** (boolean: `true` / `1` / `yes` / `on`): `gerrit.logShowUrl`, `gerrit.logShowChangeId`, `gerrit.logOneline`, `gerrit.logCompact`. CLI flags override when present; `--no-oneline` and `--no-compact` defeat the oneline and compact defaults. See [Configuration.md](../Configuration.md#ger-log--gerritlog).

---

## Output format

Default — one primary line per commit, with optional detail lines:

```
<sha> <push> <verified> <code-review> <comments> <sub> # <subject> [<change-id>]
[# failed: <job-name>, ...]
[# comments: N unresolved]
```

`<sub>` is a submittability hint: `✓` when the change is submittable, `·` when it is not (dim), or two spaces when there is no Gerrit change yet. Missing **Verified** / **Code-Review** votes are shown as dim `·` placeholders instead of blank space.

Example:

```
a1b2c3d p v+1 cr+2        # Base cleanup (local SHA = current patch set)
b2c3d4e p v-1 cr+2        # Color scheme
# failed: style
c3d4e5f p v+1 cr+1 com    # Add status characters
# comments: 2 unresolved
d4e5f6a n v+1 cr+2        # Amended locally; labels are for Gerrit's current revision
e5f6a7b o v+1 cr+2        # Local tree is an old patch set; server has a newer revision
f6a7b8c -                 # Change not on Gerrit yet (no matching change)

summary: ready 2/6 · CI 1 · comments 1 · review 3
```

### Column definitions

| Column | Meaning |
|--------|---------|
| `p` | Local commit SHA is Gerrit's **current** patch set (votes apply to this commit). |
| `n` | A change exists, but this SHA is **not** on the server (e.g. amended or rewritten locally)—newer than the remote patch set. |
| `o` | This SHA was uploaded but is **not** the current patch set (outdated vs the change tip). |
| `-` | No Gerrit change for this commit (nothing pushed for this Change-Id yet). |
| `v+1` / `v-1` / (blank) | CI passed / failed / no vote (from the change's **current** revision). |
| `cr+2` / `cr+1` / `cr-1` / `cr-2` / (blank) | Code-Review vote (same caveat: current revision on the server). |
| `com` / (blank) | Unresolved comments exist |
| `✓` / `·` | Change is submittable / not (blank column if not on Gerrit) |

When the first column is not `p`, **Verified** / **Code-Review** still reflect Gerrit's current patch set, not necessarily your local SHA.

### Color coding

| Color | Meaning |
|-------|---------|
| green | Current patch set (`p`), or progress toward merge (`v+1`, `cr+2`) |
| red | Outdated patch set (`o`), blocking issue (`v-1`, `cr-2`, CI failure detail lines) |
| yellow | Local ahead of Gerrit (`n`), or needs attention (comments, `cr-1`) |
| dim | Not on Gerrit (`-`) |

Commit subjects are also highlighted in color mode when they match configured summary patterns: stop matches (`gerrit.stopPattern`) and warning matches (`gerrit.warningPattern`). If both patterns match the same text, stop highlighting wins.

### Compact format (`gerrit.logCompact`)

Extra column before comment flag: `+` submittable, `.` not, `-` not on Gerrit.

```
a1b2c3d p +1 +2 + .
b2c3d4e p -1 +2 + .
c3d4e5f p +1 +1 + c
d4e5f6a n +1 +2 + .
e5f6a7b o +1 +2 + .
f6a7b8c - .  .  - .
```

---

## Attention detection

A commit requires attention if **any** of the following:
- Not on Gerrit (`-` / no change for this Change-Id)
- Local commit is ahead of the server's current revision (`n` / ahead-of-gerrit)
- Local commit is a non-current patch set (`o` / outdated-patchset)
- `v-1` (CI failed)
- `cr-1` or `cr-2`
- Lacks `cr+2` (awaiting review)
- Has unresolved comments
- Depends on an earlier commit that is not submittable (chain-blocked)

The **ready-to-push** summary count includes commits that are absent on Gerrit or ahead of Gerrit (`-` and `n`).

---

## JSON output

Each commit object:

```json
{
  "sha": "...",
  "summary": "...",
  "pushed": true,
  "patchset_status": "active",
  "verified": 1,
  "code_review": 2,
  "comments_unresolved": 0,
  "ci_failures": [],
  "gerrit_url": "https://...",
  "submittable": true,
  "change_id": "I…",
  "attention_reasons": []
}
```

`patchset_status` is one of `active`, `newer`, `outdated`, or `absent` (see the first output column above). `pushed` is `true` when a Gerrit change exists for the Change-Id (`active`, `newer`, or `outdated`).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | No attention required |
| `1` | At least one commit requires attention |
| `2` | Invalid usage / range error |
| `3` | Gerrit API error |

---

## See also

- [`ger show`](show-todos.md) — Gerrit status and comment bodies for one commit or Change-Id
- `git log` over `merge-base..HEAD` — local commit list only (no Gerrit API)
- [Testing guide](../Howto_Test.md) — how to run `ger log` against a real Gerrit instance
