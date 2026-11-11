# `ger resolve`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_resolve.py` |
| **Requires** | `gerrit.webUrl`, credentials (when resolving Gerrit-side identifiers) |

Side-effect-free resolver: report what a **changeish** maps to in the current repo and on Gerrit. Useful for debugging ambiguity and for automation pre-flight checks.

---

## Usage

```
ger resolve <CHANGEISH> [--json]
```

`CHANGEISH` — same shared grammar as every other resolving command (git ref, Change-Id, triplet, `change:<n>`, `refs/changes/…`, URL, `q:…`). Full rules: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

An address that cannot be right exits `3`: a URL for another host or project, a `refs/changes/…` ref with the wrong shard or a patch set the change does not have, or a `change:` value that is not a positive number with no space after the colon.

---

## Output

### Text (default)

Stdout:

- `local SHA: …` when the changeish maps to a commit in the working repo: a git rev, or the **local stack** commit of the selected change (the one that pushes to it; a **sibling** on another branch has none)
- `Gerrit change: #<n> <triplet> (branch …, status …)` when a Gerrit change was selected

Stderr:

- A dimmed **transparency note** when a bare Change-Id was narrowed (same text as other commands via `format_resolution_note`)
- A dimmed note when the selected change is a **sibling** of the local stack commit carrying its Change-Id, which targets another branch (`format_local_sibling_note`; `local_sibling` in JSON)

### `--json`

Prints only the `resolution` object ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)):

```json
{
  "resolution": {
    "input": "…",
    "kind": "change-id",
    "selected": { … },
    "selected_reason": "target-branch",
    "ambiguous": true,
    "alternatives": [ … ],
    "local_sha": "…"
  }
}
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Resolved (including narrowed ambiguous Change-Ids that picked one change) |
| `2` | Usage error (missing `CHANGEISH`, invalid flags) |
| `3` | Not found: the changeish resolves to nothing, or names an address that cannot be right |
| `4` | Ambiguous — multiple matches survived narrowing; re-run with a triplet or `change:` number |
| `5` | Gerrit answered badly or not at all (HTTP, auth, unreachable); never reported as `3` |
| `6` | Required git configuration missing (`gerrit.webUrl`, credentials) |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar, narrowing, JSON shape
- [`ger show`](show.md) — full commit + Gerrit status view
- [architecture.md](../../architecture.md)
