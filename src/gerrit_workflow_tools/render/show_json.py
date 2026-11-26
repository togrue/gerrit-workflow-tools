"""JSON payloads for one commit's overlay state: ``ger show`` and ``ger log``."""

from __future__ import annotations

from gerrit_workflow_tools.core.annotated_stack import AnnotatedStack, NextStepKind, StackOverview
from gerrit_workflow_tools.core.attention import attention_severity, blocking_reason
from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.comment_chain import CommentChain
from gerrit_workflow_tools.core.comment_chains import gerrit_inline_comment_url
from gerrit_workflow_tools.core.comment_write import ChangedComment, WrittenComment
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.rebase_next_step import stop_refusal
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseProgress
from gerrit_workflow_tools.core.upstream_moved import UpstreamMoved
from gerrit_workflow_tools.render.ci_content import ci_content_json


def comment_json_payload(
    chains: list[CommentChain],
    gerrit_url: str | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Flatten comment chains into JSON comment and chain lists."""
    comment_payload: list[dict[str, object]] = []
    chain_payload: list[dict[str, object]] = []
    for chain in chains:
        chain_comments: list[dict[str, object]] = []
        for row_item in chain.comments:
            entry: dict[str, object] = {
                "path": row_item.path,
                "line": row_item.line,
                "message": row_item.message,
                "url": None if row_item.local else gerrit_inline_comment_url(gerrit_url, row_item.comment_id),
                "patch_set": row_item.patch_set,
                "draft": row_item.draft,
                "state": row_item.state,
            }
            if row_item.author:
                entry["author"] = row_item.author
            if row_item.comment_id:
                entry["comment_id"] = row_item.comment_id
            if row_item.context_lines:
                entry["context_lines"] = [
                    {"line_number": line.line_number, "context_line": line.text} for line in row_item.context_lines
                ]
            if row_item.suggested_edits:
                entry["suggested_edits"] = [edit.to_json() for edit in row_item.suggested_edits]
            chain_comments.append(entry)
            comment_payload.append(entry)
        chain_payload.append(
            {
                "root_id": chain.root_id,
                "path": chain.path,
                "line": chain.line,
                "start_line": chain.start_line,
                "range": chain.comment_range.as_gerrit() if chain.comment_range else None,
                "url": None if chain.local else gerrit_inline_comment_url(gerrit_url, chain.root_id),
                "resolved": chain.resolved,
                "anchor_moved": chain.anchor_moved,
                "outdated": chain.outdated,
                **_anchored_fields(chain),
                "resolved_after_publish": chain.resolved_after_publish,
                "turn": chain.turn.value if chain.turn else None,
                "comments": chain_comments,
            }
        )
    return comment_payload, chain_payload


def _anchored_fields(chain: CommentChain) -> dict[str, object]:
    """Where a local comment was written and the text it covered; ``null`` for a Gerrit chain."""
    anchor = chain.anchor
    return {
        "anchored_path": anchor.anchored.path if anchor else None,
        "anchored_start_line": anchor.anchored.start_line if anchor else None,
        "anchored_line": anchor.anchored.line if anchor else None,
        "anchored_text": anchor.text if anchor else None,
    }


def log_commit_json(commit: LogCommit) -> dict[str, object]:
    """The overlay state of one commit: what ``ger log --json`` and ``ger show --json`` both carry."""
    return {
        "sha": commit.sha if commit.sha else None,
        "change_id": commit.change_id,
        "summary": commit.summary,
        "pushed": commit.pushed,
        "patchset_status": commit.patchset_status,
        "verified": commit.verified,
        "code_review": commit.code_review,
        "comments_unresolved": commit.comments_unresolved,
        "comments_awaiting_reply": commit.comments_awaiting_reply,
        "comments_drafted": commit.comments_drafted,
        "comments_to_answer": commit.comments_to_answer,
        "comments_total": commit.comments_total,
        "ci_failures": commit.ci_failures,
        "ci_links": [
            {
                "label": link.label,
                "url": link.url,
                "build_url": link.build_url,
                "source": link.source,
                "verdict": link.verdict.value,
            }
            for link in commit.ci_links
        ],
        "ci_pipelines": [
            {
                "label": pipe.label,
                "state": pipe.state,
                **({"url": pipe.url} if pipe.url else {}),
                **({"verdict": pipe.verdict.value} if pipe.verdict else {}),
            }
            for pipe in commit.ci_pipelines
        ],
        "gerrit_url": commit.gerrit_url,
        "number": commit.number,
        "current_patch_set": commit.current_patch_set,
        "last_pushed_votes": (
            {"verified": commit.last_pushed_votes[0], "code_review": commit.last_pushed_votes[1]}
            if commit.last_pushed_votes
            else None
        ),
        "code_change": commit.code_change,
        "ci_patch_set": commit.ci_patchset.number if commit.ci_patchset else None,
        "submittable": commit.submittable,
        "votes": [{"label": vote.label, "value": vote.value, "by": vote.by} for vote in commit.votes],
        "reviewers": [{"name": r.slug, "cc": r.cc} for r in commit.reviewers],
        "work_in_progress": commit.wip,
        "private": commit.private,
        "attention_reasons": commit.attention_reasons,
        "severity": attention_severity(commit.attention_reasons).value,
        "change_status": commit.change_status,
        "merged_equivalent": commit.merged_equivalent,
        "blocks_chain": commit.blocks_chain,
    }


def commit_json_payload(
    commit: LogCommit,
    *,
    is_local: bool,
    resolution: object,
    chains: list[CommentChain],
    ci_content: list[CiContent] | None = None,
) -> dict[str, object]:
    """Build the per-commit JSON object for ``ger show --json``.

    ``ci_content`` (findings per **CI link**, no body) is present only when CI content is on.
    """
    comment_payload, chain_payload = comment_json_payload(chains, commit.gerrit_url)
    ci_payload = {} if ci_content is None else {"ci_content": [ci_content_json(c) for c in ci_content]}
    return {
        **log_commit_json(commit),
        "comments": comment_payload,
        "comment_chains": chain_payload,
        "local_commit": is_local,
        "resolution": resolution.to_json_dict(),  # type: ignore[attr-defined]
        **ci_payload,
    }


def changed_comment_json(action: str, changed: ChangedComment) -> dict[str, object]:
    """One of your comments edited or discarded; ``message`` is what it said before."""
    return {
        "action": action,
        "state": changed.state,
        "id": changed.id,
        "change": changed.change,
        "message": changed.message,
    }


def written_comment_json(written: WrittenComment) -> dict[str, object]:
    """A comment chain just started (``ger comment --json``, ``POST /comments/new``)."""
    location = written.location
    return {
        "action": "created",
        "change": written.change,
        "commit": written.commit,
        "path": location.path,
        "line": location.line,
        "range": location.range.as_gerrit() if location.range else None,
        "state": written.state,
        "id": written.id,
        "patch_set": written.patch_set,
        "unresolved": written.unresolved,
        "url": written.url,
    }


def rebase_json(stack: AnnotatedStack, progress: RebaseProgress | None) -> dict[str, object] | None:
    """The rebase underway, as ``ger log --json`` and ``GET /stack`` serve it; ``None`` without one.

    ``current`` is the id of the commit it stopped at as the stack shows it: the amended commit
    after an amend, the commit as it was while it is not applied. ``as_before`` is true when the
    rebase keeps no todo to show it live (the ``apply`` backend): ``ger log`` shows the branch as it
    was before it, unless an explicit range was asked for. ``actions`` are the ways on from this stop
    (:attr:`RebaseProgress.actions`), ``refused`` why each of the others is not
    (:func:`~gerrit_workflow_tools.core.rebase_next_step.stop_refusal`).
    """
    if progress is None:
        return None
    current = stack.rebase.current if stack.rebase else None
    return {
        "branch": progress.branch,
        "onto": progress.onto,
        "orig_head": progress.orig_head,
        "stop": progress.stop.value if progress.stop else None,
        "applied": progress.applied,
        "current": current.sha if current else None,
        "folding_into": progress.folding_into,
        "reused": list(progress.reused),
        "unmerged": list(progress.unmerged),
        "as_before": not progress.keeps_todo,
        "actions": [action.value for action in progress.actions],
        "refused": {action.value: why for action in RebaseAction if (why := stop_refusal(progress, action))},
    }


def upstream_json(moved: UpstreamMoved | None) -> dict[str, object] | None:
    """How far the upstream moved on, as ``ger log --json`` and ``GET /stack`` serve it; ``None`` when not told."""
    if moved is None:
        return None
    conflict = moved.conflict
    return {
        "name": moved.name,
        "behind": moved.behind,
        "argv": list(moved.argv),
        "conflict": conflict
        and {
            "sha": conflict.commit.sha,
            "summary": conflict.commit.subject,
            "files": list(conflict.files),
            "kind": conflict.kind.value,
        },
        "preview": moved.preview,
        "reason": moved.reason,
        "dropped": [{"sha": c.sha, "summary": c.subject} for c in moved.dropped],
    }


def rebase_steps(stack: AnnotatedStack) -> list[str | None]:
    """Per commit of *stack*: where the rebase underway stands on it (``done``, ``current``, ``todo``), or ``None``."""
    if stack.rebase is None:
        return [None] * len(stack.commits)
    return [step.value if step else None for step in stack.rebase.steps]


def next_steps_json(overview: StackOverview) -> list[dict[str, object]]:
    """The stack's next steps (``StackOverview.next_steps``), as ``ger log --json`` and ``GET /stack`` serve them."""
    return [
        {
            "kind": step.kind.value,
            "shas": [c.sha for c in step.commits],
            "beyond_boundary": step.beyond_boundary,
            "reason": blocking_reason(step.commits[0]) if step.kind is NextStepKind.CHAIN_BLOCKED else None,
        }
        for step in overview.next_steps()
    ]
