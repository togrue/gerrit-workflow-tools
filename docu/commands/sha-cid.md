# ger sha / ger cid

**Status:** Implemented

Two complementary identifier-translation commands:

| Command | Direction |
|---------|-----------|
| `ger sha` | Change-Id → commit SHA |
| `ger cid` | commit / SHA / range → Change-Id |

Both operate on local git history (no Gerrit API required).

---

## ger sha

Resolve a Gerrit Change-Id to the corresponding Git commit SHA in the current stack (or a specified range).

### Usage

```
ger sha [--range <rev-range> | --all] [--short | --subject | --json] [-v] <change-id>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `change-id` | Gerrit Change-Id to look up (`I` + 40 hex digits) |

### Options

| Option | Description |
|--------|-------------|
| `--range REV-RANGE` | Search this git revision range (e.g. `origin/main..HEAD`) |
| `--all` | Search all commits reachable from any ref in the repository |
| `--short` | Print abbreviated SHA instead of full SHA |
| `--subject` | Print abbreviated SHA followed by commit subject |
| `--json` | Print `{"change_id": …, "sha": …, "subject": …}` |
| `--debug-log` | Log diagnostics to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

`--range` and `--all` are mutually exclusive. `--short`, `--subject`, and `--json` are mutually exclusive.

### Default range

If `--range` is omitted, searches the current Gerrit stack using:
1. Configured Gerrit base range (if available)
2. `branch@{upstream}..HEAD` (if upstream is configured)
3. `<merge-base with target>..HEAD`

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Exactly one match found |
| `1` | Usage error or invalid Change-Id format |
| `2` | No matching commit found |
| `3` | Multiple commits match (duplicate Change-Id) |
| `4` | Git / repository error |

### Examples

```bash
# Print full SHA
ger sha Iabc1234...

# Print short SHA and subject
ger sha --subject Iabc1234...

# Use in a pipeline
git show $(ger sha Iabc1234...)
git checkout $(ger sha Iabc1234...)

# Search entire repo history
ger sha --all Iabc1234...

# JSON output (for scripting)
ger sha --json Iabc1234...
```

---

## ger cid

Return the Change-Id for a commit, SHA, or range of commits.

### Usage

```
ger cid [options] [<commit-or-range>]
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `arg` | `HEAD` | Commit SHA, Change-Id (`I…`), or range (`sha1..sha2`) |

If `arg` is already a valid Change-Id (`I` + 40 hex digits), it is echoed back unchanged.

### Options

| Option | Description |
|--------|-------------|
| `--start-at-remote` | Resolve from the merge-base with the configured Gerrit target branch |
| `--check-duplicates` | Check all commits in the stack for duplicate or missing Change-Ids; exit 0=ok, 1=missing, 2=duplicate |
| `--debug-log` | Log diagnostics to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | No Change-Id found in commit |
| `2` | Usage error or duplicate Change-Id found (with `--check-duplicates`) |

### Examples

```bash
# Get Change-Id of HEAD
ger cid

# Get Change-Id of a specific commit
ger cid a1b2c3d

# Get Change-Ids for a range
ger cid origin/main..HEAD

# Check entire stack for duplicates / missing footers
ger cid --check-duplicates
```

---

## See also

- [`ger edit`](edit.md) — accepts a Change-Id as the commit argument
- [`ger show`](show-todos.md) — Gerrit status and comments for a commit or Change-Id
