# `ger show`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_show.py` |
| **Requires** | `gerrit.webUrl`, credentials |

Single commit or change: local commit message (when resolvable), Gerrit status line, unresolved inline comments.

---

## Usage

```
ger show [options] [REV]
```

`REV` — git ref, Change-Id (`I…`), change number, or Gerrit query. Default: `HEAD`.

---

## Options

| Option | Description |
|--------|-------------|
| `--full` | No tail truncation on comment bodies |
| `--comment-tail-lines N` | Last N lines per comment (overrides config) |
| `--json` | JSON payload (full comment text; ignores tail truncation) |
| `--color`, `--debug-log`, `-v` | Standard helpers |

---

## Behavior (current)

1. Resolve ref → stack row + Gerrit change (`resolve_show_commit_row`).
2. Fetch labels, patchset status, attention via `GerritService` / `gerrit_change_status`.
3. If **local** commit: print `git show` medium message body first.
4. Print Gerrit URL (dim), detail lines, primary status line (same vocabulary as `ger log`).
5. If any **unresolved** inline comments (per-comment `unresolved: true` in API map): print `Unresolved comments:` and per-comment blocks (path, author, url, body).

**Comment resolution (current):** `collect_unresolved_comments()` treats each API comment with `unresolved: true` independently — **not** chain-level “last comment resolved” semantics.

**Change-Id-only:** When there is no local commit, the git message block is skipped.

**Comment threads:** Gerrit can mark a thread resolved while earlier replies stay visible. The tool treats each API comment with `unresolved: true` independently, so resolved chains may still list earlier replies that look unresolved.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, no attention reasons |
| `1` | Success but attention required (or generic error in some paths — see code `_EXIT_*`) |
| `3` | Gerrit / git resolution error |

(Implementation uses `_EXIT_ATTENTION` and `_EXIT_ERROR`; treat non-zero as failure for scripting unless documented in tests.)

---

## Configuration

| Key | Effect |
|-----|--------|
| `gerrit.showCommentTailLines` | Default tail lines (default `10`) |
| `gerrit.warningPattern` | Subject highlighting on status line |

---

## See also

- [`ger log`](log.md)
- [`ger edit`](edit.md)
- [architecture.md](../../architecture.md)
