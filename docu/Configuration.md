# Configuration reference

Git config drives defaults for Gerrit workflow tools. Values are read from repo `.git/config`, global `~/.gitconfig`, etc. (standard Git precedence).

**Convention:** keys live under the `gerrit` section unless noted. Boolean values accept `true`, `1`, `yes`, or `on` (case-insensitive). Command-line flags override these defaults when present; several commands support `--no-…` to force a behavior off when a config default would turn it on.

---

## Global (`[gerrit]`)

| Key | Purpose |
|-----|---------|
| `gerrit.remote` | Remote name for `git push` to Gerrit (default: `origin`). |
| `gerrit.webUrl` | HTTPS base for Gerrit REST API and web links (required for API commands). |
| `gerrit.user` | Username for HTTP Basic auth to the REST API. |
| `gerrit.password` / `gerrit.token` | Password or HTTP access token (token preferred). |
| `gerrit.defaultPushMode` | Default push mode label (e.g. `ready`); used by ready-boundary logic. |
| `gerrit.stopPattern` | **Repeatable.** Regex matched against **commit subject** (first line only in practice). The first matching commit starts the non-pushable tail unless `git gpush --all` or pattern overrides apply. If **no** `stopPattern` is set, built-in defaults apply: `^dropme!`, `^TODO\b`, `^test!`. Add or replace lines with multiple `git config --add gerrit.stopPattern '…'` entries. Use `git gpush --ignore-pattern` / `--no-config-patterns` to bypass without editing config. |
| `gerrit.gshowCommentTailLines` | Positive integer; truncates long comment bodies in `git gshow` (default `10`). |

---

## `git glog` — `gerrit.glog*`

| Key | Effect |
|-----|--------|
| `gerrit.glogShowUrl` | Default on: include Gerrit URLs in text output (same as `--url` / `--show-url`). |
| `gerrit.glogShowChangeId` | Default on: append Change-Id on each text line (`--show-change-id`). |
| `gerrit.glogOneline` | Default on: one-line format (`--oneline`). Use `--no-oneline` to show full rows. |
| `gerrit.glogCompact` | Default on: compact columns (`--compact`). Use `--no-compact` for full rows. |

---

## `git gpush` — `gerrit.gpush*`

| Key | Effect |
|-----|--------|
| `gerrit.gpushShowAttributes` | Default on: include Gerrit reviewer / wip / private preview (`--show-attributes`). Use `--no-show-attributes` to disable when this is set. |

---

## Branch-local (`branch.<name>.*`)

| Key | Effect |
|-----|--------|
| `branch.<name>.gerritTarget` | Gerrit destination branch for pushes and merge-base resolution. |
| `branch.<name>.gerritReviewers` | Comma-separated accounts; merged into `git gpush` ref options. |
| `branch.<name>.gerritPushMode` | Stored push mode for the branch (see `git gbranch`). |

Set via `git gbranch init` / `gbranch set-*` or `git config` / `set_branch_config` in code.

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
    glogShowUrl = true
    gpushShowAttributes = true
```

```bash
# Append another stop pattern (repeatable key)
git config --add gerrit.stopPattern '^hold:'

# Turn off default glog one-line without changing other config
git glog --no-oneline
```

---

## See also

- [Documentation index](README.md) — command list and first-time setup
- [`git gpush`](commands/gpush.md), [`git glog`](commands/glog.md) — command-specific options
