"""``ger show``'s Markdown for one commit (``--ai``): its state, its comment chains and its CI findings."""

from __future__ import annotations

from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit
from gerrit_workflow_tools.render.ci_content import format_ci_section_markdown
from gerrit_workflow_tools.render.comments import format_unresolved_section_markdown
from gerrit_workflow_tools.render.commit_row import attention_summary


def commit_markdown(
    commit: LogCommit,
    *,
    chains: list[CommentChain],
    ci_contents: list[CiContent] | None,
    comments: CommentSelection,
) -> list[str]:
    """The lines ``ger show --ai`` prints for *commit*: heading, state, the chosen comment chains, CI findings."""
    short = commit.short_sha or (commit.sha[:8] if commit.sha else "????????")
    lines = [f"## {short} — {commit.summary or ''}", f"- {attention_summary(commit)}"]
    if commit.gerrit_url:
        lines.append(f"- Gerrit: {commit.gerrit_url}")
    lines.append("")
    lines += format_unresolved_section_markdown(chains, commit.gerrit_url, pushed=commit.pushed, comments=comments)
    if ci_contents:
        lines += ["", *format_ci_section_markdown(ci_contents)]
    return lines
