# Configuration reference

Git config drives defaults for Gerrit workflow tools. Values are read from repo `.git/config`, global `~/.gitconfig`, etc. (standard Git precedence).

**Convention:** every key ger owns is `ger.<key>`, or `branch.<name>.ger<Key>` for a branch. Git's own keys (`branch.<name>.remote`, `branch.<name>.merge`, `core.editor`) are only read. Boolean values accept `true`, `1`, `yes`, or `on` (case-insensitive). Command-line flags override these defaults when present.

---

## Global (`[ger]`)

| Key | Purpose |
|-----|---------|
| `ger.remote` | Remote name for `git push` to Gerrit (default: `origin`). |
| `ger.webUrl` | HTTPS base for Gerrit REST API and web links (required for API commands). Must start with `https://`: every REST call carries the Basic-auth credentials, so a plain `http://` URL is refused with a config error unless `ger.allowInsecureHttp` is set. |
| `ger.allowInsecureHttp` | When `true`, accept an `http://` `ger.webUrl` (credentials then travel in cleartext). Default off. Only for a local test Gerrit, such as the one in `testenv/` or the integration tests. |
| `ger.user` | Username for HTTP Basic auth to the REST API. |
| `ger.password` / `ger.token` | Password or HTTP access token (token preferred). |
| `ger.stopPattern` | Regex matched against **commit subject** (first line only in practice). The first matching commit starts the non-pushable tail unless `ger push --all` applies. If unset, built-in default: `^(?:dropme!|todo\b|test!|wip\b)` (case-insensitive). Override with `git config ger.stopPattern '…'`. |
| `ger.warningPattern` | Regex matched against commit subject for warning highlighting in `ger log`, `ger push`, `ger show`, and `ger inbox` when color output is enabled. If unset, built-in default: `(?:^[^\s]+$|(?i:\b(?:wip|todo)\b))`. Stop-pattern highlighting takes precedence when both match the same text span. Override with `git config ger.warningPattern '…'`. |
| `ger.wrapWidth` | Max visible width for `ger show` commit-message lines and comment-box text (default `120`). Comment bodies and context lines break on spaces and tabs; a token with no space is sliced. An over-long path header keeps its tail (the filename). The chain URL is not wrapped. Unset, blank, or a value below 1 uses 120. |
| `ger.project` | **Gerrit project name** for change resolution and REST calls (e.g. `mygroup/myrepo`). When unset, parsed from the `ger.remote` URL. Set this when the remote URL does not encode the project path Gerrit expects, or when you use a mirror/fork whose URL differs from the server project name. Required input for building **triplets** (`project~branch~Change-Id`) used by `ger log`, `ger show`, `ger push`, `ger fix`, and `ger resolve`. |
| `ger.scriptsDir` | Project-local root for extension registries (default: `.ger`). Relative paths resolve from the repository toplevel; absolute paths are allowed. Domain scripts live at `<scriptsDir>/<domain>/registry.py` (e.g. `ci`, `ready`, `attention`, `reviewers`). When present, a local registry **replaces** the global cache-dir copy for that domain. Setting it in the repository's own config (`git config ger.scriptsDir .ger`) also **trusts** the registries there; see [Trusting project registries](#trusting-project-registries). |

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
| `ger.project` | **Project** segment — explicit override; otherwise parsed from `ger.remote` |
| `branch.<name>.gerTarget` | **Branch** segment — Gerrit **destination branch** for the current working branch (same branch used in `refs/for/<branch>` on push) |

Resolution order for the branch segment matches push: `branch.<current>.gerTarget` wins; otherwise the upstream ref on `ger.remote` (default `origin`) is used. If either segment cannot be resolved, commands fail with a clear configuration error rather than picking a change on the wrong branch.

A bare Change-Id may match several Gerrit changes (e.g. the same patch pushed to `main` and `dev`). The shared resolver narrows to the change on your configured destination branch and reports alternatives — see [spec/change-and-commit-identifiers.md](spec/change-and-commit-identifiers.md).

---

## `ger log` — `ger.log*`

| Key | Effect |
|-----|--------|
| `ger.logShowUrl` | When `true`, include Gerrit URLs in text output even without OSC 8 hyperlinks (same as `--url` / `--show-url`). Compact `Open in gerrit` links are shown by default when hyperlinks are on. |
| `ger.logShowChangeId` | Default on: append Change-Id on each text line (`--show-change-id`). |

---

## `ger push` — `ger.push*` and related

| Key | Effect |
|-----|--------|
| `ger.pushShowAttributes` | When `true`, include Gerrit reviewer / wip / private preview on the push preview (requires `ger.webUrl` and credentials). Default off when unset. |
| `ger.pushPublishDrafts` | Default on: when you have drafts on the changes a push updates, the push options line starts with `publish-drafts`, so Enter pushes and publishes them. Set `false` to start it without, so Enter keeps them as drafts; typing `publish-drafts` still publishes. |
| `ger.lastPushedBranch` | Default on: after a **successful** `ger push`, create or move the local branch `lastPush/<current-branch-name>` to the commit that was pushed (the same tip as in the refspec). Set `false` to skip. |
| `ger.pushRemotePolicy` | Before push: fetch/check that `HEAD` is linear on the remote Gerrit target tip. Disable per-invocation with `ger push --no-rebase-check`. |

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

## `ger inbox` — `ger.inbox*`

Host-scoped (no clone required); inside a clone it is narrowed to that clone's project unless `--all` is given. See [spec/commands/inbox.md](spec/commands/inbox.md).

| Key | Effect |
|-----|--------|
| `ger.inboxRequireVerified` | When `true` (default), the *to review* query requires `label:<verifiedLabel>+1`. |
| `ger.inboxVerifiedLabel` | CI label name for that gate (default `Verified`). |
| `ger.inboxProjects` | Default `--project` list outside a clone, comma-separated. Unset means every project on the host. Inside a clone, the clone's own project is the default instead. |
| `ger.inboxToReviewQuery` | Replace the *to review* query entirely (verified gate is then your problem). |
| `ger.inboxLimit` | Default `--limit` (positive integer). |

---

## ger edit

| Key | Effect |
|-----|--------|
| `ger.showAfterEdit` | Default `true`: `ger edit --next` and `ger edit --first-attention-commit` run `ger show` on the commit they stopped at, after that rebase exits 0. `false`: they only move. `ger edit REV` and `ger reword` never show. Show's attention exit does not replace a successful rebase; other show failures are returned as-is. |
| `ger.attentionCursorTtl` | Max age of a usable attention cursor (default `14d`). See [Attention cursor](#attention-cursor-ger-and-branch-local). |

`ger config` shows these keys, and the rest of this page, in one screen. See [spec/commands/config.md](spec/commands/config.md).

---

## `ger serve` — `ger.eventStream*`

Gerrit's **event stream** (`ssh … gerrit stream-events`), which `ger serve` follows so the
editor refreshes when review activity happens. Optional: it needs ssh, a key Gerrit accepts and
the *Stream Events* capability. Without it everything works as before, by polling. See
[spec/commands/serve.md](spec/commands/serve.md#gerrits-event-stream).

| Key | Effect |
|-----|--------|
| `ger.eventStream` | `auto` (default): try once at startup, quietly; when it never comes up, poll. `on`: keep retrying and log each failure (still polling meanwhile). `off`: never connect. `true`/`false` read as `on`/`off`. |
| `ger.eventStreamHost` | ssh host of Gerrit. Default: the host of the `ger.remote` URL when it is an ssh URL, else the host of `ger.webUrl`. |
| `ger.eventStreamPort` | ssh port. Default: the port in an ssh remote URL (none: ssh's own config decides), else Gerrit's `29418`. |
| `ger.eventStreamUser` | ssh user. Default: the user in an ssh remote URL, else `ger.user`, else ssh's own config. |

ssh runs non-interactively (`BatchMode=yes`, no tty, 10-second connect timeout): it never asks
for a password or to accept a host key. Connect once by hand (`ssh -p 29418 you@host gerrit
version`) to accept the host key, and make sure your account has the *Stream Events* global
capability (or is an administrator).

---

## `ger rebase` — `ger.rebase*`

| Key | Effect |
|-----|--------|
| `ger.rebaseOntoRemote` | Default for `ger rebase --onto-remote` (rebase onto `refs/remotes/<ger.remote>/<target>`). |
| `ger.rebaseDropMergedEquivalent` | Default for `ger rebase --drop-merged-equivalent`. |

---

## Branch-local (`branch.<name>.*`)

| Key | Effect |
|-----|--------|
| `branch.<name>.gerTarget` | **Optional override** for the Gerrit **destination branch** (e.g. `main`, `dev`). When unset, `ger push`, `ger rebase`, and change resolution infer the destination from `@{upstream}` if its remote name matches `ger.remote` (default `origin`). When set, it wins for push, merge-base, `ger rebase --onto-remote`, and **triplet building** when resolving a bare Change-Id (see [Change identity](#change-identity-triplet-resolution)). The value must resolve to an existing ref—typically a local branch of that name or `refs/remotes/<remote>/<branch>` after `git fetch` on `ger.remote`. If the tool reports that the target is missing locally, fetch from the remote first; do not create a local branch literally named `origin/<branch>`—that is the remote-tracking name space, not a branch you should create by hand. |
| `branch.<name>.gerReviewers` | Comma-separated accounts; merged into `ger push` ref options. |
| `branch.<name>.gerAttentionCursor` / `…CursorAt` | See [Attention cursor](#attention-cursor-ger-and-branch-local). |

Set via `git config` (or `set_branch_config` / attention-cursor helpers in `core/config_write.py`).

---

## Renamed keys (`gerrit.*`, `inbox.*`, `branch.<name>.gerrit*`)

Older versions read their settings from three prefixes. Each such key now lives under `ger`:

| Old key | New key |
|---------|---------|
| `gerrit.<key>` (e.g. `gerrit.webUrl`, `gerrit.token`, `gerrit.scriptsDir`) | `ger.<key>` |
| `gerrit.push.remotePolicy` | `ger.pushRemotePolicy` |
| `inbox.<key>` (e.g. `inbox.limit`) | `ger.inbox<Key>` (e.g. `ger.inboxLimit`) |
| `branch.<name>.gerritReviewers` | `branch.<name>.gerReviewers` |
| `branch.<name>.gerritTarget` | `branch.<name>.gerTarget` |

The new key is read first. When only the old one is set, it still answers, and the command
prints one warning line per key naming the `git config` commands that move it (at the scope
where it is set). `ger setup` and `ger config` write only new keys and remove the old name
at the scope they write. The one table lives in `core/config.py` (`LEGACY_KEYS`).

---

## Colour

Every command that colours takes `--color WHEN` (`always` | `auto` | `never`, default `auto`), decided once per run for stdout and stderr (`render/style.py` `init_color_mode`). `auto` colours only a terminal, and not at all while the environment variable `NO_COLOR` is set to anything non-empty ([no-color.org](https://no-color.org/)). `--color=always` and `--color=never` win over both.

---

## Examples

```ini
[ger]
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
git config ger.stopPattern '^(?:dropme!|hold:)'

# Show Gerrit URLs by default in ger log
git config ger.logShowUrl true
```

---

## Extension scripts (`.ger` and `~/.config/ger`)

Team- or user-owned Python registries customize CI links, ready boundaries, attention rules, and default reviewers. Each domain uses a `registry.py` that exports `STRATEGIES: dict[str, Callable]` keyed by exact `ger.project`. Optional `get_strategy(project) -> Callable | None` supports prefix-style matching when many repos share one implementation.

### Resolution order

1. **Project-local** — `<ger.scriptsDir>/<domain>/registry.py` (default scriptsDir: `.ger`)
2. **Global** — `$XDG_CONFIG_HOME/ger/<host>/<domain>/registry.py` (default `~/.config/ger/<host>/`)
3. **Built-in** — hardcoded defaults (subject stop pattern, attention thresholds, etc.)

The project-local tier runs only when trusted; an untrusted one is skipped, as if absent. See [Trusting project registries](#trusting-project-registries).

Per tier: if `registry.py` exists but fails to import, the command **fails** (no silent fallback). If the file loads but has no entry for `ger.project`, the next tier is tried. If a callable **raises at runtime**, the next tier is tried, then built-in.

Red subject highlighting in `ger log`, `ger push`, and `ger show` uses the same ready-boundary rules as push (not a separate stop-pattern regex). `ger.warningPattern` still controls yellow warning highlights.

`ger cache info` prints the global scripts root (`~/.config/ger/<host>/`) next to the SQLite cache path.

### Trusting project registries

A `registry.py` is Python that ger imports, so it runs as you and can read everything you can, `ger.token` included. A project-local one arrives with the work tree: a change you check out to review, or a repository you clone, can add `.ger/ci/registry.py`. Git does not clone hooks for this reason, and ger does not run a project-local registry just because it is there. It runs when one of these holds:

1. **`ger.scriptsDir` is set where a checkout cannot write.** In the repository's own config (`git config ger.scriptsDir .ger`: `.git/config` is never cloned), a worktree's config, or `git -c`. Set in global or system config, it trusts only a directory outside the work tree (an absolute path to your own scripts); a relative or in-tree path there would trust every repository you open.
2. **You said you trust its contents.** Otherwise, before a command runs, ger shows the path and asks `Trust it and run it?` (default no). A yes records a SHA-256 digest of every `*.py` file in that registry directory (`registry.py` and the helpers it imports, symlinked directories followed under every name that reaches them; a registry of more than 2000 such files is refused) in `$XDG_CONFIG_HOME/ger/trusted-registries.json` (default `~/.config/ger/`). The same contents are then trusted wherever they appear, every worktree of the project included. Any edit changes the digest, so ger asks again before running a changed registry. ger reads those files once, hashes them, and runs exactly those bytes: it never runs bytecode from `__pycache__` or a `.pyc`, nor imports anything from the registry directory that is not part of the digest.

Without a terminal, or with `--json` / `--ai`, ger does not ask: it skips the untrusted registry, falls back to the global tier and built-in behaviour, and prints `warning: not running untrusted <path>` with how to trust it. `GER_ASSUME_YES` does not answer this question: an agent must not trust a checked-out change's code for you. A declined registry is asked about again on the next run. `ger serve` asks, or warns, once when it starts; trust given later takes effect when it restarts.

The global tier (`~/.config/ger/<host>/`) is yours and always runs. To revoke trust, delete the entry, or the file.

### Domains

| Domain | Path segment | Role |
|--------|--------------|------|
| CI links | `ci/` | Transform failed Checks / message URLs into `CiLink` rows. Callable: `extract_ci_links(*, project, checks, messages) -> list[CiLink]`. Built-in Jenkins parsing applies when no registry matches; `messages` may be lazily fetched — see [gerrit-ci-strategies.md](gerrit-ci-strategies.md). Optionally also `CONTENT_PROVIDERS`: **CI content providers** for `ger ci` / `ger show --ci`, matched by URL rather than project — see [CI content providers](gerrit-ci-strategies.md#ci-content-providers). |
| Ready boundary | `ready/` | Choose the pushable stack tip and non-pushable tail highlighting. Callable: `find_ready_boundary(*, commits, stop_pattern, overlay) -> BoundaryResult`. |
| Attention | `attention/` | Override attention reasons (`STRATEGIES` / `get_strategy`) and optional chain blocking (`CHAIN_BLOCK_STRATEGIES` / `get_chain_block_strategy`). |
| Reviewers | `reviewers/` | Default push reviewers when CLI/branch config does not. Callable: `default_reviewers(*, branch, commits, settings) -> list[str]`. |

**Example (CI):** built-in behaviour needs no registry. Copy [`example/hooks/ci/`](../example/hooks/ci/) to `.ger/ci/` only for custom URL transforms or a custom **CI content provider** — see [gerrit-ci-strategies.md](gerrit-ci-strategies.md) and [example/hooks/](../example/hooks/).

With `ger log -v`, failed CI lines use these URLs (OSC 8 when `--hyperlinks` allows). JSON includes `ci_links` alongside `ci_failures` (names only).

---

## See also

- [SPEC.md](SPEC.md) — specification index
- [`ger push`](spec/commands/push.md), [`ger log`](spec/commands/log.md) — command specs
- [`example/hooks/`](../example/hooks/) — copyable `.ger` registry examples (CI strategy + content provider)
