"""JSON payloads for one commit's overlay state: ``ger show`` and ``ger log``."""

from __future__ import annotations

from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommentChain,
    LogCommit,
    gerrit_inline_comment_url,
)
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
            chain_comments.append(entry)
            comment_payload.append(entry)
        chain_payload.append(
            {
                "root_id": chain.root_id,
                "path": chain.path,
                "line": chain.line,
                "start_line": chain.start_line,
                "url": None if chain.local else gerrit_inline_comment_url(gerrit_url, chain.root_id),
                "resolved": chain.resolved,
                "anchor_moved": chain.anchor_moved,
                "outdated": chain.outdated,
                **_anchored_fields(chain),
                "resolved_after_publish": chain.resolved_after_publish,
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
        "ci_failures": commit.ci_failures,
        "ci_links": [{"label": link.label, "url": link.url, "source": link.source} for link in commit.ci_links],
        "ci_pipelines": [
            {
                "label": pipe.label,
                "state": pipe.state,
                **({"url": pipe.url} if pipe.url else {}),
            }
            for pipe in commit.ci_pipelines
        ],
        "gerrit_url": commit.gerrit_url,
        "submittable": commit.submittable,
        "work_in_progress": commit.wip,
        "private": commit.private,
        "attention_reasons": commit.attention_reasons,
        "change_status": commit.change_status,
        "merged_equivalent": commit.merged_equivalent,
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
