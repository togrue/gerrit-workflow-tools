# `ger sha` / `ger change-id`

| | |
|--|--|
| **Status** | Implemented |
| **Modules** | `cli_sha.py`, `cli_changeid.py` |
| **Requires** | Git; `--first-attention` / `--next-attention` need `gerrit.webUrl` + credentials |

Plumbing between Change-Ids and commit SHAs. Plain Change-Id lookup is local only; attention targeting uses the annotated stack (Gerrit).

---

## `ger sha`

Resolve Change-Id → SHA in local history, or print the SHA of an edit-attention target.

### Usage

```
ger sha [options] <change-id>
ger sha --first-attention
ger sha -n / --next-attention [CHANGEISH]
```

### Options

| Option | Description |
|--------|-------------|
| `--first-attention` | SHA of the oldest stack commit with edit attention (read-only; no cursor write) |
| `-n` / `--next-attention` | SHA of the next edit-attention commit tip-ward of the cursor (or of CHANGEISH as AFTER) |
| `--range REV-RANGE` | Search range (mutually exclusive with `--all`; incompatible with attention flags) |
| `--all` | All refs in repository |
| `--short` | Abbreviated SHA |
| `--subject` | Short SHA + subject |
| `--json` | `{"change_id", "sha", "subject"}` |
| `--debug-log`, `-v` | Standard helpers |

Default range (Change-Id lookup): configured Gerrit stack window, else upstream..HEAD, else merge-base..HEAD.

### Exit codes

Shared table: **[exit-codes.md](../exit-codes.md)**.

| Code | Meaning |
|------|---------|
| `0` | Exactly one match |
| `2` | Usage / invalid Change-Id |
| `3` | Not found |
| `7` | Git error |
| `8` | Duplicate Change-Id |

---

## `ger change-id`

Print or validate Change-Ids.

### Usage

```
ger change-id [options] [REV_OR_RANGE]
```

Default `REV_OR_RANGE`: `HEAD`. Change-Id argument is echoed unchanged. A range prints oldest first, like `ger log`. An empty range prints `(no commits in range)` on stderr and exits `0`.

### Options

| Option | Description |
|--------|-------------|
| `--start-at-remote` | Use `upstream_tip..END` stack window |
| `--check` | Validate **all** commits in the current local stack (`upstream_tip..HEAD`): every commit must have a valid Change-Id footer, and no Change-Id may appear on more than one commit |
| `--fix` | Assign missing Change-Ids on last message line (message-only rewrite via `commit-tree`) |
| `--color`, `--debug-log`, `-v` | Standard helpers |

`--check` always covers the full current stack (`upstream_tip..HEAD`). A `REV_OR_RANGE` argument is a usage error in this mode. Formerly named `--check-duplicates`.

### Exit codes (`--check`)

Shared table: **[exit-codes.md](../exit-codes.md)**.

| Code | Meaning |
|------|---------|
| `0` | OK — all stack commits have unique, valid Change-Ids |
| `8` | Duplicate Change-Id |
| `9` | Missing (or invalid) Change-Id |

---

## See also

- [`ger push`](push.md) (Change-Id errors tighten the ready boundary in Gerrit mode)
- [`ger log`](log.md) (surfaces missing Change-Id in attention hints)
- [`ger show`](show.md)
