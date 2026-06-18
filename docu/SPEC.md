# Gerrit Workflow Tools — Specification

**Single source of truth** for CLI behavior and architecture. When docs and code disagree, **fix the code or update this spec** — not the other way around.

| Document | Role |
|----------|------|
| This file | Index, conventions, command registry |
| [architecture.md](architecture.md) | System design, shared concepts, module map |
| [spec/commands/](spec/commands/) | One spec per shipped `ger` command |
| [Configuration.md](Configuration.md) | Git config keys (referenced by command specs) |

**Operational docs** (not behavioral specs): [Completion.md](Completion.md).

---

## Conventions

Each command spec lists its **implementation module** (e.g. `cli_log.py`) so reviewers can diff spec ↔ code quickly.

### Global `ger` behavior

- **Entry:** `ger <command> [options]` — see `cli_ger.py` for the authoritative command list.
- **`ger --help`** — lists registered commands (sorted).
- **`ger --refresh`** — sets `GER_CACHE_REFRESH=1` for the invoked command (bypass stale Gerrit cache reads).
- **Aliases** (not shown in `ger --help`): `changeid` → `change-id`; `restack` / `stack` → `rebase`.
- **Per-command help:** `ger <command> --help` — options there override narrative spec if they differ (then fix the spec).

### Shared concepts

Defined once in [architecture.md](architecture.md): **local stack**, **ready boundary**, **Gerrit target branch**, **Change-Id**, **attention**, **patchset status** (`p` / `n` / `o` / `-`).

---

## Command registry

Commands listed here are registered in `cli_ger.py` today.

### Core loop

| Command | Spec |
|---------|------|
| `ger log` | [spec/commands/log.md](spec/commands/log.md) |
| `ger push` | [spec/commands/push.md](spec/commands/push.md) |
| `ger show` | [spec/commands/show.md](spec/commands/show.md) |

### Stack editing & identifiers

| Command | Spec |
|---------|------|
| `ger edit` | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger reword` | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger fix` | [spec/commands/fix.md](spec/commands/fix.md) |
| `ger rebase` | [spec/commands/rebase.md](spec/commands/rebase.md) |
| `ger sha` | [spec/commands/sha-change-id.md](spec/commands/sha-change-id.md) |
| `ger change-id` | [spec/commands/sha-change-id.md#ger-change-id) |

### Onboarding

| Command | Spec |
|---------|------|
| `ger setup` | [spec/commands/setup.md](spec/commands/setup.md) |
| `ger bash-completion` | [spec/commands/bash-completion.md](spec/commands/bash-completion.md) |

### Developer / debug

| Command | Spec |
|---------|------|
| `ger fetch-api` | [spec/commands/fetch-api.md](spec/commands/fetch-api.md) |
| `ger cache` | [spec/commands/cache.md](spec/commands/cache.md) |

---

## Configuration & setup

- **Config reference:** [Configuration.md](Configuration.md)
- **First-time setup:** [architecture.md#onboarding](architecture.md#onboarding) and root [README.md](../README.md)

Branch-local keys (`branch.*.gerritTarget`, `gerritReviewers`) are documented in [Configuration.md](Configuration.md#branch-local-branchname); set them with `git config`.
