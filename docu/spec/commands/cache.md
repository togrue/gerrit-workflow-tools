# `ger cache`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_cache.py` |
| **Requires** | `gerrit.webUrl` (host key for cache path) |

Inspect or clear the **local SQLite Gerrit API cache** (`core/gerrit/cache.py`).

Use global **`ger --refresh`** to bypass cache for one command invocation.

---

## Usage

```
ger cache <subcommand>
```

| Subcommand | Action |
|------------|--------|
| `info` | Print host, path, row counts (changes, accounts, comments) |
| `clear` | Delete cached payloads for this Gerrit host |

---

## See also

- [architecture.md](../../architecture.md#gerrit-api-access)
- [`ger fetch-api`](fetch-api.md)
