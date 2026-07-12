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

---

## Output

### Text (default)

Stdout:

- `local SHA: …` when the changeish maps to a commit in the working repo
- `Gerrit change: #<n> <triplet> (branch …, status …)` when a Gerrit change was selected

Stderr:

- A dimmed **transparency note** when a bare Change-Id was narrowed (same text as other commands via `format_resolution_note`)

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
| `3` | Gerrit API / git resolution error (not found, missing stack context, auth) |
| `4` | Ambiguous — multiple matches survived narrowing; re-run with a triplet or `change:` number |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar, narrowing, JSON shape
- [`ger show`](show.md) — full commit + Gerrit status view
- [architecture.md](../../architecture.md)
