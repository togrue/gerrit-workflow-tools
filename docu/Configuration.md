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
| `gerrit.project` | **Gerrit project name** for change resolution and REST calls (e.g. `mygroup/myrepo`). When unset, parsed from the `gerrit.remote` URL. Set this when the remote URL does not encode the project path Gerrit expects, or when you use a mirror/fork whose URL differs from the server project name. Required input for building **triplets** (`project~branch~Change-Id`) used by `ger log`, `ger show`, `ger push`, `ger fix`, and `ger resolve`. |
| `gerrit.scriptsDir` | Project-local root for extension registries (default: `.ger`). Relative paths resolve from the repository toplevel; absolute paths are allowed. Domain scripts live at `<scriptsDir>/<domain>/registry.py` (e.g. `ci`, `ready`, `attention`, `inbox`, `reviewers`). When present, a local registry **replaces** the global cache-dir copy for that domain. |

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

Set via `git config` (or `set_branch_config` in code).

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

Team- or user-owned Python registries customize CI links, ready boundaries, attention rules, and default reviewers. Each domain uses a `registry.py` that exports either `STRATEGIES: dict[str, Callable]` keyed by exact `gerrit.project`, or `get_strategy(project) -> Callable | None`.

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
| CI links | `ci/` | Transform failed Checks / message URLs into `CiLink` rows. Callable: `extract_ci_links(*, project, checks, messages) -> list[CiLink]`. |
| Ready boundary | `ready/` | Choose the pushable stack tip and non-pushable tail highlighting. Callable: `find_ready_boundary(*, commits, stop_pattern, overlay) -> BoundaryResult`. |
| Attention | `attention/` | Override attention reasons (`STRATEGIES` / `get_strategy`) and optional chain blocking (`CHAIN_BLOCK_STRATEGIES` / `get_chain_block_strategy`). |
| Reviewers | `reviewers/` | Default push reviewers when CLI/branch config does not. Callable: `default_reviewers(*, branch, commits, settings) -> list[str]`. |

**Example (CI):** copy [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) to `.ger/ci/` and edit the `STRATEGIES` keys.

With `ger log -v`, failed CI lines use these URLs (OSC 8 when `--hyperlinks` allows). JSON includes `ci_links` alongside `ci_failures` (names only).

---

## See also

- [SPEC.md](SPEC.md) — specification index
- [`ger push`](spec/commands/push.md), [`ger log`](spec/commands/log.md) — command specs
- [`contrib/ger-ci-example`](../contrib/ger-ci-example/) — sample Jenkins → console strategy
