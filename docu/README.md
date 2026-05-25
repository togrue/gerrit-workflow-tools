# Gerrit Workflow Tools — Documentation

> **Start here:** [SPEC.md](SPEC.md) — specification index (single source of truth for CLI behavior).

| Topic | Document |
|-------|----------|
| Architecture | [architecture.md](architecture.md) |
| Version 1 product scope | [Version 1 Scope.md](Version%201%20Scope.md) |
| Git configuration | [Configuration.md](Configuration.md) |
| Per-command specs | [spec/commands/](spec/commands/) |
| Bash completion setup | [Completion.md](Completion.md) |
| Testing | [Howto_Test.md](Howto_Test.md) |

---

## Quick concepts

**Local stack** — `upstream_tip..HEAD` (commits above your tracking branch).

**Ready boundary** — first commit matching `gerrit.stopPattern`; earlier commits are the default push set.

**Gerrit target** — server branch for `refs/for/…` (`branch.*.gerritTarget` or inferred upstream on `gerrit.remote`).

Details: [architecture.md](architecture.md).

---

## First-time setup

```bash
git config --global gerrit.webUrl https://gerrit.example.com
# credentials: gerrit.user + gerrit.token (or password)

ger branch init --target main --reviewers alice,bob
ger bash-completion --install   # optional, recommended

ger change-id --check-duplicates
ger log
ger push --dry-run
ger push
```

Commit-msg hook: manual until `ger hooks` (v1.1) — see [Version 1 Scope.md](Version%201%20Scope.md).
