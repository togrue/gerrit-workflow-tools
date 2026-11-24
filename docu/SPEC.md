# Gerrit Workflow Tools — Specification

**Single source of truth** for CLI behavior. When docs and code disagree, **fix the code or update this spec** — not the other way around.

| Document | Role |
|----------|------|
| This file | Index, conventions, command registry |
| [Getting-Started.md](Getting-Started.md) | First-run setup (install, config, hook) |
| [Reading-ger-log.md](Reading-ger-log.md) | User guide for `ger log` text output |
| [architecture.md](architecture.md) | System design, shared concepts, module map |
| [spec/commands/](spec/commands/) | One spec per shipped `ger` command |
| [Configuration.md](Configuration.md) | Git config keys (referenced by command specs) |

**Operational docs** (not behavioral specs): [Completion.md](Completion.md). **Doc index:** [README.md](README.md).

---

## Conventions

Each command spec lists its **implementation module** (e.g. `cli_log.py`) so reviewers can diff spec ↔ code quickly.

### Global `ger` behavior

- **Entry:** `ger <command> [options]` — see `cli_ger.py` for the authoritative command list.
- **`ger --help`** — lists every registered command once, in sections: *daily commands* (`log`, `show`, `edit` / `reword` on one line, `fix`, `inbox`, `push`, `rebase`, `reply`, `review`, `checkout`, `comment`, `ci`), *setup* (`setup`, `config`, `vscode`, `bash-completion`) and *scripting and agents* (`resolve`, `sha`, `change-id`, `fetch-api`, `serve`, `cache`); then the *common tasks* index (job → command) and notes on running without a terminal. The sections are `_SECTIONS` in `cli_ger.py`.
- **`ger --refresh`** — sets `GER_CACHE_REFRESH=1` for the invoked command (bypass stale Gerrit cache reads).
- **`GER_ASSUME_YES=1`** — answers every confirmation as `-y` does (`ger push`, `ger reply --publish`, `ger review --vote`), for agent sandboxes and scripts that have no terminal. It decides nothing a prompt would not ask: drafts still need `--publish-comments` to go out with a push, a diverged change still needs `--force`, and `GER_NO_PUBLISH` still blocks publishing.
- **`ger --version`** (`-V`) — prints `ger <version>` and exits `0`.
- **Aliases** (not shown in `ger --help`): `changeid` → `change-id`; `restack` / `stack` → `rebase`.
- **Per-command help:** `ger <command> --help` — options there override narrative spec if they differ (then fix the spec).

### Output formats

Every command that prints something worth parsing takes one shared group, `cli_common.add_output_format_args`: `--json`, `--format`, `--ai`, mutually exclusive. `tests/test_output_formats.py` pins that a command with `--json` has the other two.

| Format | Flag | Meaning |
|--------|------|---------|
| human | (default), `--format human` | Colour and hyperlinks per `--color` / `--hyperlinks` |
| json | `--json` | The contract for scripts: every field, `null` where unknown |
| ai | `--ai`, `--format ai` | Text for a model to read: easy to understand, few tokens |

`--ai` means, per command:

- **`ger show`, `ger ci`:** their Markdown view (also `--format markdown`).
- **`ger log`, `ger inbox`:** a compact view of their own, built from the same model and the same words as the human view (`render/ai_text.py`, words from `render/status_fmt.py`): one line per commit or chain; plain words, not the `p`/`n`/`cr+2` columns that need a legend; a fact only when it has a value (no `0 comments`, no empty lists); change numbers instead of URLs; no colour, hyperlinks or alignment padding; the `next:` lines kept.
- **every other command with `--json`** (`comment`, `fix`, `reply`, `resolve`, `review`, `sha`): its human output, with colour and hyperlinks forced off.

Under `--ai` colour and hyperlinks are off on stderr too (notes, the CI download meter), Markdown view or not. Neither `--json` nor `--ai` output ever prompts, terminal or not (`cli_common.may_prompt`): a question takes its non-interactive answer, the same as without a terminal (`ger review` checks out in a worktree, `ger fix` stages nothing, `ger reply --publish` and `ger review --vote` need `-y`, `ger log` reports a missing upstream instead of asking for one).

### A missing upstream

Every command that needs the working branch's upstream asks for it on a terminal, rather than failing, whichever path led there: the **local stack** (`ger log`, `show --stack`), or the stack's **target branch** for a changeish such as `HEAD` (`ger show`, `ci`, `comment`, `fix`, `reply`). It asks once per branch, offering what the branch was created from (else the nearest branch of the Gerrit remote), sets the answer with `git branch --set-upstream-to`, and runs the command again from the start. `cli_common.run_cli_command` does this for every command, on a `core.upstream.NeedsUpstreamError`. Declining exits `1` (`REFUSED`). It never asks under `--json` or `--ai`, under `--dry-run` (which writes nothing), under `ger push -y`, or without a terminal: then the command reports the missing upstream and exits as before (`6`, or `3` when the target branch could not be found). A command that can do without the upstream (`ger show 12345`) never asks.

### Shared concepts

Defined once in [CONTEXT.md](../CONTEXT.md): **local stack**, **ready boundary**, **target branch**, **Change-Id**, **attention**, **patchset status**. The patchset tokens (`p` / `n` / `o` / `-` …) are listed in [Reading-ger-log.md](Reading-ger-log.md#status-columns).

---

## Command registry

Commands listed here are registered in `cli_ger.py` today.

### Core loop

| Command | Spec |
|---------|------|
| `ger log` | [spec/commands/log.md](spec/commands/log.md) |
| `ger inbox` | [spec/commands/inbox.md](spec/commands/inbox.md) |
| `ger push` | [spec/commands/push.md](spec/commands/push.md) |
| `ger show` | [spec/commands/show.md](spec/commands/show.md) |
| `ger ci` | [spec/commands/ci.md](spec/commands/ci.md) — **CI findings** behind failed **CI links** ([design](spec/ci-content.md)) |
| `ger comment` | [spec/commands/comment.md](spec/commands/comment.md) (partial: uploading local drafts and orphans planned) |
| `ger review` | [spec/commands/review.md](spec/commands/review.md) |
| `ger reply` | [spec/commands/reply.md](spec/commands/reply.md) (partial: guided reply planned) |
| `ger serve` | [spec/commands/serve.md](spec/commands/serve.md) — local HTTP service for editor integrations (the VS Code extension) |

### Stack editing & identifiers

| Command | Spec |
|---------|------|
| `ger edit` | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger reword` | [spec/commands/edit.md](spec/commands/edit.md) |
| `ger fix` | [spec/commands/fix.md](spec/commands/fix.md) |
| `ger rebase` | [spec/commands/rebase.md](spec/commands/rebase.md) |
| `ger checkout` | [spec/commands/checkout.md](spec/commands/checkout.md) |
| `ger resolve` | [spec/commands/resolve.md](spec/commands/resolve.md) |
| `ger sha` | [spec/commands/sha-change-id.md](spec/commands/sha-change-id.md) |
| `ger change-id` | [spec/commands/sha-change-id.md](spec/commands/sha-change-id.md#ger-change-id) |

### Onboarding

| Command | Spec |
|---------|------|
| `ger setup` | [spec/commands/setup.md](spec/commands/setup.md) |
| `ger config` | [spec/commands/config.md](spec/commands/config.md) |
| `ger bash-completion` | [spec/commands/bash-completion.md](spec/commands/bash-completion.md) |
| `ger vscode` | [spec/commands/vscode.md](spec/commands/vscode.md) |

### Developer / debug

| Command | Spec |
|---------|------|
| `ger fetch-api` | [spec/commands/fetch-api.md](spec/commands/fetch-api.md) |
| `ger cache` | [spec/commands/cache.md](spec/commands/cache.md) |

---

## Planned commands

Specified but not registered in `cli_ger.py` yet. Move a row into the registry above when the command ships.

| Command | Spec |
|---------|------|
| ger assign | [spec/commands/assign.md](spec/commands/assign.md) |

---

## Configuration & setup

- **First-time setup:** [Getting-Started.md](Getting-Started.md)
- **Config reference:** [Configuration.md](Configuration.md)
- **Reading `ger log`:** [Reading-ger-log.md](Reading-ger-log.md)

Branch-local keys (`branch.*.gerTarget`, `gerReviewers`) are documented in [Configuration.md](Configuration.md#branch-local-branchname); set them with `git config`.
