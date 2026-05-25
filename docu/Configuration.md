# Configuration reference

Git config drives defaults for Gerrit workflow tools. Values are read from repo `.git/config`, global `~/.gitconfig`, etc. (standard Git precedence).

**Convention:** keys live under the `gerrit` section unless noted. Boolean values accept `true`, `1`, `yes`, or `on` (case-insensitive). Command-line flags override these defaults when present.

---

## Global (`[gerrit]`)

| Key | Purpose |
|-----|---------|
| `gerrit.remote` | Remote name for `git push` to Gerrit (default: `origin`). |
| `gerrit.webUrl` | HTTPS base for Gerrit REST API and web links (required for API commands). |
| `gerrit.user` | Username for HTTP Basic auth to the REST API. |
| `gerrit.password` / `gerrit.token` | Password or HTTP access token (token preferred). |
| `gerrit.stopPattern` | **Repeatable.** Regex matched against **commit subject** (first line only in practice). The first matching commit starts the non-pushable tail unless `ger push --all` or pattern overrides apply. If **no** `stopPattern` is set, built-in defaults apply: `^dropme!`, `^TODO\b`, `^test!`. Add or replace lines with multiple `git config --add gerrit.stopPattern '…'` entries. Use `ger push --ignore-pattern` to drop specific patterns without editing config. |
| `gerrit.warningPattern` | **Repeatable.** Regex matched against commit subject for warning highlighting in `ger log`, `ger push`, and `ger show` when color output is enabled. Defaults when unset: single-word subject (`^[^\\s]+$`), `wip`, `todo` (case-insensitive). Stop-pattern highlighting takes precedence when both match the same text span. |
| `gerrit.showCommentTailLines` | Positive integer; truncates long comment bodies in `ger show` (default `10`). |

---

## `ger log` — `gerrit.log*`

| Key | Effect |
|-----|--------|
| `gerrit.logShowUrl` | Default on: include Gerrit URLs in text output (same as `--url` / `--show-url`). |
| `gerrit.logShowChangeId` | Default on: append Change-Id on each text line (`--show-change-id`). |

---

## `ger push` — `gerrit.push*` and related

| Key | Effect |
|-----|--------|
| `gerrit.pushShowAttributes` | When `true`, include Gerrit reviewer / wip / private preview on the push preview (requires `gerrit.webUrl` and credentials). Default off when unset. |
| `gerrit.lastPushedBranch` | Default on: after a **successful** `ger push`, create or move the local branch `lastPush/<current-branch-name>` to the commit that was pushed (the same tip as in the refspec). Set `false` to skip. |

---

## Branch-local (`branch.<name>.*`)

| Key | Effect |
|-----|--------|
| `branch.<name>.gerritTarget` | **Optional override** for the Gerrit **destination branch** (e.g. `main`, `dev`). When unset, `ger push` and `ger rebase` infer the destination from `@{upstream}` if its remote name matches `gerrit.remote` (default `origin`). When set, it wins for push, merge-base, and `ger rebase --onto-remote`. The value must resolve to an existing ref—typically a local branch of that name or `refs/remotes/<remote>/<branch>` after `git fetch` on `gerrit.remote`. If the tool reports that the target is missing locally, fetch from the remote first; do not create a local branch literally named `origin/<branch>`—that is the remote-tracking name space, not a branch you should create by hand. |
| `branch.<name>.gerritReviewers` | Comma-separated accounts; merged into `ger push` ref options. |

Set via `ger branch init` / `ger branch set-*` or `git config` / `set_branch_config` in code.

---

## Examples

```ini
[gerrit]
    remote = origin
    webUrl = https://gerrit.example.com
    user = me
    token = secret
    stopPattern = ^dropme!
    stopPattern = ^WIP:
    warningPattern = ^[^\\s]+$
    warningPattern = (?i:\\bwip\\b)
    warningPattern = (?i:\\btodo\\b)
    logShowUrl = true
    pushShowAttributes = true
    lastPushedBranch = true
```

```bash
# Append another stop pattern (repeatable key)
git config --add gerrit.stopPattern '^hold:'

# Show Gerrit URLs by default in ger log
git config gerrit.logShowUrl true
```

---

## See also

- [Documentation index](README.md) — command list and first-time setup
- [`ger push`](commands/push.md), [`ger log`](commands/log.md) — command-specific options
