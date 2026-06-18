# Gerrit Workflow Tools — Documentation

> **Start here:** [SPEC.md](SPEC.md) — specification index (single source of truth for CLI behavior).

| Topic | Document |
|-------|----------|
| Architecture | [architecture.md](architecture.md) |
| Git configuration | [Configuration.md](Configuration.md) |
| Per-command specs | [spec/commands/](spec/commands/) |
| Bash completion setup | [Completion.md](Completion.md) |
| Testing | [../README.md#development](../README.md#development) (unit); [../tests/integration/README.md](../tests/integration/README.md) (integration) |

---

## Quick concepts

**Local stack** — `upstream_tip..HEAD` (commits above your tracking branch).

**Ready boundary** — first commit matching `gerrit.stopPattern`; earlier commits are the default push set.

**Gerrit target** — server branch for `refs/for/…` (`branch.*.gerritTarget` or inferred upstream on `gerrit.remote`).

Details: [architecture.md](architecture.md).

---

## First-time setup

Configuration reference: [Configuration.md](Configuration.md) (`gerrit.webUrl`, credentials, defaults, and stop patterns).

```bash
git config --global gerrit.webUrl https://gerrit.example.com
# credentials: gerrit.user + gerrit.token (or password)

git branch --set-upstream-to origin/main    # or your Gerrit remote/branch
git config branch.$(git branch --show-current).gerritTarget main
git config branch.$(git branch --show-current).gerritReviewers alice,bob
ger bash-completion --install   # optional, recommended

ger change-id --check-duplicates
ger log
ger push --dry-run
ger push
```

Commit-msg hook: [README.md → First-time setup: Change-Id hook](../README.md#first-time-setup-change-id-hook).
