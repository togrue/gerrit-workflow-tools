# `ger cache`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_cache.py` |
| **Requires** | `gerrit.webUrl` (host key for cache path) |

Inspect or clear the **local SQLite Gerrit API cache** (`core/gerrit/cache.py`) and the **CI content** store beside it (`core/ci_content_store.py`).

Use global **`ger --refresh`** to bypass cache for one command invocation.

---

## Usage

```
ger cache <subcommand>
```

| Subcommand | Action |
|------------|--------|
| `info` | Print host, path, row counts (changes, comments, checks), CI content entries |
| `clear` | Delete cached payloads and CI content for this Gerrit host |

---

## See also

- [architecture.md](../../architecture.md#8-cache-and-refresh)
- [`ger fetch-api`](fetch-api.md)
