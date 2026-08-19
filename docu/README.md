# Gerrit Workflow Tools — Documentation

Navigation hub for **`ger`** docs. Pick a path below instead of hunting through scattered files.

---

## New here?

Start with **[Getting-Started.md](Getting-Started.md)** — install, `ger setup`, upstream, Change-Id hook, and a verification checklist.

Then read **[Reading-ger-log.md](Reading-ger-log.md)** to interpret daily `ger log` output.

---

## User guides

| Guide | Contents |
|-------|----------|
| [Getting-Started.md](Getting-Started.md) | First-run setup (install, config, hook, verify) |
| [Reading-ger-log.md](Reading-ger-log.md) | Columns, tokens, attention hints, summary line |
| [Configuration.md](Configuration.md) | All `gerrit.*` and `branch.*` git config keys |
| [Completion.md](Completion.md) | Bash tab completion install |

---

## Specification (behavior & architecture)

| Document | Role |
|----------|------|
| [SPEC.md](SPEC.md) | **Index** — command registry, conventions (source of truth for CLI behavior) |
| [architecture.md](architecture.md) | System design, shared concepts, module map |
| [../CONTEXT.md](../CONTEXT.md) | Domain vocabulary — binding terms for code, docs and design discussion |
| [adr/](adr/) | Architecture decision records — decisions and, more importantly, why the obvious alternative was rejected |
| [spec/exit-codes.md](spec/exit-codes.md) | Exit-code contract shared by every command |
| [spec/commands/](spec/commands/) | One spec per shipped `ger` command |

When docs and code disagree, fix the code or update the spec — see [SPEC.md](SPEC.md).

Read [adr/](adr/) before "simplifying" something that looks odd — that is what it is for.

---

## Development & testing

| Topic | Location |
|-------|----------|
| Install & unit tests | [../README.md#development](../README.md#development) |
| Integration tests (Docker Gerrit) | [../tests/integration/README.md](../tests/integration/README.md) |
| Implementation plans | [plans/](plans/) |

---

## Quick concepts

**Local stack** — commits in `upstream_tip..HEAD` (above your tracking branch).

**Ready boundary** — first commit whose subject matches `gerrit.stopPattern`; earlier commits are the default push set.

**Gerrit target** — server branch for `refs/for/…` (`branch.*.gerritTarget` or inferred from upstream on `gerrit.remote`).

Details: [architecture.md](architecture.md).

---

## Typical daily flow

```bash
ger log                  # stack vs Gerrit — see Reading-ger-log.md
ger inbox                # chains waiting on your review
ger show <ref>           # one change, full comments
ger push                 # push ready prefix
```

Command details: [SPEC.md](SPEC.md) → [spec/commands/](spec/commands/).
