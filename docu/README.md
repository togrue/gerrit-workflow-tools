# Gerrit Workflow Tools — Documentation

Local git helpers for Gerrit stacked review workflows. Each command is invokable as `git <name>` after installation (see [root README](../README.md) for install instructions).

---

## Core concepts

**Local stack** — commits reachable from `HEAD` but not from the configured base branch / merge-base target. This is the unit of work for all commands.

**Ready boundary** — the first commit in the stack whose subject matches a configured stop pattern (e.g. `^dropme!`, `^TODO\b`, `^test!`). Commits before it are pushable by default; commits from it onward are blocked unless explicitly overridden.

**Gerrit target branch** — stored per local branch in git config (`branch.<name>.gerritTarget`). Required for any push or Gerrit-API command. Set with `git gbranch init --target <branch>`.

**Change-Id** — the Gerrit footer (`Change-Id: I…`) in each commit message. All commands depend on this being present and unique within the stack. Validate with `git gcid --check-duplicates` (or list footers with `git gcid --start-at-remote`).

---

## Commands

### Implemented

| Command | Purpose |
|---------|---------|
| [`git gbranch`](commands/gbranch.md) | Manage branch-local Gerrit metadata (target, reviewers, push mode) |
| [`git gpush`](commands/gpush.md) | Push the ready prefix (or full stack) to Gerrit |
| [`git gedit`](commands/gedit.md) | Edit, reword, or drop a commit in the middle of the stack (interactive rebase) |
| [`git gshow`](commands/gedit.md) | Show the status of the checked out commit, it's change-id or sha |
| [`git gcomments`](commands/gcomments.md) | Fetch and display Gerrit review comments for the current or selected change |
| [`git gsha` / `git gcid`](commands/gsha-gcid.md) | Translate between Change-Ids and commit SHAs; `gcid --check-duplicates` validates the stack |
| [`git glog`](commands/glog.md) | Compact actionable overview of the commit chain vs Gerrit (CI, votes, comments) |

### Planned (not yet implemented)

| Command | Purpose |
|---------|---------|
| `git gstatus` | Full status overview: stack + ready boundary + Change-Id check + Gerrit comment counts |
| `git gmove` | Move all changes in the current stack to a different Gerrit target branch |
| `git gfixup` | Commit as a fixup for a specific Change-Id in the stack |
| `git gshow` | Show Gerrit status (votes, comments, CI) for a single commit or Change-Id |

---

## Configuration

**Full reference:** [Configuration.md](Configuration.md) — all `gerrit.*` keys, `gerrit.glog*`, `gerrit.gpush*`, branch-local keys, and `gerrit.stopPattern` (repeatable regexes for the ready boundary).

### Global (`~/.gitconfig` or repo `.git/config`)

```ini
[gerrit]
    remote = origin
    webUrl = https://gerrit.example.com
    defaultPushMode = ready
    stopPattern = ^dropme!
    stopPattern = ^TODO\b
    stopPattern = ^test!
```

`gerrit.webUrl` is **required** for any command that contacts the Gerrit REST API (`gcomments`, `glog`, `gpush --show-attributes`, …).

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
    gerritPushMode = ready
```

Set with `git gbranch init --target <branch>` or individual `git gbranch set-*` subcommands.

---

## Typical first-time setup

```bash
# 1. Set global Gerrit server URL
git config --global gerrit.webUrl https://gerrit.example.com

# 2. Configure current branch
git gbranch init --target main --reviewers alice,bob

# 3. Inspect the stack vs Gerrit (optional)
git glog

# 4. Validate Change-Ids before pushing
git gcid --check-duplicates

# 5. Push the ready prefix
git gpush --dry-run
git gpush
```

---

## Implementation phases

| Phase | Status | Commands |
|-------|--------|---------|
| 1 — local only | Done | `gbranch`, `gpush`, `gedit`, `gsha`, `gcid` |
| 2 — Gerrit navigation | In progress | `gcomments` (done), `glog` (done), `gstatus` (planned) |
| 3 — Gerrit mutation | Planned | `gmove` |

---

## See also

- [Workflow scenarios](../Gerrit-Workflow-Scenarios.md) — the real-world problems these tools solve
- [MVP feature list](../MVP.md)
- [Testing guide](Howto_Test.md)
