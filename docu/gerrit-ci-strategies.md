# Gerrit CI strategies and message parsing

`ger log` and `ger show` surface **CI links** when Verified is −1: clickable URLs to
failed build consoles (often Jenkins). Links come from the Gerrit Checks plugin
and/or **change messages** posted by CI bots.

## Built-in behaviour

When no project-local or global CI registry matches `gerrit.project`, **ger** uses a
built-in strategy that:

1. Reads failed **Checks** rows and rewrites their `url` fields to `…/console`.
2. If Checks produce no links, scans change messages for Jenkins **Build Failed**
   lines (including nested `/job/…/job/…/<build>/` paths and trailing
   ` : FAILURE`) and emits one console link from the newest failure.

Supported message shapes include:

| Pattern | Example (anonymised) |
|---------|----------------------|
| Build started | `Patch Set 1:\n\nBuild Started https://ci.example.org/job/Widget/job/Widget/12/` |
| Build failed | `Patch Set 2: Verified-1\n\nBuild Failed \n\nhttps://ci.example.org/…/55/ : FAILURE` |
| No builds | `Patch Set 4:\n\nNo Builds Executed` |
| Patch upload | `Uploaded patch set 3: Patch Set 2 was rebased.` |

The built-in parser classifies messages via `parse_change_message()` in
`gerrit_workflow_tools.core.gerrit_message_parsing`. Only **build failed**
messages contribute CI links; other kinds are available for future features and
for custom strategies that import the helper.

**Checks win over messages.** If any link has `source: "checks"`, message-derived
links are dropped. This matches custom registry behaviour.

---

## Custom CI strategies

For project-specific URL shapes, host naming, or non-Jenkins CI, add a registry at
**`.ger/ci/registry.py`** (or `~/.config/ger/<host>/ci/registry.py`). See
[Configuration.md](Configuration.md#extension-scripts-ger-and-configger) for tier
resolution.

### Callable signature

```python
from gerrit_workflow_tools.core.ci_links import CiLink

def extract_ci_links(
    *,
    project: str,
    checks: list[dict],
    messages: list[dict],
) -> list[CiLink]:
    ...
```

Return `CiLink(label=…, url=…, source="checks"|"message")` rows. You may return
both checks- and message-derived links; **ger** keeps only checks links when any
exist.

Register per project:

```python
STRATEGIES = {
    "mygroup/myrepo": extract_ci_links,
}
```

Or use a factory:

```python
def get_strategy(project: str):
    if project.startswith("mygroup/"):
        return extract_ci_links
    return None
```

Copy [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) as a starting point.

### Extending the built-in parser

Import helpers instead of reimplementing Jenkins parsing:

```python
from gerrit_workflow_tools.core.gerrit_message_parsing import (
    builtin_extract_ci_links,
    jenkins_job_url_to_console,
    parse_change_message,
)

def extract_ci_links(*, project, checks, messages):
    links = builtin_extract_ci_links(project=project, checks=checks, messages=messages)
    if links:
        return links
    # fall back to custom logic for this project
    ...
```

---

## Writing custom message parsers

Change messages are dicts from Gerrit's `changes/…/messages` API (`message`,
`tag`, `date`, `_revision_number`, …).

### Classify a message

```python
from gerrit_workflow_tools.core.gerrit_message_parsing import parse_change_message

parsed = parse_change_message(msg)
# parsed.kind: patch_set_upload | build_started | build_failed | no_builds | unknown
# parsed.patch_set, parsed.build_url, parsed.label_vote
```

Use `parsed.kind == "build_failed"` and `parsed.build_url` when you only care
about failed builds.

### Extract a Jenkins URL from free text

```python
from gerrit_workflow_tools.core.gerrit_message_parsing import jenkins_build_url_from_text

url = jenkins_build_url_from_text(msg.get("message") or "")
```

Handles nested job folders and ` : FAILURE` suffixes.

### Iterate messages newest-first

Sort by `_revision_number` and `date`, then pick the first matching row:

```python
def newest_first(messages):
    return sorted(
        messages,
        key=lambda m: (m.get("_revision_number") or -1, str(m.get("date") or "")),
    )

for msg in reversed(newest_first(messages)):
    parsed = parse_change_message(msg)
    if parsed.kind == "build_failed" and parsed.build_url:
        ...
        break
```

---

## URL transformations

CI systems expose different “useful” URLs than Gerrit stores in Checks or bot
messages. Strategies should return the URL users actually want (console log,
test report, pipeline run, etc.).

### Jenkins job → console

```python
from gerrit_workflow_tools.core.gerrit_message_parsing import jenkins_job_url_to_console

console = jenkins_job_url_to_console("https://ci.example.org/job/App/42/")
# → …/job/App/42/console
```

Idempotent when `/console` is already present.

### Custom transforms

Keep transforms as small pure functions and apply them consistently to both
Checks `url` fields and message-extracted URLs:

```python
def to_test_report(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/testReport"):
        return base
    return f"{base}/testReport"

def extract_ci_links(*, project, checks, messages):
    out = []
    for row in checks:
        if row.get("state") != "FAILED":
            continue
        raw = row.get("url") or ""
        if raw.startswith("http"):
            out.append(CiLink(label="tests", url=to_test_report(raw), source="checks"))
    if out:
        return out
    for msg in messages:
        parsed = parse_change_message(msg)
        if parsed.build_url:
            out.append(CiLink(label="tests", url=to_test_report(parsed.build_url), source="message"))
            break
    return out
```

### GitLab / other CI

Match your bot's message format with regex or structured `tag` values, then map
to the pipeline URL your team uses. The registry runs only for matching
`gerrit.project` keys, so different repos can use different parsers.

---

## See also

- [Configuration.md](Configuration.md) — registry paths and resolution order
- [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) — minimal Jenkins example
- [spec/commands/log.md](spec/commands/log.md) — `ci_links` JSON field
