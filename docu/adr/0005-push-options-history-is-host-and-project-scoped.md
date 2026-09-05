# Push-options history is keyed by Gerrit host and project

Interactive `ger push` recalls recent push-option lines from a cache file. That
recall must follow the same people who review a given Gerrit project, not
whatever the operator last typed in any clone on the machine.

Independent clones of the same Gerrit project share history. Different projects
(and the same project name on different hosts) do not.

## Considered options

**One global history file** (the previous behaviour) was rejected: reviewers and
topics from unrelated repos pollute the prompt.

**Key by clone path or by git remote name** was rejected: clones of the same
project would not share, and remote names like `origin` collide across repos.

**Key by `gerrit.project` alone** was rejected: the same project path can exist
on more than one Gerrit host; host-scoped cache layout already exists for
`cache.db` and inbox state.

**Migrate the old global file into the current project** was rejected: lines are
not attributable to a project, so copying them would recreate the pollution this
change removes. The orphan `~/.cache/ger/push_options_history.txt` is left
alone.

## Consequences

- History lives at
  `$XDG_CACHE_HOME/ger/<host>/push_options_history/<project-safe>.txt`.
- Stored lines keep reviewers, topic, and `wip`/`private`. Strategy keywords
  (`push` / `lazy` / `overwrite`) are never persisted; a CLI
  `--reviewer-strategy` is merged into the visible prompt for the session only.
- When `gerrit.webUrl` or project identity is missing, history is neither read
  nor written. Empty history prefills from `--reviewers`, then the reviewers
  registry, then `branch.*.gerritReviewers`.
