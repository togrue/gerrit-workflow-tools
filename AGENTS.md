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

**Extend the owner.** Every concept has exactly one owning module, listed in the
[module catalog](docu/architecture.md#module-catalog). Search for the *concept*, not for
a name you guessed, and extend its owner. A `_private` helper you need gets promoted and
moved down a layer. Behaviour a second command needs moves into `core/`. The best change
leaves the codebase with fewer ways to do a thing than before.

## Workflow

1. **Locate.** Name the owning module for every concept the task touches. If the task
   does not fit the model, say so and propose the extension before writing code.
2. **Make room.** When the owner lacks the shape you need, or a file you must grow is
   pinned in the size ratchet, land a behaviour-preserving refactor commit first:
   extract, promote, merge copies. Tests stay green; no feature code in it.
3. **Build** the change on top, in its own commit. Test through public entry points (a
   command's `main`, a `core/` function), with `ChangeStore` standing in for Gerrit.
4. **Finish** when ruff, mypy and the full test suite are green and the commit body
   states the net line change in `src/` (from `git diff --stat`).

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

Across the `core/` boundary, pass dataclasses and enums, not tuples or raw REST dicts.
Normalise a REST payload once, in `core/`, and reuse the result everywhere.

## Guardrails

[`tests/test_architecture.py`](tests/test_architecture.py) pins the layer direction, one
owner per concept, and the size ratchets. [`tests/test_docs.py`](tests/test_docs.py) keeps
every link, code identifier and catalog in the docs true to the code. Both run in the
pre-commit hook in about a second.

A red guardrail is a design signal: its message names where the code belongs. Each rule
lists today's debt as known exceptions, tied to an item in the
[consolidation backlog](docu/architecture.md#consolidation-backlog). The lists only
shrink: fixing debt means deleting its entry, and the test goes red on an entry that is
no longer needed. Growing a list or raising a pin is a design decision for the human:
propose it with the reason and wait.

## Cost and caching

Subprocess calls and REST fetches are expensive. Batch them and cache them. A cached
value may be served only when it is known to be valid. The cache is a trust window on
`GerritService`, not a guess. Never widen that window to make something faster, and
never add a cache that cannot say whether it is stale
([ADR-0001](docu/adr/0001-offline-is-a-trust-window-not-a-gerritrest.md)).

## Build and check

```bash
uv sync
if [ -f ./scripts/pytest-on-remote.sh ]; then
  ./scripts/pytest-on-remote.sh
else
  uv run pytest -q
fi
uv run ruff format . && uv run ruff check --fix .
uv run mypy
```

Prefer a test that pins agreement *between* commands over another per-module test.
Copies drift silently when each is only tested against itself.
`tests/test_change_resolution_consistency.py` is the pattern.

## Docs

Docs change in the same commit as the code they describe. When you rename or delete a
symbol, search `docu/`, `CONTEXT.md` and this file for it. When code and spec disagree,
one of them is wrong: fix it. Record a decision that would otherwise be re-litigated as
an ADR.

## Where the answers are

| Question | Read |
|----------|------|
| What does this term mean? | [CONTEXT.md](CONTEXT.md) |
| Which module owns this concept? | [architecture.md § Module catalog](docu/architecture.md#module-catalog) |
| What is still duplicated? | [architecture.md § Consolidation backlog](docu/architecture.md#consolidation-backlog) |
| How should this command behave? | [docu/SPEC.md](docu/SPEC.md), [docu/spec/commands/](docu/spec/commands/) |
| Why is it done this odd way? | [docu/adr/](docu/adr/). Read before "simplifying" |
| What does Gerrit's API return? | [docu/gerrit/md/](docu/gerrit/md/) |
| Which git config keys exist? | [docu/Configuration.md](docu/Configuration.md) |
