# `ger config`

| | |
|--|--|
| **Status** | Implemented (screen only; does not write git config yet) |
| **Module** | `src/gerrit_workflow_tools/cli_config.py` |
| **Requires** | Interactive terminal (stdin and stdout are TTYs) |

Full-screen editor for the git config settings `ger` reads. Every field is editable as soon as the screen opens. There is no separate read-only mode.

Saving (on blur, or as a batch confirm) is not implemented. Ctrl-C closes the screen and leaves git config unchanged.

---

## Usage

```
ger config
```

Shared `--color`, `--hyperlinks`, and `--debug-log` flags apply. They do not change the screen layout.

Non-interactive environments print an error and exit 1 (`ATTENTION`).

---

## Layout

Sections, in order:

1. General
2. `ger show`
3. `ger log`
4. `ger push`
5. `ger inbox`
6. `ger rebase`
7. `ger edit`

Commands with no settings of their own are omitted. Branch-scoped keys (`branch.<name>.gerritTarget`, `branch.<name>.gerritReviewers`) appear under `ger push` when a working branch resolves. Attention-cursor identity and history are walk state, not rows on this screen.

Each row shows:

- the git config key
- an editable control filled with the current value, or the default when the key is absent
- the default, the raw git config value (`(unset)` when absent), the allowed values when the setting has them, and a short description

Booleans are `true` / `false`. A stored spelling such as `yes` is shown as `true`, and the raw spelling stays on the git config line.

---

## Defaults and git config

Resolution lives in `resolve_config_form` (`core/config_form.py`). The screen is `build_config_screen`.

A missing or blank key displays its default and is not treated as stored. `planned_config_write` is the rule a future save will use: do nothing while the field still matches what was resolved, write when the user changes it, and unset a stored value edited back to the default. The screen does not call it.

`gerrit.editNextShow` (default off) is the `ger edit` flag that makes `ger edit --next` run `ger show` after a successful move. See [edit.md](edit.md) and [Configuration.md](../../Configuration.md#ger-edit).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Screen closed |
| `1` | `ATTENTION` — not a TTY |
| `2` | Usage error |

---

## See also

- [Configuration.md](../../Configuration.md) — key reference
- [`ger setup`](setup.md) — first-run web URL, user, and token
- [`ger edit`](edit.md) — `gerrit.editNextShow`
