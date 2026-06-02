# `ger fetch-api`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_fetch_api.py` |
| **Requires** | `gerrit.webUrl`, credentials |

Developer utility: **GET** one authenticated Gerrit REST path and print JSON.

---

## Usage

```
ger fetch-api [options] PATH
```

`PATH` — under `/a/`, e.g. `changes/12345/detail` or `accounts/self/detail`.

---

## Options

| Option | Description |
|--------|-------------|
| `--compact` | Single-line JSON |
| `--debug-log`, `-v` | Standard helpers |

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Config or API error |

---

## See also

- [`ger cache`](cache.md)
