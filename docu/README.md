# Gerrit Workflow Tools — Documentation

Local helpers for Gerrit stacked review workflows. After installation, run **`ger <command>`** (see [root README](../README.md)).

---

## Core concepts

**Local stack** — commits reachable from `HEAD` but not from the configured base branch / merge-base target. This is the unit of work for all commands.

**Ready boundary** — the first commit in the stack whose subject matches a configured stop pattern (e.g. `^dropme!`, `^TODO\b`, `^test!`). Commits before it are pushable by default; commits from it onward are blocked unless explicitly overridden.

**Gerrit target branch** — optional per-branch override in git config (`branch.<name>.gerritTarget`). The value is the **branch name on the Gerrit server** (e.g. `main`, `dev`), not a made-up local branch name like `origin/dev`. When unset, `ger push` infers the destination from your upstream if it tracks `gerrit.remote` (default `origin`). Tools need the effective target ref to exist locally for merge-base work—usually as a local branch or as `refs/remotes/origin/…` after `git fetch`. Set an explicit override with `ger branch init --target <branch>` or `ger branch set-target` when it must differ from upstream.

**Change-Id** — the Gerrit footer (`Change-Id: I…`) in each commit message. All commands depend on this being present and unique within the stack. Validate with `ger cid --check-duplicates` (or list footers with `ger cid --start-at-remote`).

---

## Commands

### Implemented

| Command | Purpose |
|---------|---------|
| [`ger branch`](commands/branch.md) | Manage branch-local Gerrit metadata (target, reviewers) |
| [`ger push`](commands/push.md) | Push the ready prefix (or full stack) to Gerrit |
| [`ger edit`](commands/edit.md) | Edit, reword, or drop a commit in the middle of the stack (interactive rebase) |
| [`ger show`](commands/show-todos.md) | Show Gerrit status (votes, comments, CI) for a single commit or Change-Id |
| [`ger sha` / `ger cid`](commands/sha-cid.md) | Translate between Change-Ids and commit SHAs; `ger cid --check-duplicates` validates the stack |
| [`ger log`](commands/log.md) | Compact actionable overview of the commit chain vs Gerrit (CI, votes, comments) |

### Planned (not yet implemented)

| Command | Purpose |
|---------|---------|
| `ger status` | Full status overview: stack + ready boundary + Change-Id check + Gerrit comment counts |
| `ger move` | Move all changes in the current stack to a different Gerrit target branch |
| `ger fixup` | Commit as a fixup for a specific Change-Id in the stack |

---

## Configuration

**Full reference:** [Configuration.md](Configuration.md) — all `gerrit.*` keys, `gerrit.log*`, `gerrit.push*`, `gerrit.lastPushedBranch` (`ger push` local marker branch), branch-local keys, `gerrit.stopPattern` (ready boundary), and `gerrit.warningPattern` (summary highlighting).

### Global (`~/.gitconfig` or repo `.git/config`)

```ini
[gerrit]
    remote = origin
    webUrl = https://gerrit.example.com
    stopPattern = ^dropme!
    stopPattern = ^TODO\b
    stopPattern = ^test!
```

`gerrit.webUrl` is **required** for any command that contacts the Gerrit REST API (`log`, `show`, `push` with `gerrit.pushShowAttributes`, …).

Authentication for the REST API:

```ini
[gerrit]
    user = myuser
    password = mypassword   # or token = <http-password>
```

### Branch-local

```ini
[branch "feature/my-work"]
    gerritTarget = main
    gerritReviewers = alice,bob
```

Set with `ger branch init --target <branch>` or individual `ger branch set-*` subcommands.

---

## Typical first-time setup

```bash
# 1. Set global Gerrit server URL
git config --global gerrit.webUrl https://gerrit.example.com

# 2. Configure current branch
ger branch init --target main --reviewers alice,bob

# 3. Inspect the stack vs Gerrit (optional)
ger log

# 4. Validate Change-Ids before pushing
ger cid --check-duplicates

# 5. Push the ready prefix
ger push --dry-run
ger push
```

---

## Implementation phases

| Phase | Status | Commands |
|-------|--------|---------|
| 1 — local only | Done | `branch`, `push`, `edit`, `sha`, `cid` |
| 2 — Gerrit navigation | In progress | `log` (done), `show` (done), `status` (planned) |
| 3 — Gerrit mutation | Planned | `move` |

---

## See also

- [Bash completion](Completion.md) — `ger bash-completion` / `--install` for `~/.bashrc`
- [Testing guide](Howto_Test.md)
