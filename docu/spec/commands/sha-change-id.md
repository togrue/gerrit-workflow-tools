# `ger sha` / `ger change-id`

| | |
|--|--|
| **Status** | Implemented |
| **Modules** | `cli_sha.py`, `cli_changeid.py` |
| **Requires** | Git; `--first-attention` / `--next-attention` need `ger.webUrl` + credentials |

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
| `--ai`, `--format {human,ai}` | The same line as without it, colour off; combines with `--short` / `--subject` ([Output formats](../../SPEC.md#output-formats)). `--json` excludes `--short`, `--subject` and `--ai` |
| `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |

Default range (Change-Id lookup): the local stack, `upstream..HEAD` of the working branch along first parents, loaded as `ger log` loads it (`get_stack_snapshot`).

### Exit codes

`0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract.

---

## `ger change-id`

Print or validate Change-Ids.

### Usage

```
ger change-id [options] [REV_OR_RANGE]
```

Default `REV_OR_RANGE`: `HEAD`. Change-Id argument is echoed unchanged. A range prints oldest first, like `ger log`. An empty range prints `(no commits in range)` on stderr and exits `0`. A commit with a missing, invalid or duplicate Change-Id is never dropped silently: the valid Change-Ids are printed, then every problem on stderr exactly as `--check` names it, and the exit code is that of the first.

### Options

| Option | Description |
|--------|-------------|
| `--start-at-remote` | Use `upstream_tip..END` stack window |
| `--check` | Validate **all** commits in the current local stack (`upstream_tip..HEAD`): every commit must have a valid Change-Id footer, and no Change-Id may appear on more than one commit |
| `--fix` | Assign missing Change-Ids on last message line (message-only rewrite via `commit-tree`) |
| `--color`, `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |

`--check` always covers the full current stack (`upstream_tip..HEAD`). A `REV_OR_RANGE` argument is a usage error in this mode. Formerly named `--check-duplicates`. It names every problem it finds, one `error: commit <sha>: …` line each, oldest commit first; the exit code is that of the first line. With none, it prints `N commits, all Change-Ids valid` on a terminal and nothing when piped.

## See also

- [`ger push`](push.md) (Change-Id errors tighten the ready boundary in Gerrit mode)
- [`ger log`](log.md) (surfaces missing and invalid Change-Ids in attention hints)
- [`ger show`](show.md)
