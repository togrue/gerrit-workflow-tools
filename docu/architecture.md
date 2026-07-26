# Architecture overview

How **`ger`** is structured, where each part lives, and how data flows between layers. Command-level behavior is specified under [spec/commands/](spec/commands/).

---

## Repository layout

| Path | Role |
|------|------|
| `src/gerrit_workflow_tools/` | All application code (Python package) |
| `src/gerrit_workflow_tools/cli_*.py` | One module per shipped command (+ dispatcher) |
| `src/gerrit_workflow_tools/core/` | Domain logic: git stack, config, Gerrit status, REST |
| `src/gerrit_workflow_tools/core/gerrit/` | Gerrit REST client, SQLite cache, service orchestration, change resolution |
| `src/gerrit_workflow_tools/render/` | Terminal formatting for commit rows and status tokens |
| `src/gerrit_workflow_tools/*.py` (package root) | Cross-cutting helpers not tied to a single command |
| `contrib/completion/ger.bash` | Shipped bash completion script |
| `docu/` | User guides, specs, this document |
| `tests/` | Unit tests (default `pytest` run) |
| `tests/integration/` | Docker + real Gerrit tests (opt-in) |
| `scripts/` | Dev helpers (e.g. remote test sync) |

Entry point: `pyproject.toml` registers `ger = gerrit_workflow_tools.cli_ger:main`.

---

## Layer model

```mermaid
flowchart TB
  subgraph entry [Entry]
    ger[cli_ger.py]
  end

  subgraph cli [CLI layer — src/gerrit_workflow_tools/cli_*.py]
    common[cli_common.py · cli_style.py]
    commands[cli_log · cli_show · cli_push · cli_edit · …]
  end

  subgraph presentation [Presentation — render/ + package root]
    render[render/commit_row.py · render/status_fmt.py]
    highlight[summary_highlight.py]
    push_ui[push_input_line.py · push_input_prompt.py]
    rebase_hook[rebase_enricher.py · rebase_sequence_editor.py]
  end

  subgraph core [Core domain — core/]
    stack[stack.py · ready_calc.py · change_id.py]
    status[gerrit_change_status.py · comment_chains.py]
    config[config.py · upstream_interactive.py]
    git[git_run.py]
    reviewers[reviewer.py · reviewer_completion.py]
    push_core[push_reviewers.py · gerrit_show.py]
  end

  subgraph gerrit [Gerrit integration — core/gerrit/]
    resolution[change_resolution.py]
    service[service.py]
    rest[rest.py]
    cache[cache.py · paths.py]
    models[models.py]
  end

  subgraph external [External systems]
    git_repo[(Local git repo)]
    gerrit_api[(Gerrit REST API)]
    sqlite[(SQLite cache)]
  end

  ger --> commands
  commands --> common
  commands --> core
  commands --> presentation
  core --> gerrit
  service --> rest
  service --> cache
  rest --> gerrit_api
  cache --> sqlite
  stack --> git
  config --> git
  git --> git_repo
```

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **Entry** | `cli_ger.py` | Lazy command dispatch, global `--refresh` → `GER_CACHE_REFRESH` |
| **CLI** | `cli_*.py`, `cli_common.py`, `cli_style.py` | Argparse, exit codes, stderr/stdout UX, logging/color init |
| **Presentation** | `render/`, `summary_highlight.py`, push/rebase helpers | Column layout, ANSI tokens, interactive push prompt, `GIT_SEQUENCE_EDITOR` wrappers |
| **Core** | `core/` (except `core/gerrit/`) | Stack math, config, Change-Id rules, status model, git subprocess wrapper |
| **Gerrit integration** | `core/gerrit/` | REST HTTP, caching, batch fetch, changeish resolution, `GerritService` |
| **External** | git, Gerrit server, `~/.cache/ger/` SQLite | Persistent state and I/O |

---

## Information flows

### 1. Command dispatch

```mermaid
sequenceDiagram
  participant User
  participant ger as cli_ger
  participant cmd as cli_&lt;command&gt;
  participant common as cli_common

  User->>ger: ger [--refresh] &lt;command&gt; [args]
  opt --refresh
    ger->>ger: GER_CACHE_REFRESH=1
  end
  ger->>cmd: lazy import + main(args)
  cmd->>common: init_cli_runtime (logging, color, debug)
  cmd->>cmd: run command logic
```

Every command module owns its argparse schema and calls shared helpers from `cli_common.py` (logging, color, git error handling, resolution exit codes).

---

### 2. Configuration and git substrate

Most commands eventually read git config or invoke git:

```mermaid
flowchart LR
  cmd[CLI command]
  config[core/config.py]
  git[core/git_run.py]
  repo[(git repo)]

  cmd --> config
  cmd --> git
  config --> git
  git --> repo
```

`config.py` snapshots `git config --list` once per cwd. Keys like `gerrit.webUrl`, `gerrit.remote`, `branch.*.gerritTarget`, and `gerrit.stopPattern` drive downstream behavior. See [Configuration.md](Configuration.md).

`upstream_interactive.py` prompts the user when a branch lacks `@{upstream}` — used by log, push, edit, rebase, change-id, sha.

---

### 3. Local stack inspection

The **local stack** is commits in `upstream_tip..HEAD` (or an explicit `REV_RANGE`).

```mermaid
flowchart LR
  cmd[cli_log · cli_push · cli_edit · …]
  stack[core/stack.py]
  ready[core/ready_calc.py]
  cid[core/change_id.py]
  git[core/git_run.py]

  cmd --> stack
  cmd --> ready
  stack --> git
  ready --> stack
  push[cli_push] --> cid
  cid --> git
```

| Step | Module | Output |
|------|--------|--------|
| Resolve upstream tip | `stack.upstream_tracking_tip_and_display` | `(sha, display_name)` |
| List commits | `stack.commits_in_range` | `list[Commit]` (sha, subject, body, change_id) |
| Ready boundary | `ready_calc.compute_ready` | push range excluding stop-pattern commits |
| Change-Id validation | `change_id.classify_issues` | missing / duplicate / malformed |

---

### 4. Gerrit overlay (log, show, rebase enrichment)

Read-only commands overlay Gerrit state on local commits:

```mermaid
sequenceDiagram
  participant CLI as cli_log / cli_show / rebase_enricher
  participant Stack as stack.py
  participant Svc as GerritService
  participant Res as change_resolution
  participant REST as rest.py
  participant Cache as cache.py
  participant Status as gerrit_change_status
  participant Render as render/

  CLI->>Stack: commits_in_range → CommitStatusInput rows
  CLI->>Svc: from_cwd() / fetch_gerrit_data(commits)
  Svc->>Res: resolve_stack_context, build_triplet
  Svc->>Cache: get/put change payloads
  Cache-->>Svc: cache hit/miss
  Svc->>REST: batch project:P (change:I OR …) on miss
  REST-->>Svc: ChangeInfo JSON (all branches)
  Note over Svc: alias to target-branch triplets
  Svc->>Status: build_log_commit per row
  Svc->>Status: parallel follow-ups (comments, checks, reviewers)
  Svc-->>CLI: list[LogCommit]
  CLI->>Status: annotate_attention
  CLI->>Render: oneline_body, status tokens
```

**Shared pipeline:** `GerritService.fetch_gerrit_data` is the single enrichment entry point used by `ger log`, `ger show`, and `rebase_enricher`.

**Status model:** `gerrit_change_status.py` defines `LogCommit`, patchset states (`active`, `newer`, `outdated`, `absent`, merged variants), and `determine_attention()`.

**Rendering:** `render/commit_row.py` (full rows for log/show) and `render/status_fmt.py` (compact tokens for rebase todo lines) both consume `LogCommit`.

---

### 5. Changeish resolution

Any input that might mean “a commit or Gerrit change” flows through one core module:

```mermaid
flowchart TB
  input[changeish: git ref · Change-Id · triplet · change:N · URL · q:query]
  classify[change_resolution.classify_changeish]
  stack_ctx[change_resolution.resolve_stack_context]
  resolve[change_resolution.resolve_changeish]
  rest[HttpGerritRest REST queries]
  output[Resolution: local_sha + SelectedChange + note]

  input --> classify
  classify --> resolve
  stack_ctx --> resolve
  resolve --> rest
  resolve --> output
```

| Consumer | Module | Use |
|----------|--------|-----|
| `ger resolve` | `cli_resolve.py` | Inspect resolution only (no side effects) |
| `ger show` | `core/gerrit_show.py` → `resolve_show_commit_row` | Pick one commit row, then enrich |
| `ger fix` | `cli_fix.py` | Map changeish → local SHA for fixup |
| `ger log` | `cli_log.py` | Resolution notes per Change-Id |
| `ger push` | `cli_push.py`, `push_reviewers.py` | Triplet building for REST |
| `stack.resolve_stack_commit` | `core/stack.py` | Map changeish → SHA within current stack |

Stack context (`project`, `target_branch`, `push_branch`) comes from `config.py` + `gerrit_project_id.py`. Contract: [spec/change-and-commit-identifiers.md](spec/change-and-commit-identifiers.md).

---

### 6. Push pipeline

```mermaid
sequenceDiagram
  participant User
  participant Push as cli_push
  participant Ready as ready_calc
  participant CID as change_id
  participant Git as git push
  participant Rev as push_reviewers
  participant Svc as GerritService

  User->>Push: ger push [options]
  Push->>Ready: compute_ready → refspec range
  Push->>CID: classify_issues (duplicate/missing Change-Id)
  Push->>User: confirm / dry-run (push_input_prompt)
  Push->>Git: git push origin HEAD:refs/for/&lt;target&gt;[%opts]
  alt reviewer strategy lazy/overwrite
    Push->>Rev: apply reviewers per change
    Rev->>Svc: REST POST reviewers
  end
```

Push-specific UI lives in `push_input_line.py` (line editor state) and `push_input_prompt.py` (prompt-toolkit). Reviewer catalog/completion: `reviewer_catalog.py`, `core/reviewer_completion.py`.

---

### 7. Git mutation commands

| Command | Flow |
|---------|------|
| **`ger edit` / `ger reword`** | Resolve ref via `stack.resolve_stack_commit` → set `GIT_SEQUENCE_EDITOR=rebase_sequence_editor` → `git rebase -i` |
| **`ger rebase`** | Compute onto ref via `stack.merge_base_with_target` → set `GIT_SEQUENCE_EDITOR=rebase_enricher` → `git rebase -i` (enricher fetches Gerrit data, then opens real editor) |
| **`ger fix`** | `change_resolution.resolve_changeish` → `git commit --fixup` |
| **`ger change-id --fix`** | Subprocess script using `change_id` helpers |
| **`ger push`** | See push pipeline above |

`cli_edit` reuses `cli_log.load_annotated_commits` for `--first-attention-commit` (same attention rules as log).

---

### 8. Cache and refresh

```mermaid
flowchart LR
  refresh[ger --refresh / GER_CACHE_REFRESH]
  svc[GerritService.refresh]
  cache[GerritCache SQLite]
  rest[rest.py HTTP]

  refresh --> svc
  svc --> cache
  svc --> rest
  cache --> rest
```

`ger cache` (`cli_cache.py`) inspects or clears the SQLite DB. Paths: `core/gerrit/paths.py`. TTL/trust window: `GerritService` constructor args.

---

## Module catalog

### CLI commands (`src/gerrit_workflow_tools/`)

| Module | Command | Primary core dependencies |
|--------|---------|---------------------------|
| `cli_ger.py` | *(dispatcher)* | — |
| `cli_log.py` | `ger log` | stack, gerrit_change_status, GerritService, render |
| `cli_show.py` | `ger show` | gerrit_show, GerritService, comment_chains, render |
| `cli_push.py` | `ger push` | ready_calc, change_id, push_reviewers, GerritService |
| `cli_edit.py` | `ger edit`, `ger reword` | stack, cli_log (attention), rebase_sequence_editor |
| `cli_rebase.py` | `ger rebase` | stack, rebase_enricher |
| `cli_fix.py` | `ger fix` | change_resolution, HttpGerritRest |
| `cli_resolve.py` | `ger resolve` | change_resolution, GerritService |
| `cli_sha.py` | `ger sha` | stack, change_id |
| `cli_changeid.py` | `ger change-id` | change_id, stack |
| `cli_setup.py` | `ger setup` | config |
| `cli_cache.py` | `ger cache` | GerritCache |
| `cli_fetch_api.py` | `ger fetch-api` | HttpGerritRest (debug) |
| `cli_bash_completion.py` | `ger bash-completion` | bash_completion_generator |

Shared CLI infrastructure: `cli_common.py` (runtime init, shared argparse), `cli_style.py` (ANSI helpers).

### Core domain (`src/gerrit_workflow_tools/core/`)

| Module | Role |
|--------|------|
| `git_run.py` | Subprocess wrapper for git commands |
| `config.py` | Read/normalize all `gerrit.*` and `branch.*` git config |
| `stack.py` | Stack snapshot, commit ranges, merge-base, changeish→SHA for stack |
| `change_id.py` | Change-Id parse/validate/generate/fix helpers |
| `ready_calc.py` | Ready boundary and push-range computation |
| `gerrit_change_status.py` | `LogCommit` model, patchset status, attention, merged equivalence |
| `comment_chains.py` | Unresolved inline comment threads |
| `gerrit_show.py` | `ger show`-specific commit row resolution |
| `push_reviewers.py` | Post-push reviewer assignment strategies |
| `reviewer.py` | Reviewer account normalization, credentials check |
| `reviewer_completion.py` | Gerrit account search for tab completion |
| `gerrit_project_id.py` | Resolve Gerrit project from config or remote URL |
| `upstream_interactive.py` | Interactive upstream setup when missing |

### Gerrit integration (`src/gerrit_workflow_tools/core/gerrit/`)

| Module | Role |
|--------|------|
| `rest.py` | `HttpGerritRest` — HTTP, auth, batch/chunked GET, parallel helpers |
| `cache.py` | `GerritCache` — SQLite persistence keyed by triplet |
| `service.py` | `GerritService` — cache-aware batch fetch, `fetch_gerrit_data`, sub-APIs |
| `change_resolution.py` | Changeish classification, stack context, ambiguity narrowing |
| `models.py` | Thin dataclass wrappers over REST payloads |
| `paths.py` | Cache DB path and host key |

### Presentation and hooks (package root)

| Module | Role |
|--------|------|
| `render/commit_row.py` | Full commit line layout for log/show |
| `render/status_fmt.py` | Compact status tokens (patchset, CR, verified, comments) |
| `summary_highlight.py` | Subject highlighting from stop/warning patterns |
| `push_input_line.py` | Push confirmation line state machine |
| `push_input_prompt.py` | Interactive push UI (prompt-toolkit) |
| `reviewer_catalog.py` | Reviewer picker data for push prompt |
| `rebase_enricher.py` | `GIT_SEQUENCE_EDITOR` — annotate rebase todo with Gerrit status |
| `rebase_sequence_editor.py` | `GIT_SEQUENCE_EDITOR` — single-line edit for `ger edit` |
| `bash_completion_generator.py` | Generate/install bash completion |

---

## Shared concepts (summary)

### Local stack

Commits in **`upstream_tip..HEAD`**. Resolved in `core/stack.py`. Not the same as “all open Gerrit changes” — only what is local above the tracking branch.

### Gerrit target branch

Server branch for `refs/for/<target>` and merge-base. Precedence: `branch.<name>.gerritTarget` → upstream on `gerrit.remote`.

### Ready boundary

First commit whose subject matches `gerrit.stopPattern`. Logic: `core/ready_calc.py`. Highlighting: `summary_highlight.py`.

### Change identity

Gerrit canonical key: **triplet** `project~branch~Change-Id` (cache PK and follow-up REST paths). Bare Change-Id is ambiguous across branches; the shared resolver narrows with stack context.

**Fetch ≠ identity:** stack overlay batch-loads with compact `project:P (change:I1 OR …)` queries (no `branch:` in the query). Client-side aliasing binds each requested target-branch triplet to the matching `ChangeInfo` row; other-branch duplicates stay cached under their own ids and are ignored for overlay status. Multi-branch transparency notes for `ger log` are derived from the local cache (no per-Change-Id re-query). Remaining overlay cost (reviewer follow-ups, etc.): [plans/gerrit-log-performance.md](plans/gerrit-log-performance.md).

### Patchset status tokens

| Token | Meaning |
|-------|---------|
| `p` / `active` | Local SHA is Gerrit's current patch set |
| `n` / `newer` | Change exists; local commit is ahead of server |
| `o` / `outdated` | Local SHA was uploaded but is not current |
| `-` / `absent` | No Gerrit change for this Change-Id |

Computed in `gerrit_change_status.py`; displayed via `render/`.

### Attention

Commit needs attention when `determine_attention()` finds unresolved comments, CI failure, missing CR+2, chain-blocked state, etc. Drives `ger log` exit code `1`, `ger edit --first-attention-commit`, and rebase enricher annotations.

---

## Duplication and weak separation of concerns

Issues observed in the current codebase. Not blockers, but useful when extending or refactoring.

### 1. Duplicate Change-Id parsing

Two nearly identical “last-line footer” extractors with **different validation strictness**:

| Function | Module | Validation |
|----------|--------|------------|
| `parse_change_id` | `core/stack.py` | Case-insensitive; any `\S+` after `Change-Id:` |
| `extract_change_id_from_msg` | `core/change_id.py` | Strict `I` + 40 lowercase hex |

Used inconsistently: stack/enrichment paths use `parse_change_id`; change-id CLI and `change_resolution` use `extract_change_id_from_msg`. A single canonical parser would avoid subtle mismatches.

### 2. Commands construct their own Gerrit access

`GerritService.from_cwd()` is now the single construction path (`cli_push._service_from_cwd`, a drifting second copy that ignored `GER_CACHE_REFRESH`, was removed).

Remaining friction: every command still *constructs* its own access rather than receiving it, so there is no place to substitute Gerrit. `cli_fix`, `cli_fetch_api`, and `reviewer_catalog` bypass `GerritService` entirely and build an `HttpGerritRest` for one-off REST calls.

`GerritRest` (in `rest.py`) is the seam this is heading toward: single-round-trip Gerrit operations, with batching, aliasing and caching above it. `HttpGerritRest` is currently its only implementation; injecting it into commands is the remaining step.

### 3. CLI-layer coupling

- `cli_edit.py` imports `load_annotated_commits`, `resolve_rev_range` from **`cli_log.py`**. Stack loading and Gerrit enrichment logic lives in a command module instead of core or a shared `core/stack_view.py`.
- `_print_resolution_note` is copy-pasted in `cli_show.py` and `cli_resolve.py`.

### 4. Rev-range resolution in the wrong layer

`resolve_rev_range` and `rev_range_needs_upstream_resolution` live in `cli_log.py` but are needed by edit (and conceptually by any stack-scoped command). Belongs in `core/stack.py` or a small `core/rev_range.py`.

### 5. Split reviewer concerns

Reviewer logic spans four locations:

- `core/gerrit_change_status.py` — `ReviewerAccount` dataclass
- `core/reviewer.py` — normalization from REST payloads
- `core/reviewer_completion.py` — account search API
- `reviewer_catalog.py` (package root) — push-prompt catalog

Consider consolidating under `core/reviewer/` or similar.

### 6. Show resolution vs generic resolution

`core/gerrit_show.py` wraps `change_resolution.resolve_changeish` but also calls `HttpGerritRest.get_change` directly to build a `CommitStatusInput` for remote-only changes. Overlaps with what `ger resolve` already exposes; show-specific row building could sit closer to the status model.

### 7. Presentation depending on CLI styling

`summary_highlight.py` imports `cli_style.py` (ANSI colors). `render/commit_row.py` also depends on `cli_style`. Core-ish highlighting rules (`stopPattern`) are mixed with terminal color concerns — a thin split between “what to highlight” (core/config) and “how to colorize” (presentation) would be cleaner.

### 8. `stack.py` depends on Gerrit REST types

`resolve_stack_commit` accepts an optional `HttpGerritRest` and imports `change_resolution` lazily. Stack inspection is mostly git-only; the Gerrit type hint and changeish branches blur the boundary between **local stack** and **Gerrit resolution** (the latter already lives in `change_resolution.py`).

### 9. Parallel rebase editor modules

`rebase_enricher.py` (full-stack Gerrit annotations) and `rebase_sequence_editor.py` (single-commit edit/reword/drop) are separate entry points invoked via `GIT_SEQUENCE_EDITOR`. Functionally distinct but both are subprocess hooks at package root with no shared module.

### 10. Legacy doc reference

Earlier versions of this document referenced `core/gerrit_client.py`. That module was merged into `core/gerrit/service.py` + `core/gerrit/rest.py`. All high-level fetch paths should go through `GerritService`.

---

## Onboarding

Full checklist: **[Getting-Started.md](Getting-Started.md)**.

Daily flow: `ger log` → `ger show <ref>` → `ger push`. Interpret log output: [Reading-ger-log.md](Reading-ger-log.md).

---

## Spec maintenance

When changing behavior:

1. Update the relevant `spec/commands/<cmd>.md`.
2. Run unit tests; integration tests under `tests/integration/` (optional, Docker).
