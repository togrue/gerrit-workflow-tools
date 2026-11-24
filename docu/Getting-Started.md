# Getting started

First-run checklist for using **`ger`** in a Gerrit repository. Complete these steps once per machine (install, global config) and once per clone (hook, branch settings).

**Quick path:** install → `ger setup` (in your clone: it installs the commit-msg hook too) → upstream + branch config → `ger log`.

---

## What you need

| Requirement | Why |
|-------------|-----|
| Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) or pipx | Runs and installs the `ger` CLI |
| A Git clone of a Gerrit-backed repo | `ger` reads git history and config from the current directory |
| Gerrit HTTPS URL and API credentials | REST calls for review state (`ger log`, `ger show`, `ger push` preview) |
| `commit-msg` hook | Gerrit Change-Ids on new commits |
| Upstream tracking branch | Default stack range is `branch@{upstream}..branch` |

---

## 1. Install `ger`

One command, no clone (more ways: [project README](../README.md#install)):

```bash
uv tool install https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger.tar.gz
# or:  pipx install https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger.tar.gz
```

Verify:

```bash
ger --help
```

Not found? uv and pipx put `ger` in `~/.local/bin`; `uv tool update-shell` (or `pipx ensurepath`) adds that to `PATH` for new terminals.

Optional:

- The VS Code extension that matches this ger: `ger vscode install` ([spec](spec/commands/vscode.md)).
- [Bash completion](Completion.md): `ger bash-completion --install`.

To update, run the install command again with `--force`, then `ger vscode install` again.

---

## 2. Configure Gerrit connection

### Interactive (recommended)

```bash
ger setup              # writes to global ~/.gitconfig
# or, for this repo only:
ger setup --local
```

Asks for `ger.webUrl`, `ger.user` and `ger.token` (an HTTP password: Gerrit's web UI, *Settings*, *HTTP Credentials*), asks again when an answer will not do, and checks them with Gerrit before it saves anything:

```text
Signed in as Alice Reviewer <alice@example.com> on https://gerrit.example.com
saved to your global git config: ger.webUrl, ger.user and ger.token
installed Gerrit's commit-msg hook, so each new commit gets a Change-Id: /path/to/clone/.git/hooks/commit-msg
to take it out: rm /path/to/clone/.git/hooks/commit-msg
next: `ger log` shows your stack and its reviews; `ger inbox` the changes waiting for you
```

A test Gerrit on plain `http://` is explained and allowed only on your yes (`ger.allowInsecureHttp`). Without a terminal (scripts, provisioning) give it all as flags:

```bash
ger setup --url https://gerrit.example.com --user YOUR_USERNAME --token-file - < token.txt
```

### Manual

```bash
git config --global ger.webUrl https://gerrit.example.com
git config --global ger.user YOUR_USERNAME
git config --global ger.token YOUR_HTTP_TOKEN
```

Use repo-local config instead of `--global` when settings differ per project.

Full key reference: [Configuration.md](Configuration.md). `ger config` shows the same keys in one screen, grouped by command ([spec](spec/commands/config.md)).

---

## 3. Set up this repository

Run inside your clone:

```bash
# Tracking branch for the default stack range (adjust remote/branch names)
git branch --set-upstream-to origin/main

# Optional: override Gerrit destination branch and default reviewers
git config branch.$(git branch --show-current).gerTarget main
git config branch.$(git branch --show-current).gerReviewers alice,bob
```

| Setting | When to set |
|---------|-------------|
| `branch.<name>.gerTarget` | Push target differs from upstream inference (e.g. feature branch tracks `origin/dev` but pushes to `main`) |
| `branch.<name>.gerReviewers` | Default reviewers on every `ger push` for this branch |

Without an upstream, `ger` asks for one on a terminal (it suggests what the branch was created from). Elsewhere, the error names the command to run, e.g. `git branch --set-upstream-to=origin/main feature`.

---

## 4. The Change-Id hook

Gerrit requires a `Change-Id:` footer on each commit. `ger setup` run inside a clone that has no
`commit-msg` hook installs Gerrit's (from `<ger.webUrl>/tools/hooks/commit-msg`, honouring
`core.hooksPath`) and says so. For another clone, run `ger setup --local` there, or `ger setup`
again: it offers the values you saved as the defaults. `--no-hook` leaves the hooks alone.

After the hook is in place, validate Change-Ids across the current stack (missing and duplicates):

```bash
ger change-id --check
```

---

## 5. Verify the setup

```bash
ger log                  # stack overview vs Gerrit (needs credentials)
ger push --dry-run       # preview what would be pushed
```

| Symptom | Fix |
|---------|-----|
| `ger.webUrl` / auth errors | Run `ger setup` or set keys in [Configuration.md](Configuration.md) |
| No upstream / empty range | `git branch --set-upstream-to=…` |
| Missing Change-Id on commits | Install hook; amend or recommit |
| API 401/403 | Check token and username |

---

## 6. Daily workflow

```text
ger log              → see what needs attention
ger show <ref>       → read comments and votes on one change
ger push             → push the ready prefix of your stack
ger edit / ger rebase → rework commits in the middle of the stack
ger checkout <change> → work on a change from Gerrit, on a branch tracking its target
ger ci               → why CI failed, from the CI logs
ger reply <id> --done → answer a review comment (a draft until you push)
ger inbox            → chains waiting on your review, and your own outgoing ones
ger review <change>  → check one out in the review worktree, then --vote and --done
```

- **The workflows, step by step:** [flows.md](flows.md)
- **Reading `ger log` output:** [Reading-ger-log.md](Reading-ger-log.md)
- **Command reference:** [SPEC.md](SPEC.md)
- **All config keys:** [Configuration.md](Configuration.md)

---

## Optional tuning

| Key | Purpose |
|-----|---------|
| `ger.stopPattern` | Commits whose subject matches this regex start the non-pushable tail (WIP, etc.) |
| `ger.warningPattern` | Highlight suspicious subjects in `ger log` / `ger push` (single regex) |
| `ger.logShowUrl` | Show Gerrit web URLs on each line even without OSC 8 hyperlinks |

See [Configuration.md](Configuration.md) for the full list.
