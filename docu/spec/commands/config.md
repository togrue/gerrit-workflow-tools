# `ger config`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_config.py` |
| **Requires** | Interactive terminal (stdin and stdout are TTYs) |

Opens ger's git config settings in your git editor as a temporary `key=value` file (same shape as `git config --list`). Save and close to apply; delete all content to abort.

Credentials (`gerrit.token`, `gerrit.password`) are not included — use [`ger setup`](setup.md).

---

## Usage

```
ger config
```

Shared `--color`, `--hyperlinks`, and `--debug-log` flags apply.

Non-interactive environments print an error and exit `2` (`USAGE`), as every command does that needs a terminal it does not have.

---

## Editor file

Sections, in order: General, `ger show`, `ger log`, `ger push`, `ger inbox`, `ger review`, `ger rebase`, `ger edit`, `ger serve`. Commands with no settings are omitted. Branch-scoped keys appear under `ger push` when a working branch resolves.

Each setting is:

1. description comment
2. `# default: …` (empty defaults as `(unset)`)
3. optional `# allowed: …`
4. `# scope: local` / `global` / `unset (uncomment to write local)` (or another git scope name)
5. `key=value` when stored, or `# key=<default>` when unset

Editor: `GIT_EDITOR` → `core.editor` → `VISUAL` → `EDITOR` → `vi`. GUI editors need a wait flag (`code --wait`, etc.).

---

## Apply rules

| Edit | Write |
|------|--------|
| Uncomment an unset key (any value, including the default) | `git config --local` |
| Change a local value | `git config --local` |
| Change a global value | `git config --global` |
| Comment out a stored key | `git config --unset` at that scope |
| Leave an active line unchanged (including an explicit stored default) | no write |

Empty file after the editor exits → abort (`ATTENTION`). Parse errors → usage exit `2` with line numbers. Every catalog key must appear exactly once (active or commented).

Resolution lives in `resolve_config_form` (`core/config_form.py`); file format in `render_config_editor_text` / `parse_config_editor_text` (`core/config_editor_file.py`). Scope comes from `Settings.scope` (`git config --list --show-scope`).

`gerrit.editNextShow` (default off) is the `ger edit` flag that makes `ger edit --next` run `ger show` after a successful move. See [edit.md](edit.md) and [Configuration.md](../../Configuration.md#ger-edit).

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Applied (or no changes) |
| `1` | `ATTENTION` — not a TTY, empty file abort, or editor non-zero exit |
| `2` | Usage / parse error |
| `7` | `GIT` — editor binary not found |

---

## See also

- [Configuration.md](../../Configuration.md) — key reference
- [`ger setup`](setup.md) — first-run web URL, user, and token
- [`ger edit`](edit.md) — `gerrit.editNextShow`
