# Gerrit Workflow Tools — Specification

**Single source of truth** for CLI behavior, architecture, and release scope. When docs and code disagree, **fix the code or update this spec** — not the other way around.

| Document | Role |
|----------|------|
| This file | Index, conventions, command registry |
| [architecture.md](architecture.md) | System design, shared concepts, module map |
| [Version 1 Scope.md](Version%201%20Scope.md) | Product goals, v1 polish items, deferred features |
| [spec/commands/](spec/commands/) | One spec per `ger` command (implemented behavior) |
| [Configuration.md](Configuration.md) | Git config keys (referenced by command specs) |

**Operational docs** (not behavioral specs): [Howto_Test.md](Howto_Test.md), [Completion.md](Completion.md), [hooks_implementation.md](hooks_implementation.md).

---

## Conventions

### Spec status labels

| Label | Meaning |
|-------|---------|
| **Implemented** | Shipped; spec sections describe current code |
| **Planned** | In [Version 1 Scope](Version%201%20Scope.md) or later; no `cli_*` module yet |
| **Deferred** | Explicitly out of v1; may have notes only |

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

### Core loop (v1 polish tracked in scope doc)

| Command | Status | Spec |
|---------|--------|------|
| `ger log` | Implemented | [spec/commands/log.md](spec/commands/log.md) |
| `ger push` | Implemented | [spec/commands/push.md](spec/commands/push.md) |
| `ger show` | Implemented | [spec/commands/show.md](spec/commands/show.md) |

### Stack editing & identifiers

| Command | Status | Spec |
|---------|--------|------|
| `ger edit` | Implemented | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger reword` | Implemented | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger fix` | Implemented | [spec/commands/fix.md](spec/commands/fix.md) |
| `ger rebase` | Implemented | [spec/commands/rebase.md](spec/commands/rebase.md) |
| `ger sha` | Implemented | [spec/commands/sha-change-id.md](spec/commands/sha-change-id.md) |
| `ger change-id` | Implemented | [spec/commands/sha-change-id.md#ger-change-id) |

### Branch & onboarding

| Command | Status | Spec |
|---------|--------|------|
| `ger branch` | Implemented | [spec/commands/branch.md](spec/commands/branch.md) |
| `ger bash-completion` | Implemented | [spec/commands/bash-completion.md](spec/commands/bash-completion.md) |

### Developer / debug

| Command | Status | Spec |
|---------|--------|------|
| `ger fetch-api` | Implemented | [spec/commands/fetch-api.md](spec/commands/fetch-api.md) |
| `ger cache` | Implemented | [spec/commands/cache.md](spec/commands/cache.md) |

### Planned (v1 scope, not implemented)

| Command | Status | Spec |
|---------|--------|------|
| `ger assign` | Planned | [spec/commands/assign.md](spec/commands/assign.md) |

### Deferred (see Version 1 Scope)

`ger hooks`, `ger checkout`, `ger status` (superseded by `log` + `show`), standalone `ger move` (folded into planned `ger assign`).

---

## Configuration & setup

- **Config reference:** [Configuration.md](Configuration.md)
- **First-time setup:** [architecture.md#onboarding](architecture.md#onboarding) and root [README.md](../README.md)
