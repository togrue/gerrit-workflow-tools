# `ger rebase`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_rebase.py`, `rebase_enricher.py` |
| **Requires** | Gerrit enrichment: `gerrit.webUrl` + credentials |

Start **`git rebase -i`** with Gerrit status annotations on each `pick` line (patchset, Verified, CR, comments, attention note).

**Aliases:** `ger restack`, `ger stack` → `rebase`.

---

## Usage

```
ger rebase [options] [REV]
```

`REV` — base commit, Change-Id, or ref (default: merge-base with target). Not used with `--onto-remote`.

### Change resolution

`REV` is resolved by `resolve_stack_changeish`, the same path as [`ger fix`](fix.md) and
[`ger edit`](edit.md), so `change:<n>`, `refs/changes/…` refs, URLs and `q:` queries now work
here too — they previously failed with "cannot resolve without a Gerrit client".

One difference, deliberate: `ger rebase` does **not** require its target to be in the local
stack. `REV` is the commit to rebase *from*, which normally sits below the stack, whereas
`ger fix` / `ger edit` / `ger reword` rewrite a commit *in* it. Grammar:
[change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

---

## Options

| Option | Description |
|--------|-------------|
| `--onto-remote` | Rebase onto fetched `refs/remotes/<gerrit.remote>/<target>` tip |
| `--no-onto-remote` | Force merge-base behavior (overrides `gerrit.rebaseOntoRemote`) |
| `--drop-merged-equivalent` | Mark provably merged-equivalent commits as `drop` in todo |
| `--debug-log`, `-v` | Standard helpers |

Editor: enricher delegates to `GIT_EDITOR` / `core.editor` / `VISUAL` / `EDITOR`.

---

## Configuration

| Key | Default | Effect |
|-----|---------|--------|
| `gerrit.rebaseOntoRemote` | off | Default for `--onto-remote` |
| `gerrit.rebaseDropMergedEquivalent` | off | Default for `--drop-merged-equivalent` |

---

## See also

- [`ger log`](log.md)
- [`ger edit`](edit.md)
