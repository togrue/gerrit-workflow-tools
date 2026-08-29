# Gerrit CI strategies

`ger log` and `ger show` surface **CI links** when Verified is −1: clickable URLs to
failed build consoles (often Jenkins). Links come from the Gerrit Checks plugin
and/or **change messages** posted by CI bots.

## Built-in behaviour

When no `.ger/ci/registry.py` (or global cache-dir registry) matches
`gerrit.project`, **ger** uses a built-in strategy that:

1. Reads failed **Checks** rows and rewrites their `url` fields to `…/console`.
2. If Checks produce no links, scans change messages for Jenkins **Build Failed**
   lines and emits one console link from the newest failure.

Common Jenkins message shapes:

| Pattern | Example (anonymised) |
|---------|----------------------|
| Build failed | `Patch Set 2: Verified-1\n\nBuild Failed \n\nhttps://ci.example.org/…/55/ : FAILURE` |
| Build started | `Patch Set 1:\n\nBuild Started https://ci.example.org/job/Widget/12/` |
| No builds | `Patch Set 4:\n\nNo Builds Executed` |

**Checks win over messages.** When any link has `source: "checks"`, message-derived
links are dropped. This applies to built-in and custom strategies alike.

**Lazy messages.** Change messages are fetched from Gerrit only when a strategy
actually reads the `messages` argument. The built-in strategy checks `checks`
first; if they already yield a link, `get_messages` is never called. Custom
strategies that follow the same shape get this for free. Do not call
`list(messages)` speculatively before checking whether `checks` already answered.

---

## When you need a registry

| Situation | What to do |
|-----------|------------|
| Standard Jenkins + Gerrit Checks | **Nothing** |
| Custom URL shape or non-Jenkins CI | Add `.ger/ci/registry.py` (see below) |

Registry paths and tier resolution: [Configuration.md](Configuration.md#extension-scripts-ger-and-configger).
Use `STRATEGIES` keyed by exact `gerrit.project`. For prefix matching across many
repos, `get_strategy(project)` is also supported (one factory, same signature).

Copy [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) only when you need
customization — the example rewrites console URLs to test reports.

---

## Extension template

```python
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_strategy import default_extract_ci_links

def extract_ci_links(*, project, checks, messages):
    links = default_extract_ci_links(project=project, checks=checks, messages=messages)
    # optional: rewrite URLs, add project-specific fallbacks, etc.
    return links

STRATEGIES = {"mygroup/myrepo": extract_ci_links}
```

**Contract:**

- Implement `extract_ci_links(*, project, checks, messages) -> list[CiLink]`.
- Set `source` to `"checks"` or `"message"` on each row.
- Return all candidates; **ger** keeps checks-sourced links when any exist.
- Start from `default_extract_ci_links` unless you fully replace parsing.

---

## Advanced: non-Jenkins or custom parsers

For bots that do not match the built-in Jenkins patterns, implement
`extract_ci_links` directly. Change messages are dicts from Gerrit's
`changes/…/messages` API (`message`, `tag`, `date`, `_revision_number`, …).

Parsing helpers in `gerrit_workflow_tools.core.gerrit_message_parsing`:

- `parse_change_message(msg)` — classify a message (`build_failed`, `build_started`, …) and extract `build_url`.
- `jenkins_job_url_to_console(url)` — idempotent Jenkins console rewrite.
- `ci_links_from_failed_checks(checks)` / `ci_links_from_build_failed_messages(messages)` — composable pieces used by `default_extract_ci_links`.

Match your bot's format with regex or structured `tag` values, then map to the
pipeline URL your team uses. The registry runs only for matching
`gerrit.project` keys.

---

## See also

- [Configuration.md](Configuration.md) — registry paths and resolution order
- [`contrib/ger-ci-example/`](../contrib/ger-ci-example/) — testReport URL example
- [spec/commands/log.md](spec/commands/log.md) — `ci_links` JSON field
