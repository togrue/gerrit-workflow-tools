# AGENTS.md

Instructions for coding agents working on `ger`, a CLI for Gerrit stacked reviews.
Read this in full. Follow the links only when the task touches that area.

## Mindset

**One model of Gerrit state.** There is a single internal representation of a change,
a stack, and its review state. Not one per command. If a command needs a shape the
model does not have, extend the model. Do not build a private one beside it. The core
is meant to outlive the CLI. A long-running local service is the intended direction,
so core must stay callable without a terminal: it never prints, never prompts, and
assumes nothing about living for the length of one command.

**The vocabulary is binding.** *Local stack*, *ready boundary*, *changeish*, *triplet*,
*Gerrit overlay*, *annotated stack*, *trust window*. Each of these means exactly one
thing, in code, tests, docs, commit messages, and conversation. Definitions:
[CONTEXT.md](CONTEXT.md). Do not invent a synonym for a term that already exists. That
is how the same concept ends up implemented twice under two names.

**Show what you can.** A command reports everything it can report and fails only on
what it genuinely cannot do.

**Reuse before writing.** Search for the *concept*, not for a name you guessed. If a
helper you need is `_private`, promote it and move it down a layer. Never copy it. If
you find yourself writing the same behaviour in a second command module, that behaviour
belongs in `core/`.

## Shape

```
cli_ger.py        one entry point; dispatches to commands by name
cli_<command>.py  one file per subcommand; arg parsing and printing only
cli_common.py     shared CLI runtime: ExitCode, run_cli_command, init_cli_runtime, arg groups
core/             the domain: stack, changeish resolution, attention, CI, reviewers
core/gerrit/      talking to Gerrit: the GerritRest seam, GerritService, SQLite cache
render/           shared output formatting
```

Everything two commands could share is shared: argument parsing, the runtime
bootstrap, exit-code mapping, output formatting, and the domain model itself. A command
module should read as *parse args → call core → render*. Logic that survives being moved
into `core/` belongs there.

Fixed obligations on every command:

- Build the runtime with `cli_common.init_cli_runtime`. Do not re-inline it.
- Wrap the body in `cli_common.run_cli_command`. That is the only place errors map to codes.
- Reach Gerrit through `GerritService.from_cwd(...)`. Never construct HTTP yourself.
- Exit with the shared codes in `cli_common.ExitCode`
  ([spec/exit-codes.md](docu/spec/exit-codes.md)). One reason, one code, every command.

## Cost and caching

Subprocess calls and REST fetches are expensive. Batch them and cache them. A cached
value may be served only when it is known to be valid. The cache is a trust window on
`GerritService`, not a guess. Never widen that window to make something faster, and
never add a cache that cannot say whether it is stale
([ADR-0001](docu/adr/0001-offline-is-a-trust-window-not-a-gerritrest.md)).

## Build and check

```bash
uv sync
uv run pytest -q          # unit tests; integration tests are opt-in
uv run ruff format . && uv run ruff check --fix .
uv run mypy
```

Prefer a test that pins agreement *between* commands over another per-module test.
Copies drift silently when each is only tested against itself.
`tests/test_change_resolution_consistency.py` is the pattern.

## Where the answers are

| Question | Read |
|----------|------|
| What does this term mean? | [CONTEXT.md](CONTEXT.md) |
| How should this command behave? | [docu/SPEC.md](docu/SPEC.md), [docu/spec/commands/](docu/spec/commands/) |
| Where does this code belong? | [docu/architecture.md](docu/architecture.md) |
| Why is it done this odd way? | [docu/adr/](docu/adr/). Read before "simplifying" |
| What does Gerrit's API return? | [docu/gerrit/md/](docu/gerrit/md/) |
| Which git config keys exist? | [docu/Configuration.md](docu/Configuration.md) |

When code and spec disagree, one of them is wrong. Fix it. Do not work around it.
Record a decision that would otherwise be re-litigated as an ADR.
