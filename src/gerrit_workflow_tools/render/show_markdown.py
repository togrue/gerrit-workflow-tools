"""``ger show``'s Markdown for one commit (``--ai``): its state, its comment chains and its CI findings."""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.comment_chain import CommentChain
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.render.ci_content import format_ci_section_markdown
from gerrit_workflow_tools.render.comments_markdown import format_unresolved_section_markdown
from gerrit_workflow_tools.render.commit_row import attention_summary, votes_text
from gerrit_workflow_tools.render.status_fmt import chain_blocked_text, patchset_words


def commit_markdown(
    commit: LogCommit,
    *,
    chains: list[CommentChain],
    ci_contents: list[CiContent] | None,
    comments: CommentSelection,
    blocked_by: LogCommit | None = None,
) -> list[str]:
    """The lines ``ger show --ai`` prints for *commit*: heading, state, the chosen comment chains, CI findings.

    The same facts as the human view: attention, the commit that blocks it (*blocked_by*), patchset,
    WIP and private, votes and reviewers.
    """
    short = commit.short_sha or (commit.sha[:8] if commit.sha else "????????")
    said = frozenset({Attention.CHAIN_BLOCKED}) if blocked_by is not None else frozenset()
    summary = attention_summary(commit, leave_out=said)
    lines = [f"## {short} — {commit.summary or ''}", *([f"- {summary}"] if summary else [])]
    if blocked_by is not None:
        blocked = chain_blocked_text(blocked_by)
        lines.append(f"- {blocked[0].upper()}{blocked[1:]}")
    lines.append(f"- Patchset: {patchset_words(commit)}")
    flags = [flag for flag, on in (("work in progress", commit.wip), ("private", commit.private)) if on]
    if flags:
        lines.append(f"- Change is {' and '.join(flags)}")
    if commit.gerrit_url:
        lines.append(f"- Gerrit: {commit.gerrit_url}")
    if votes := votes_text(commit):
        lines.append(f"- Votes: {votes}")
    if commit.reviewers:
        cc = [r.slug for r in commit.reviewers if r.cc]
        named = [", ".join(r.slug for r in commit.reviewers if not r.cc)] + ([f"(cc: {', '.join(cc)})"] if cc else [])
        lines.append(f"- Reviewers: {' '.join(part for part in named if part)}")
    lines.append("")
    lines += format_unresolved_section_markdown(chains, commit.gerrit_url, pushed=commit.pushed, comments=comments)
    if ci_contents:
        lines += ["", *format_ci_section_markdown(ci_contents)]
    return lines
