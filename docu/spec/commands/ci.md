# `ger ci`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_ci.py` |
| **Requires** | `ger.webUrl`, credentials; `ger.ciCredential.<host>` when the CI server needs auth |
| **Design** | [ci-content.md](../ci-content.md) — layering, result shape, caching |

Why CI failed, without opening a console: for each target's failed **CI links**, fetch the **CI content** behind the link and print its **CI findings**, grouped by link label. This is the deliberate, expensive hop; `ger log` and the **Gerrit overlay** never fetch logs.

---

## Usage

```
ger ci [options] [CHANGEISH …]
```

Targets resolve exactly as in [`ger show`](show.md): each `CHANGEISH` is a changeish or an `A..B` / `A...B` range; `--stack` adds the local stack; with neither, `HEAD`. Each commit's header carries the same hints as `ger log` and `ger show`: a stack commit is judged within the whole stack. Links come from the same **CI strategy** as `ger log` (`ci_links`), so only changes whose Verified is −1 have any.

---

## Options

| Option | Description |
|--------|-------------|
| `--stack` | Include the local stack (`upstream_tip..HEAD`); prompts for a missing upstream like `ger show --stack` |
| `--branch NAME` | Work on local branch NAME: its local stack and upstream (default: the working branch) |
| `-v` / `--verbose` | After each link's findings, print the last 60 log lines |
| `--full` | After each link's findings, print the whole log (for searching the raw text) |
| `--warnings` | List the warnings below the errors, not just their count. JSON always has them |
| `--json` | JSON payload — the stable contract for agents |
| `--format {human,markdown,ai}` / `--ai` | Output format; `ai` (`--ai`) is the Markdown view ([Output formats](../../SPEC.md#output-formats)) |
| `--color`, `--hyperlinks`, `--debug-log` | Standard helpers |

`ger ci --help` ends with examples, and points to `ger log --help` for the status columns.

---

## Output

**Human:** per target, the `ger show` headline and the commit's subject on the line below it, then per link `label  build-url  (log: log-url)  (build, cached, log truncated)`: the build URL exactly as the CI message or Checks row gave it (what to quote when naming the build), and the log URL ger derived and read when that is another one and one line per finding: `tool  path:line[:col]  message`, with pytest's `E` lines indented beneath. Errors come first; warnings below them are one line, `N warnings not listed (ger ci --warnings lists them)`, unless `--warnings` is given or no error explains the failure ([severity](../ci-content.md#severity)). A target without failed links prints `(no failed CI links)`, `(not on Gerrit)`, or names the failed checks that had no link. When none of the targets has a failed link but another commit of the local stack failed CI (by default only `HEAD` is shown), a last dim line says which and how to look: `11ad86d below has failed CI: `ger ci 11ad86d`, or `ger ci --stack``.

**Markdown (`--ai`):** `## <sha> — <subject>`, `### CI findings`, `#### <label>` per label, a bullet per finding, warnings as a count like the human output; a fenced log with `-v` / `--full`.

**JSON:** always `{ "commits": [ … ] }`, one object per target:

```json
{
  "sha": "…", "change_id": "I…", "summary": "…", "pushed": true, "verified": -1,
  "gerrit_url": "…", "ci_failures": ["…"],
  "ci_content": [
    {
      "label": "jenkins", "url": "https://ci/…/55/console", "build_url": "https://ci/…/55/", "source": "message", "verdict": "failed",
      "provenance": {
        "url": "…", "provider": "jenkins", "build": "Widget #55", "fetched_at": "2026-11-09T10:00:00Z",
        "truncated": false, "cache_verdict": "body", "cached": false, "error": null
      },
      "findings": [
        {"tool": "pytest", "kind": "failed", "path": "demo/calc.py", "line": 6, "column": null,
         "message": "tests/test_calc.py::test_divide: ZeroDivisionError: division by zero",
         "detail": "ZeroDivisionError: division by zero", "fingerprint": "…", "severity": "error",
         "patchset_line": null, "line_changed": false}
      ],
      "collapse_warnings": true, "patch_set": null, "lines_on_patchset": false
    }
  ]
}
```

With `-v` / `--full`, each `ci_content` entry adds `body` and `body_omitted_lines`. `fingerprint` ignores line numbers, so the same finding keeps its id when code moves. `findings` always holds every finding, warnings included; `collapse_warnings` says whether a reader should fold them. `path` is the file of the commit where the logged path names one ([finding paths](../ci-content.md#finding-paths)). A commit ahead of the patchset CI built, with other code, still gets that patchset's findings, each entry saying so in `patch_set` and the findings' lines mapped onto the local commit (`patchset_line`, `line_changed`, `lines_on_patchset`: [while you fix it](../ci-content.md#while-you-fix-it)).

---

## Behavior

1. Resolve targets (`resolve_show_targets`), overlay Gerrit state (`annotate_targets`: a stack commit within its whole stack, as `ger show` does).
2. `CiContentService` fetches every link of every target in parallel through the matching **CI content provider** and the CI-content store ([caching](../ci-content.md#caching)).
3. Print everything that was fetched, including the error of any link that was not.

**Fetch failure is hard:** when any link cannot be fetched or parsed, the output still shows every result, then `ger ci` exits `11`. Each link that could not be read says why once, in words, under its build URL, with the log to open by hand: `error: could not reach ci.example.invalid: host not found; open http://ci.example.invalid/job/p/934/console` (`host not found`, `connection refused`, `timed out`, `TLS failed (…)`, `<host> answered HTTP 404 Not Found`). The last line on stderr only counts them: `ci error: the CI log could not be fetched (see above)`, `ci error: 2 of 3 CI logs could not be fetched (see above)`.

`ger --refresh ci` bypasses the CI-content store as well as the Gerrit cache.

---

## Exit codes

`0` whether or not some target's CI failed: the findings are the output. `0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract.

---

## See also

- [ci-content.md](../ci-content.md) — design: providers, findings, caching, credentials
- [`ger show`](show.md) — `--ci` attaches the same findings (soft failures)
- [gerrit-ci-strategies.md](../../gerrit-ci-strategies.md) — where CI links come from, custom providers
- [`example/hooks/ci/`](../../../example/hooks/ci/) — copyable custom strategy + content provider
