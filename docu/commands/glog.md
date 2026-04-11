# git glog

**Status:** Implemented

Compact, actionable overview of the local commit chain vs Gerrit. Answers: *What do I need to do next?*

Shows only commits that require attention by default (CI failures, negative votes, unresolved comments, not yet pushed, blocked by an earlier commit). All commits visible with `--full`.

Requires `gerrit.webUrl` in git config.

---

## Usage

```
git glog [options]
```

---

## Options

| Option | Description |
|--------|-------------|
| `--full` | Show all commits, not just attention-required |
| `--oneline` | One line per commit; detail lines inlined |
| `--compact` | Minimal single-character status columns |
| `--json` | Machine-readable JSON output |
| `--range REVSET` | Override commit range (e.g. `origin/main..HEAD`) |
| `--no-color` | Disable colored output |
| `-v`, `--verbose` | Log git commands to stderr |

---

## Output format

Default — one primary line per commit, with optional detail lines:

```
<sha> <push> <verified> <code-review> <comments>  # <subject>
[# failed: <job-name>, ...]
[# comments: N unresolved]
```

Example:

```
a1b2c3d p v+1 cr+2        # Base cleanup
b2c3d4e p v-1 cr+2        # Color scheme
# failed: style
c3d4e5f p v+1 cr+1 com    # Add status characters
# comments: 2 unresolved
d4e5f6a n                  # Refactor output formatting

summary:
ready-to-push: 1
ci-failures: 1
unresolved-comments: 1
awaiting-review: 1
```

### Column definitions

| Column | Meaning |
|--------|---------|
| `p` / `n` | Pushed to Gerrit / not yet pushed |
| `v+1` / `v-1` / (blank) | CI passed / failed / no vote |
| `cr+2` / `cr+1` / `cr-1` / `cr-2` / (blank) | Code-Review vote |
| `com` / (blank) | Unresolved comments exist |

### Color coding

| Color | Meaning |
|-------|---------|
| green | Progress toward merge (`v+1`, `cr+2`) |
| red | Blocking issue (`v-1`, `cr-2`, CI failure detail lines) |
| yellow | Needs attention (comments, `cr-1`) |
| cyan | Not yet pushed |
| dim | Pushed and stable |

### Compact format (`--compact`)

```
a1b2c3d p +1 +2 .
b2c3d4e p -1 +2 .
c3d4e5f p +1 +1 c
d4e5f6a n .  .  .
```

---

## Attention detection

A commit requires attention if **any** of the following:
- Not pushed
- `v-1` (CI failed)
- `cr-1` or `cr-2`
- Lacks `cr+2` (awaiting review)
- Has unresolved comments
- Depends on an earlier commit that is not submittable (chain-blocked)

---

## JSON output

Each commit object:

```json
{
  "sha": "...",
  "summary": "...",
  "pushed": true,
  "verified": 1,
  "code_review": 2,
  "comments_unresolved": 0,
  "ci_failures": [],
  "gerrit_url": "https://...",
  "submittable": true,
  "attention_reasons": []
}
```

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

- [`git gcomments`](gcomments.md) — full comment text for a single change
- [`git gstack`](gstack.md) — local-only stack view (no Gerrit API)
- [Testing guide](../Howto_Test.md) — how to run `glog` against a real Gerrit instance
