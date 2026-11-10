# `ger ci`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_ci.py` |
| **Requires** | `gerrit.webUrl`, credentials; `ger.ciCredential.<host>` when the CI server needs auth |
| **Design** | [ci-content.md](../ci-content.md) — layering, result shape, caching |

Why CI failed, without opening a console: for each target's failed **CI links**, fetch the **CI content** behind the link and print its **CI findings**, grouped by link label. This is the deliberate, expensive hop; `ger log` and the **Gerrit overlay** never fetch logs.

---

## Usage

```
ger ci [options] [CHANGEISH …]
```

Targets resolve exactly as in [`ger show`](show.md): each `CHANGEISH` is a changeish or an `A..B` / `A...B` range; `--stack` adds the local stack; with neither, `HEAD`. Links come from the same **CI strategy** as `ger log` (`ci_links`), so only changes whose Verified is −1 have any.

---

## Options

| Option | Description |
|--------|-------------|
| `--stack` | Include the local stack (`upstream_tip..HEAD`); prompts for a missing upstream like `ger show --stack` |
| `--branch NAME` | Branch for stack/upstream context (default: working branch) |
| `-v` / `--verbose` | After each link's findings, print the last 60 log lines |
| `--full` | After each link's findings, print the whole log (for searching the raw text) |
| `--json` | JSON payload — the stable contract for agents |
| `--format {human,markdown}` / `--ai` | Output format; `--ai` is Markdown |
| `--color`, `--hyperlinks`, `--debug-log` | Standard helpers |

---

## Output

**Human:** per target, the `ger show` headline, then per link `label  url  (build, cached, log truncated)` and one line per finding: `tool  path:line[:col]  message`, with pytest's `E` lines indented beneath. A target without failed links prints `(no failed CI links)`, `(not on Gerrit)`, or names the failed checks that had no link.

**Markdown (`--ai`):** `## <sha> — <subject>`, `### CI findings`, `#### <label>` per label, a bullet per finding; a fenced log with `-v` / `--full`.

**JSON:** always `{ "commits": [ … ] }`, one object per target:

```json
{
  "sha": "…", "change_id": "I…", "summary": "…", "pushed": true, "verified": -1,
  "gerrit_url": "…", "ci_failures": ["…"],
  "ci_content": [
    {
      "label": "jenkins", "url": "https://ci/…/55/console", "source": "message",
      "provenance": {
        "url": "…", "provider": "jenkins", "build": "Widget #55", "fetched_at": "2026-11-09T10:00:00Z",
        "truncated": false, "cache_verdict": "body", "cached": false, "error": null
      },
      "findings": [
        {"tool": "pytest", "kind": "failed", "path": "demo/calc.py", "line": 6, "column": null,
         "message": "tests/test_calc.py::test_divide: ZeroDivisionError: division by zero",
         "detail": "ZeroDivisionError: division by zero", "fingerprint": "…"}
      ]
    }
  ]
}
```

With `-v` / `--full`, each `ci_content` entry adds `body` and `body_omitted_lines`. `fingerprint` ignores line numbers, so the same finding keeps its id when code moves.

---

## Behavior

1. Resolve targets (`resolve_show_targets`), overlay Gerrit state (`annotate`).
2. `CiContentService` fetches every link of every target in parallel through the matching **CI content provider** and the CI-content store ([caching](../ci-content.md#caching)).
3. Print everything that was fetched, including the error of any link that was not.

**Fetch failure is hard:** when any link cannot be fetched or parsed, the output still shows every result, then `ger ci` exits `11` with `ci error: could not fetch CI content for N link(s): …` on stderr.

`ger --refresh ci` bypasses the CI-content store as well as the Gerrit cache.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | No target has failed CI |
| `1` | Findings reported: some target's CI failed |
| `3` / `4` | Changeish not found / ambiguous |
| `11` | A CI link's content could not be fetched or parsed |

---

## See also

- [ci-content.md](../ci-content.md) — design: providers, findings, caching, credentials
- [`ger show`](show.md) — `--ci` attaches the same findings (soft failures)
- [gerrit-ci-strategies.md](../../gerrit-ci-strategies.md) — where CI links come from, custom providers
- [`example/hooks/ci/`](../../../example/hooks/ci/) — copyable custom strategy + content provider
