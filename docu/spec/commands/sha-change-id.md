# `ger sha` / `ger change-id`

| | |
|--|--|
| **Status** | Implemented |
| **Modules** | `cli_sha.py`, `cli_changeid.py` |
| **Requires** | Git only |

Plumbing between Change-Ids and commit SHAs. No Gerrit HTTP.

---

## `ger sha`

Resolve Change-Id → SHA in local history.

### Usage

```
ger sha [options] <change-id>
```

### Options

| Option | Description |
|--------|-------------|
| `--range REV-RANGE` | Search range (mutually exclusive with `--all`) |
| `--all` | All refs in repository |
| `--short` | Abbreviated SHA |
| `--subject` | Short SHA + subject |
| `--json` | `{"change_id", "sha", "subject"}` |
| `--debug-log`, `-v` | Standard helpers |

Default range: configured Gerrit stack window, else upstream..HEAD, else merge-base..HEAD.

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

Default `REV_OR_RANGE`: `HEAD`. Change-Id argument is echoed unchanged.

### Options

| Option | Description |
|--------|-------------|
| `--start-at-remote` | Use `upstream_tip..END` stack window |
| `--check-duplicates` | Scan stack for missing/duplicate Change-Ids |
| `--fix` | Interactive rebase: assign missing Change-Ids on last message line |
| `--color`, `--debug-log`, `-v` | Standard helpers |

### Exit codes (`--check-duplicates`)

| Code | Meaning |
|------|---------|
| `0` | OK |
| `8` | Duplicate Change-Id |
| `9` | Missing Change-Id |

---

## See also

- [`ger push`](push.md) (runs duplicate check in Gerrit mode)
- [`ger show`](show.md)
