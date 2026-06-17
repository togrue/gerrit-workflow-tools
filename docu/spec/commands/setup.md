# `ger setup`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_setup.py` |
| **Requires** | Interactive terminal (TTY) |

Interactively configure `gerrit.webUrl`, `gerrit.user`, and `gerrit.token` in git config.

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
| `gerrit.webUrl` | HTTPS base URL of your Gerrit instance (e.g. `https://gerrit.your.domain/`) |
| `gerrit.user` | Your Gerrit username |
| `gerrit.token` | HTTP access token for the Gerrit REST API (from your user settings in the Gerrit web UI) |

When a token is already configured, leaving the token prompt empty keeps the existing value.

Non-interactive environments print manual `git config` examples and exit with an error.

---

## See also

- [Configuration.md](../../Configuration.md)
- [SPEC.md](../../SPEC.md)
