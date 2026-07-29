# Getting started

First-run checklist for using **`ger`** in a Gerrit repository. Complete these steps once per machine (install, global config) and once per clone (hook, branch settings).

**Quick path:** install → `ger setup` → upstream + branch config → commit-msg hook → `ger log`.

---

## What you need

| Requirement | Why |
|-------------|-----|
| Python 3.11+ (see project `pyproject.toml`) | Runs the `ger` CLI |
| A Git clone of a Gerrit-backed repo | `ger` reads git history and config from the current directory |
| Gerrit HTTPS URL and API credentials | REST calls for review state (`ger log`, `ger show`, `ger push` preview) |
| `commit-msg` hook | Gerrit Change-Ids on new commits |
| Upstream tracking branch | Default stack range is `branch@{upstream}..branch` |

---

## 1. Install `ger`

From the [project README](../README.md#install):

```bash
pip install --user .
# or:  pipx install .
# or:  uv pip install --user .
```

Ensure the install directory is on `PATH` (e.g. `~/.local/bin` on Linux/macOS). Verify:

```bash
ger --help
```

Optional but recommended: [bash completion](Completion.md) — `ger bash-completion --install`.

---

## 2. Configure Gerrit connection

### Interactive (recommended)

```bash
ger setup              # writes to global ~/.gitconfig
# or, for this repo only:
ger setup --local
```

Prompts for `gerrit.webUrl`, `gerrit.user`, and `gerrit.token` (HTTP access token from Gerrit user settings).

### Manual

```bash
git config --global gerrit.webUrl https://gerrit.example.com
git config --global gerrit.user YOUR_USERNAME
git config --global gerrit.token YOUR_HTTP_TOKEN
```

Use repo-local config instead of `--global` when settings differ per project.

Full key reference: [Configuration.md](Configuration.md).

---

## 3. Set up this repository

Run inside your clone:

```bash
# Tracking branch for the default stack range (adjust remote/branch names)
git branch --set-upstream-to origin/main

# Optional: override Gerrit destination branch and default reviewers
git config branch.$(git branch --show-current).gerritTarget main
git config branch.$(git branch --show-current).gerritReviewers alice,bob
```

| Setting | When to set |
|---------|-------------|
| `branch.<name>.gerritTarget` | Push target differs from upstream inference (e.g. feature branch tracks `origin/dev` but pushes to `main`) |
| `branch.<name>.gerritReviewers` | Default reviewers on every `ger push` for this branch |

If `ger log` errors about missing upstream, set it with `git branch --set-upstream-to=<remote>/<branch>` or follow the interactive prompt.

---

## 4. Install the Change-Id hook

Gerrit requires a `Change-Id:` footer on each commit. Install once per clone:

```bash
HOOK_URL="$(git config --get gerrit.webUrl)/tools/hooks/commit-msg"

curl -sfL -o .git/hooks/commit-msg "$HOOK_URL" \
  || wget -q -O .git/hooks/commit-msg "$HOOK_URL"

chmod +x .git/hooks/commit-msg
```

If `gerrit.webUrl` is not set yet, run step 2 first.

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
| `gerrit.webUrl` / auth errors | Run `ger setup` or set keys in [Configuration.md](Configuration.md) |
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
```

- **Reading `ger log` output:** [Reading-ger-log.md](Reading-ger-log.md)
- **Command reference:** [SPEC.md](SPEC.md)
- **All config keys:** [Configuration.md](Configuration.md)

---

## Optional tuning

| Key | Purpose |
|-----|---------|
| `gerrit.stopPattern` | Commits whose subject matches this regex start the non-pushable tail (WIP, etc.) |
| `gerrit.warningPattern` | Highlight suspicious subjects in `ger log` / `ger push` (single regex) |
| `gerrit.logShowUrl` | Show Gerrit web URLs on each line (default on) |

See [Configuration.md](Configuration.md) for the full list.
