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
| [flows.md](flows.md) | The review, rework and local-review workflows step by step: CLI command, VS Code command, tests, gaps |
| [SPEC.md](SPEC.md) | **Index** — command registry, conventions (source of truth for CLI behavior) |
| [architecture.md](architecture.md) | System design, shared concepts, module map |
| [../CONTEXT.md](../CONTEXT.md) | Domain vocabulary — binding terms for code, docs and design discussion |
| [adr/](adr/) | Architecture decision records — decisions and, more importantly, why the obvious alternative was rejected |
| [spec/exit-codes.md](spec/exit-codes.md) | Exit-code contract shared by every command |
| [spec/commands/](spec/commands/) | One spec per shipped `ger` command |
| [spec/commands/serve.md](spec/commands/serve.md) | `ger serve`: the local HTTP service behind the VS Code extension in `vscode/` |
| [spec/ci-content.md](spec/ci-content.md) | Design: **CI content** behind **CI links** (`ger ci`, opt-in `ger show --ci`) |
| [gerrit-ci-strategies.md](gerrit-ci-strategies.md) | Custom **CI strategies** / **CI content providers**; copyable samples in [`example/hooks/`](../example/hooks/) |

When docs and code disagree, fix the code or update the spec — see [SPEC.md](SPEC.md).

Read [adr/](adr/) before "simplifying" something that looks odd — that is what it is for.

---

## Development & testing

| Topic | Location |
|-------|----------|
| Install & unit tests | [../README.md](../README.md) (Development) |
| Integration tests (Docker Gerrit) | [../tests/integration/README.md](../tests/integration/README.md) |
| Coding agents | [../AGENTS.md](../AGENTS.md) |

---

## Concepts

Every term (*local stack*, *ready boundary*, *target branch*, *triplet*, …) is defined once, in [../CONTEXT.md](../CONTEXT.md).

---

## Typical daily flow

```bash
ger log                  # stack vs Gerrit — see Reading-ger-log.md
ger inbox                # chains waiting on your review
ger show <ref>           # one change, full comments
ger ci                   # why CI failed: findings from the CI logs
ger push                 # push ready prefix

ger review <change>      # check out someone's change in the review worktree
ger comment app.py:42 -m "..."   # comment on a line (a draft until you send it)
ger review --vote CR+1   # send your comments with the vote
ger review --done        # leave the review
```

Every step of the review, rework and local-review flows: [flows.md](flows.md).

Command details: [SPEC.md](SPEC.md) → [spec/commands/](spec/commands/).
