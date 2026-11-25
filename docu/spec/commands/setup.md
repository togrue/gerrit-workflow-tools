# `ger setup`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_setup.py` |
| **Requires** | Interactive terminal (TTY) |

Interactively configure `ger.webUrl` and that host's user and token (`ger.<webUrl>.user`, `ger.<webUrl>.token`) in git config.

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
| `ger.<webUrl>.user` | Your Gerrit username |
| `ger.<webUrl>.token` | HTTP access token for the Gerrit REST API (from your user settings in the Gerrit web UI) |

The user and token are written for the entered host only ([Credentials are per host](../../Configuration.md#credentials-are-per-host)).

When a token is already configured, leaving the token prompt empty keeps the existing value: the host's `ger.<webUrl>.token`, else the unscoped `ger.token`. The user defaults to `ger.<webUrl>.user`, else `ger.user`. A value still stored under its old name (`gerrit.webUrl`, `gerrit.user`, `gerrit.token`) is offered as the default too. Saving writes the scoped keys and removes the unscoped `ger.token` and `ger.password` (and their `gerrit.*` names) at that scope, since they would go to any host `ger.webUrl` names; a password moves to `ger.<webUrl>.password` unless the host has one ([Renamed keys](../../Configuration.md#renamed-keys-gerrit-inbox-branchnamegerrit)).

Non-interactive environments print manual `git config` examples and exit with an error.

---

## See also

- [Configuration.md](../../Configuration.md)
- [SPEC.md](../../SPEC.md)
