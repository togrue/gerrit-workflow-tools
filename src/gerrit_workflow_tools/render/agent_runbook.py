"""The **agent runbook** as Markdown an agent can follow, and as the JSON summary beside it."""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.agent_runbook import RunbookStop
from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain
from gerrit_workflow_tools.render.comments import chain_location, first_line, format_comment_chain_markdown


_RULES = """\
## Rules

- Work through the commits below in the order given, oldest first.
- A fix belongs in the commit it is listed under. Never add new commits to the stack.
- Never publish anything: no `ger push`, no `git push`, no `ger reply --publish`, no `ger review --vote`.
  Replies you write are drafts; a human reads and publishes them.
- Leave everything under "Left for a human" alone, code and comment both.
- Do not fix findings listed as inherited: they are fixed in an earlier commit of this run.
- If a comment is unclear, or you disagree with it, do not guess. Leave the code as it is and
  say so in your report.

## For each commit

1. `ger edit <Change-Id>` stops a rebase on the commit, so you can amend it. Use the Change-Id
   given below: commit ids change as you amend the commits underneath.
2. Make the changes listed for it. Run the checks that failed, where you can.
3. `git add -A && git commit --amend --no-edit && git rebase --continue`.
   If the rebase stops on a conflict in a later commit, resolve it, `git add` the files and
   `git rebase --continue` again.
4. For each comment you addressed, run the `ger reply … --done` line given with it. That saves
   a draft reply "Done" which resolves the chain once a human publishes it.

When all commits are done, run `ger log` and report, per commit: what you changed, which
comments you answered, and what you left and why.
"""


def _finding_line(finding: CiFinding) -> str:
    where = f" `{finding.location}`" if finding.location else ""
    return f"- **{finding.tool}**{where} — {finding.message}"


def _findings(findings: Sequence[CiFinding]) -> list[str]:
    lines: list[str] = []
    for finding in findings:
        lines.append(_finding_line(finding))
        if finding.detail:
            lines += ["  ```", *(f"  {row}" for row in finding.detail.splitlines()), "  ```"]
    return lines


def _chain_block(chain: CommentChain, gerrit_url: str | None) -> list[str]:
    # One heading level down: the chain sits under its commit's section.
    lines = [
        f"#{line}" if line.startswith("### ") else line for line in format_comment_chain_markdown(chain, gerrit_url)
    ]
    return [*lines, "", f"When addressed: `ger reply {chain.root_id} --done`", ""]


def _stop_section(index: int, stop: RunbookStop) -> list[str]:
    commit = stop.commit
    lines = [f"## {index}. {commit.summary}", "", f"`ger edit {commit.change_id}`  (now {commit.short_sha})", ""]
    if commit.gerrit_url:
        lines += [f"Gerrit: {commit.gerrit_url}", ""]
    if stop.for_agent:
        lines += [f"### Comments to address ({len(stop.for_agent)})", ""]
        for chain in stop.for_agent:
            lines += _chain_block(chain, commit.gerrit_url)
    if stop.to_fix:
        origin = (
            "introduced by this commit"
            if stop.origin_known
            else "this commit or an earlier one may have introduced them"
        )
        lines += [f"### CI findings to fix ({len(stop.to_fix)}; {origin})", "", *_findings(stop.to_fix), ""]
    for content in stop.unread:
        lines += [f"CI failed, but its log could not be read ({content.provenance.error}): {content.link.url}", ""]
    if stop.inherited:
        lines += [f"### Inherited CI findings — do not fix here ({len(stop.inherited)})", ""]
        lines += [*(_finding_line(finding) for finding in stop.inherited), ""]
    if stop.held:
        lines += [f"### Left for a human ({len(stop.held)})", ""]
        lines += [
            *(f"- `{chain_location(chain)}` — {first_line(chain.comments[-1].message)}" for chain in stop.held),
            "",
        ]
    return lines


def runbook_markdown(stops: Sequence[RunbookStop], *, branch: str | None) -> str:
    """The runbook: the rules, then one section per stop that has work for an agent."""
    work = [stop for stop in stops if stop.has_agent_work]
    waiting = [stop for stop in stops if not stop.has_agent_work]
    lines = [
        "# Address the review feedback on this stack",
        "",
        f"Branch `{branch or 'HEAD'}`: {len(work)} commit(s) to work on.",
        "",
        _RULES,
    ]
    for index, stop in enumerate(work, start=1):
        lines += _stop_section(index, stop)
    if waiting:
        lines += ["## No step for these commits", ""]
        for stop in waiting:
            why = "its comments are left for a human" if stop.held else "its CI findings are fixed in an earlier commit"
            lines.append(f"- {stop.commit.short_sha} {stop.commit.summary} — {why}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def runbook_json(stops: Sequence[RunbookStop], *, branch: str | None) -> dict[str, object]:
    """The runbook with its counts: what a client shows before handing it over."""
    commits = [
        {
            "sha": stop.commit.sha,
            "short_sha": stop.commit.short_sha,
            "change_id": stop.commit.change_id,
            "summary": stop.commit.summary,
            "comments": len(stop.for_agent),
            "held": len(stop.held),
            "findings": len(stop.to_fix),
            "inherited": len(stop.inherited),
            "ci_unread": len(stop.unread),
            "agent_work": stop.has_agent_work,
        }
        for stop in stops
    ]
    return {
        "branch": branch,
        "commits": commits,
        "totals": {
            "commits": sum(1 for stop in stops if stop.has_agent_work),
            "comments": sum(len(stop.for_agent) for stop in stops),
            "held": sum(len(stop.held) for stop in stops),
            "findings": sum(len(stop.to_fix) for stop in stops),
        },
        "markdown": runbook_markdown(stops, branch=branch),
    }
