# Configuration reference

Git config drives defaults for Gerrit workflow tools. Values are read from repo `.git/config`, global `~/.gitconfig`, etc. (standard Git precedence).

**Convention:** keys live under the `gerrit` section unless noted. Boolean values accept `true`, `1`, `yes`, or `on` (case-insensitive). Command-line flags override these defaults when present.

---

## Global (`[gerrit]`)

| Key | Purpose |
|-----|---------|
| `gerrit.remote` | Remote name for `git push` to Gerrit (default: `origin`). |
| `gerrit.webUrl` | HTTPS base for Gerrit REST API and web links (required for API commands). |
| `gerrit.user` | Username for HTTP Basic auth to the REST API. |
| `gerrit.password` / `gerrit.token` | Password or HTTP access token (token preferred). |
| `gerrit.stopPattern` | Regex matched against **commit subject** (first line only in practice). The first matching commit starts the non-pushable tail unless `ger push --all` applies. If unset, built-in default: `^(?:dropme!|todo\b|test!|wip\b)` (case-insensitive). Override with `git config gerrit.stopPattern '…'`. |
| `gerrit.warningPattern` | Regex matched against commit subject for warning highlighting in `ger log`, `ger push`, `ger show`, and `ger inbox` when color output is enabled. If unset, built-in default: `(?:^[^\s]+$|(?i:\b(?:wip|todo)\b))`. Stop-pattern highlighting takes precedence when both match the same text span. Override with `git config gerrit.warningPattern '…'`. |
| `gerrit.wrapWidth` | Max visible width for `ger show` commit-message lines and comment-box text (default `120`). Comment bodies and context lines break on spaces and tabs; a token with no space is sliced. An over-long path header keeps its tail (the filename). The chain URL is not wrapped. Unset, blank, or a value below 1 uses 120. |
| `gerrit.project` | **Gerrit project name** for change resolution and REST calls (e.g. `mygroup/myrepo`). When unset, parsed from the `gerrit.remote` URL. Set this when the remote URL does not encode the project path Gerrit expects, or when you use a mirror/fork whose URL differs from the server project name. Required input for building **triplets** (`project~branch~Change-Id`) used by `ger log`, `ger show`, `ger push`, `ger fix`, and `ger resolve`. |
| `gerrit.scriptsDir` | Project-local root for extension registries (default: `.ger`). Relative paths resolve from the repository toplevel; absolute paths are allowed. Domain scripts live at `<scriptsDir>/<domain>/registry.py` (e.g. `ci`, `ready`, `attention`, `inbox`, `reviewers`). When present, a local registry **replaces** the global cache-dir copy for that domain. |

---

## Attention cursor (`ger.*` and branch-local)

Walk state for iterative edit-attention rework (`ger edit`, and read-only queries on `ger show` / `ger sha`). Identity is a **Change-Id**, not a SHA. History is append-only under `.git/ger/attention-history.jsonl` (not git config).

| Key | Effect |
|-----|--------|
| `branch.<name>.gerAttentionCursor` | Change-Id of the last attention-walk stop on that branch. |
| `branch.<name>.gerAttentionCursorAt` | UTC timestamp (`…Z`) when the cursor was last written. |
| `ger.lastAttentionChangeId` | Repo-level last walk Change-Id for **lazy adopt** when a new branch has no cursor but the Change-Id is in its stack (e.g. Gerrit download). |
| `ger.lastAttentionChangeIdAt` | UTC timestamp for `ger.lastAttentionChangeId`. |
| `ger.attentionCursorTtl` | Max age of a usable cursor (default `14d`; also accepts `7d`, `2w`, `48h`, …). On expiry the cursor is always cleared (no prompt); recover via `ger edit --cursor-history` and `--set-cursor`. |

---

## Change identity (triplet resolution)

Commands that talk to Gerrit resolve each footer **Change-Id** to Gerrit's canonical **triplet** (`project~branch~Change-Id`, the `ChangeInfo.id` string). Two git config keys supply the `project` and `branch` parts:

| Key | Role in triplet |
|-----|-----------------|
| `gerrit.project` | **Project** segment — explicit override; otherwise parsed from `gerrit.remote` |
| `branch.<name>.gerritTarget` | **Branch** segment — Gerrit **destination branch** for the current working branch (same branch used in `refs/for/<branch>` on push) |

Resolution order for the branch segment matches push: `branch.<current>.gerritTarget` wins; otherwise the upstream ref on `gerrit.remote` (default `origin`) is used. If either segment cannot be resolved, commands fail with a clear configuration error rather than picking a change on the wrong branch.

A bare Change-Id may match several Gerrit changes (e.g. the same patch pushed to `main` and `dev`). The shared resolver narrows to the change on your configured destination branch and reports alternatives — see [spec/change-and-commit-identifiers.md](spec/change-and-commit-identifiers.md).

---

## `ger log` — `gerrit.log*`

| Key | Effect |
|-----|--------|
| `gerrit.logShowUrl` | When `true`, include Gerrit URLs in text output even without OSC 8 hyperlinks (same as `--url` / `--show-url`). Compact `Open in gerrit` links are shown by default when hyperlinks are on. |
| `gerrit.logShowChangeId` | Default on: append Change-Id on each text line (`--show-change-id`). |

---

## `ger push` — `gerrit.push*` and related

| Key | Effect |
|-----|--------|
| `gerrit.pushShowAttributes` | When `true`, include Gerrit reviewer / wip / private preview on the push preview (requires `gerrit.webUrl` and credentials). Default off when unset. |
| `gerrit.lastPushedBranch` | Default on: after a **successful** `ger push`, create or move the local branch `lastPush/<current-branch-name>` to the commit that was pushed (the same tip as in the refspec). Set `false` to skip. |
| `gerrit.push.remotePolicy` | Before push: fetch/check that `HEAD` is linear on the remote Gerrit target tip. Disable per-invocation with `ger push --no-rebase-check`. |

---

## `ger review` — `ger.review*`

| Key | Effect |
|-----|--------|
| `ger.reviewCheckout` | `worktree` (a reusable review worktree; the default) or `branch` (in the current checkout). When unset, `ger review` asks on first use and writes the answer to the local config; without a terminal it uses the worktree. |
| `ger.reviewWorktree` | Path of the review worktree. Relative paths resolve from the main worktree's parent. Default: `<repository>-review` beside the main worktree. |
| `ger.reviewStage` | Default on: stage the reviewed change's diff (HEAD at its parent). `false` checks the `change-<number>` branch out instead. |

---

## `ger show` and `ger ci` — CI content

| Key | Effect |
|-----|--------|
| `ger.showCiContent` | When `true`, `ger show` fetches the CI logs behind failed **CI links** and attaches their **CI findings** (same as `--ci`; `--no-ci` overrides). Default off: fetching logs is expensive. |
| `ger.ciCredential.<host>` | Secret a **CI content provider** sends to `<host>` (the link URL's hostname, lowercase, no port), e.g. `git config --global ger.ciCredential.ci.example.org bot:apitoken`. `user:token` is HTTP Basic (Jenkins API tokens); anything else is a Bearer token. Used by `ger ci` and `ger show --ci`. |

---

## `ger inbox` — `inbox.*`

Host-scoped (no clone required). See [spec/commands/inbox.md](spec/commands/inbox.md).

| Key | Effect |
|-----|--------|
| `inbox.requireVerified` | When `true` (default), the *to review* query requires `label:<verifiedLabel>+1`. |
| `inbox.verifiedLabel` | CI label name for that gate (default `Verified`). |
| `inbox.projects` | Default `--project` list, comma-separated. Unset means every project on the host. |
| `inbox.toReviewQuery` | Replace the *to review* query entirely (verified gate is then your problem). |
| `inbox.limit` | Default `--limit` (positive integer). |

---

## ger edit

| Key | Effect |
|-----|--------|
| `gerrit.editNextShow` | When `true`, `ger edit --next` runs `ger show` on the commit it moved to after that rebase exits 0. Unset is off: `--next` only moves. `ger reword --next` ignores this key. Show's attention exit does not replace a successful rebase; other show failures are returned as-is. |
| `ger.attentionCursorTtl` | Max age of a usable attention cursor (default `14d`). See [Attention cursor](#attention-cursor-ger-and-branch-local). |

`ger config` shows these keys, and the rest of this page, in one screen. See [spec/commands/config.md](spec/commands/config.md).

---

## `ger rebase` — `gerrit.rebase*`

| Key | Effect |
|-----|--------|
| `gerrit.rebaseOntoRemote` | Default for `ger rebase --onto-remote` (rebase onto `refs/remotes/<gerrit.remote>/<target>`). |
| `gerrit.rebaseDropMergedEquivalent` | Default for `ger rebase --drop-merged-equivalent`. |

---

## Branch-local (`branch.<name>.*`)

| Key | Effect |
|-----|--------|
| `branch.<name>.gerritTarget` | **Optional override** for the Gerrit **destination branch** (e.g. `main`, `dev`). When unset, `ger push`, `ger rebase`, and change resolution infer the destination from `@{upstream}` if its remote name matches `gerrit.remote` (default `origin`). When set, it wins for push, merge-base, `ger rebase --onto-remote`, and **triplet building** when resolving a bare Change-Id (see [Change identity](#change-identity-triplet-resolution)). The value must resolve to an existing ref—typically a local branch of that name or `refs/remotes/<remote>/<branch>` after `git fetch` on `gerrit.remote`. If the tool reports that the target is missing locally, fetch from the remote first; do not create a local branch literally named `origin/<branch>`—that is the remote-tracking name space, not a branch you should create by hand. |
| `branch.<name>.gerritReviewers` | Comma-separated accounts; merged into `ger push` ref options. |
| `branch.<name>.gerAttentionCursor` / `…CursorAt` | See [Attention cursor](#attention-cursor-ger-and-branch-local). |

Set via `git config` (or `set_branch_config` / attention-cursor helpers in `core/config_write.py`).

---

## Examples

```ini
[gerrit]
    remote = origin
    webUrl = https://gerrit.example.com
    user = me
    token = secret
    stopPattern = ^(?:dropme!|WIP:|hold:)
    warningPattern = (?i:\\bwip\\b)
    logShowUrl = true
    pushShowAttributes = true
    lastPushedBranch = true
```

```bash
# Custom stop pattern for this repo
git config gerrit.stopPattern '^(?:dropme!|hold:)'

# Show Gerrit URLs by default in ger log
git config gerrit.logShowUrl true
```

---

## Extension scripts (`.ger` and `~/.config/ger`)

Team- or user-owned Python registries customize CI links, ready boundaries, attention rules, and default reviewers. Each domain uses a `registry.py` that exports `STRATEGIES: dict[str, Callable]` keyed by exact `gerrit.project`. Optional `get_strategy(project) -> Callable | None` supports prefix-style matching when many repos share one implementation.

### Resolution order

1. **Project-local** — `<gerrit.scriptsDir>/<domain>/registry.py` (default scriptsDir: `.ger`)
2. **Global** — `$XDG_CONFIG_HOME/ger/<host>/<domain>/registry.py` (default `~/.config/ger/<host>/`)
3. **Built-in** — hardcoded defaults (subject stop pattern, attention thresholds, etc.)

Per tier: if `registry.py` exists but fails to import, the command **fails** (no silent fallback). If the file loads but has no entry for `gerrit.project`, the next tier is tried. If a callable **raises at runtime**, the next tier is tried, then built-in.

Red subject highlighting in `ger log`, `ger push`, and `ger show` uses the same ready-boundary rules as push (not a separate stop-pattern regex). `gerrit.warningPattern` still controls yellow warning highlights.

`ger cache info` prints the global scripts root (`~/.config/ger/<host>/`) next to the SQLite cache path.

### Domains

| Domain | Path segment | Role |
|--------|--------------|------|
| CI links | `ci/` | Transform failed Checks / message URLs into `CiLink` rows. Callable: `extract_ci_links(*, project, checks, messages) -> list[CiLink]`. Built-in Jenkins parsing applies when no registry matches; `messages` may be lazily fetched — see [gerrit-ci-strategies.md](gerrit-ci-strategies.md). Optionally also `CONTENT_PROVIDERS`: **CI content providers** for `ger ci` / `ger show --ci`, matched by URL rather than project — see [CI content providers](gerrit-ci-strategies.md#ci-content-providers). |
| Ready boundary | `ready/` | Choose the pushable stack tip and non-pushable tail highlighting. Callable: `find_ready_boundary(*, commits, stop_pattern, overlay) -> BoundaryResult`. |
| Attention | `attention/` | Override attention reasons (`STRATEGIES` / `get_strategy`) and optional chain blocking (`CHAIN_BLOCK_STRATEGIES` / `get_chain_block_strategy`). |
| Reviewers | `reviewers/` | Default push reviewers when CLI/branch config does not. Callable: `default_reviewers(*, branch, commits, settings) -> list[str]`. |

**Example (CI):** built-in behaviour needs no registry. Copy [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) to `.ger/ci/` only for custom URL transforms — see [gerrit-ci-strategies.md](gerrit-ci-strategies.md).

With `ger log -v`, failed CI lines use these URLs (OSC 8 when `--hyperlinks` allows). JSON includes `ci_links` alongside `ci_failures` (names only).

---

## See also

- [SPEC.md](SPEC.md) — specification index
- [`ger push`](spec/commands/push.md), [`ger log`](spec/commands/log.md) — command specs
- [`contrib/ger-ci-example`](../contrib/ger-ci-example/) — sample Jenkins → console strategy
