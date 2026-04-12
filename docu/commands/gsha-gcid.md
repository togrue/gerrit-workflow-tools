# git gsha / git gcid

**Status:** Implemented

Two complementary identifier-translation commands:

| Command | Direction |
|---------|-----------|
| `git gsha` | Change-Id → commit SHA |
| `git gcid` | commit / SHA / range → Change-Id |

Both operate on local git history (no Gerrit API required).

---

## git gsha

Resolve a Gerrit Change-Id to the corresponding Git commit SHA in the current stack (or a specified range).

### Usage

```
git gsha [--range <rev-range> | --all] [--short | --subject | --json] [-v] <change-id>
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
| `-v`, `--verbose` | Log resolution steps to stderr |

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
git gsha Iabc1234...

# Print short SHA and subject
git gsha --subject Iabc1234...

# Use in a pipeline
git show $(git gsha Iabc1234...)
git checkout $(git gsha Iabc1234...)

# Search entire repo history
git gsha --all Iabc1234...

# JSON output (for scripting)
git gsha --json Iabc1234...
```

---

## git gcid

Return the Change-Id for a commit, SHA, or range of commits.

### Usage

```
git gcid [options] [<commit-or-range>]
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
| `-v`, `--verbose` | Log git commands to stderr |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | No Change-Id found in commit |
| `2` | Usage error or duplicate Change-Id found (with `--check-duplicates`) |

### Examples

```bash
# Get Change-Id of HEAD
git gcid

# Get Change-Id of a specific commit
git gcid a1b2c3d

# Get Change-Ids for a range
git gcid origin/main..HEAD

# Check entire stack for duplicates / missing footers
git gcid --check-duplicates
```

---

## See also

- [`git gedit`](gedit.md) — accepts a Change-Id as the commit argument
- [`git gcomments`](gcomments.md) — fetch Gerrit comments by Change-Id
