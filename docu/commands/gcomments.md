# ger comments

**Status:** Implemented

Fetch and display Gerrit review comments for the current or selected change. Output is optimized for terminal navigation: file paths are printed as `path/to/file.ext:line` so VS Code / terminal Ctrl+click jumps directly to the location.

Requires `gerrit.webUrl` in git config.

---

## Usage

```
ger comments [options] [REF_OR_CHANGE]
```

`REF_OR_CHANGE` — optional git revision, Gerrit change number, Change-Id (`I…`), or query string.

---

## Options

| Option | Description |
|--------|-------------|
| `--whole-chain` | Include all related changes in the Gerrit dependency chain (oldest → newest) |
| `--no-skip-fixups` | Do not skip `fixup!` / `squash!` commits when resolving the Change-Id |
| `--all` | Include resolved/historical comments |
| `--open` | Only strictly unresolved comments (mutually exclusive with `--all`) |
| `--full` | Print full comment text and full commit body (default truncates) |
| `--oneline` | One line per comment: `path:line` + status + first line + link |
| `--json` | Machine-readable JSON to stdout |
| `-v`, `--verbose` | Log resolution steps to stderr |

---

## Default commit selection

Without a positional `REF_OR_CHANGE`, the command walks from `HEAD` toward the merge base and selects the first commit that is **not** a `fixup!` or `squash!` commit. This reflects that review discussion lives on the parent logical change, not on the fixup commit.

Override with `--no-skip-fixups` to use `HEAD`'s Change-Id as-is.

---

## Output (human)

```
commit 8384b6d

  perf: improve status caching

  src/some_script.py:210 - Unresolved Comment
  Link: https://gerrit.example.com/c/project/+/1234/comment/abc_def/
    Alice -- Patchset 3 -- 5:39 PM
      nice refactor, but please add a docstring here
```

Each inline comment includes:
- `path:line` for direct editor navigation
- Resolved/unresolved status
- Author, patchset number, timestamp
- Direct Gerrit URL to the comment thread

---

## JSON output

`--json` emits one document with `changes` array. Each change contains `changeId`, `changeNumber`, `project`, `subject`, `commit`, and `comments`.

Each comment entry: `path`, `line`, `side`, `unresolved`, `patchSet`, `author`, `updated`, `message`, `url`.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Missing Change-Id, auth/API failure, or no matching change |

---

## Configuration

```ini
[gerrit]
    webUrl = https://gerrit.example.com
    user = myuser
    password = mypassword   # or token = <http-password>
```

---

## See also

- [`ger log`](glog.md) — unresolved comment counts across the full stack at a glance
- [`ger sha`](gsha-gcid.md) — resolve a Change-Id to navigate to a specific commit
