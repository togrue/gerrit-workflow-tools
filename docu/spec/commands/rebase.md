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

## V1 scope delta

No changes required for v1.

---

## See also

- [`ger log`](log.md)
- [`ger edit`](edit.md)
