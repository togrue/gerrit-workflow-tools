# `ger fetch-api`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_fetch_api.py` |
| **Requires** | `ger.webUrl`, credentials |

Developer utility: **GET** one authenticated Gerrit REST path and print JSON.

---

## Usage

```
ger fetch-api [options] PATH
```

`PATH` — under `/a/`, e.g. `changes/12345/detail` or `accounts/self/detail`.

---

A space or other character a URL cannot carry is percent-encoded. A full URL or a `..` segment is refused with exit `2` rather than sent to the server.

---

## Options

| Option | Description |
|--------|-------------|
| `--compact` | Single-line JSON |
| `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |

---

## Exit codes

`0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract.

---

## See also

- [`ger cache`](cache.md)
