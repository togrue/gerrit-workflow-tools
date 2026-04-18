# `ger show` — deferred / follow-up work

Items intentionally **out of v1**; track here until promoted into the main spec or implementation.

## Diff and patch output

- **`--stat`** — file change stats (`git show --stat` for local commits).
- **`--patch` / `-p`** — full patch (`git show -p`).
- Optional git config **`gerrit.gshowIncludeDiff`** (`stat` | `patch` | `none`) for a persistent default once flags exist.

**Rule of thumb:** only apply when a **local** commit exists; Change-Id-only / no checkout should print a one-line hint or skip.

## UX / workflow

- **`--next-attention-commit`** — align with `edit` so `show` can target the same “next attention” commit as the rebase workflow.

## Docs

- Full command doc (e.g. `docu/commands/show.md`) mirroring [`log.md`](log.md) once behavior stabilizes.
