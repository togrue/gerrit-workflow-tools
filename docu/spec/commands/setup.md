# `ger setup`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_setup.py` |
| **Requires** | Interactive terminal (TTY) |

Interactively configure `ger.webUrl`, `ger.user`, and `ger.token` in git config.

---

## Usage

```
ger setup [--local]
```

| Flag | Effect |
|------|--------|
| (none) | Write to **global** git config (`git config --global`) |
| `--local` | Write to this repository's git config |

Prompts (with short descriptions):

| Key | Description |
|-----|-------------|
| `ger.webUrl` | HTTPS base URL of your Gerrit instance (e.g. `https://gerrit.your.domain/`) |
| `ger.user` | Your Gerrit username |
| `ger.token` | HTTP access token for the Gerrit REST API (from your user settings in the Gerrit web UI) |

When a token is already configured, leaving the token prompt empty keeps the existing value. A value still stored under its old name (`gerrit.webUrl`, `gerrit.user`, `gerrit.token`) is offered as the default; saving writes the `ger.*` key and removes the old name at that scope ([Renamed keys](../../Configuration.md#renamed-keys-gerrit-inbox-branchnamegerrit)).

Non-interactive environments print manual `git config` examples and exit with an error.

---

## See also

- [Configuration.md](../../Configuration.md)
- [SPEC.md](../../SPEC.md)
