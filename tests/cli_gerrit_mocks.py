"""Reusable Gerrit API mocks for CLI tests (no network)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit_change_status import norm_change_id
from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, merge_base_with_target, parse_change_id


def change_info_for_sha(
    sha: str,
    change_id: str,
    *,
    project: str = "testproj",
    number: int = 100,
    cr: int = 2,
    verified: int = 1,
    submittable: bool = True,
    unresolved_comment_count: int = 0,
    reviewers: list[dict[str, Any]] | None = None,
    work_in_progress: bool = False,
    private: bool = False,
    status: str = "NEW",
) -> dict[str, Any]:
    """Minimal ChangeInfo for :func:`batch_load_change_details` / ``query_changes``."""
    out: dict[str, Any] = {
        "id": f"{project}~main~{change_id}",
        "change_id": change_id,
        "project": project,
        "_number": number,
        "status": status,
        "subject": "subj",
        "current_revision": sha,
        "submittable": submittable,
        "unresolved_comment_count": unresolved_comment_count,
        "revisions": {sha: {"_number": 1}},
        "work_in_progress": work_in_progress,
        "private": private,
        "labels": {
            "Verified": {"value": verified, "all": [{"value": verified}]},
            "Code-Review": {"value": cr, "all": [{"value": cr}]},
        },
    }
    if reviewers is None:
        out["reviewers"] = [{"account": {"username": "default-reviewer"}, "state": "REVIEWER"}]
    else:
        out["reviewers"] = reviewers
    return out


def stack_rows_mb_to_head(repo: Path) -> list[Commit]:
    """Oldest-first commits for upstream_tip..HEAD (same window as the local stack)."""
    _fork, _, target_tip = merge_base_with_target(repo)
    return commits_in_range(repo, f"{target_tip}..HEAD")


def build_details_by_change_id(
    rows: list[Commit] | list[tuple[str, str, str, str]],
    *,
    per_index_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Map normalized Change-Id -> ChangeInfo for each row with a Change-Id.

    *per_index_overrides* aligns with *rows* (e.g. ``[{"cr": 1}]`` to force attention).
    """
    out: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if isinstance(row, Commit):
            sha = row.sha
            cid = row.change_id
        else:
            sha, _short, _sub, raw = row
            cid = parse_change_id(raw)
        if not cid:
            continue
        ov = per_index_overrides[i] if per_index_overrides and i < len(per_index_overrides) else {}
        detail = change_info_for_sha(
            sha,
            cid,
            number=100 + i,
            cr=int(ov.get("cr", 2)),
            verified=int(ov.get("verified", 1)),
            submittable=bool(ov.get("submittable", True)),
            unresolved_comment_count=int(ov.get("unresolved_comment_count", 0)),
            reviewers=ov.get("reviewers"),
            work_in_progress=bool(ov.get("work_in_progress", False)),
            private=bool(ov.get("private", False)),
            status=str(ov.get("status", "NEW")),
        )
        out[norm_change_id(cid)] = detail
    return out


def make_query_changes_impl(details: dict[str, dict[str, Any]]):
    """Return a ``query_changes`` callable matching batched ``change:Id OR ...`` queries."""

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        matches = re.findall(r"change:(\S+)", q)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in matches:
            key = norm_change_id(m)
            if key in seen:
                continue
            seen.add(key)
            row = details.get(key)
            if row is not None:
                result.append(row)
        return result

    return query_changes


@contextmanager
def patch_gerrit_client_for_queries(
    module: str,
    *,
    details_by_change_id: dict[str, dict[str, Any]],
    web_base: str = "https://g.example",
) -> Iterator[MagicMock]:
    """Patch ``resolve_gerrit_web_base`` and ``GerritClient`` on *module* (e.g. ``cli_log``)."""
    inst = MagicMock()
    inst.query_changes.side_effect = make_query_changes_impl(details_by_change_id)

    def _get_change(change_id: str) -> dict[str, Any]:
        key = norm_change_id(change_id)
        row = details_by_change_id.get(key)
        if row is None:
            raise AssertionError(f"test mock: no ChangeInfo for {change_id!r} (normalized {key!r})")
        return row

    inst.get_change.side_effect = _get_change
    inst.add_reviewer.return_value = {}
    inst.delete_reviewer.return_value = None
    inst.get_comments.return_value = {}
    with (
        patch(f"{module}.resolve_gerrit_web_base", return_value=web_base),
        patch(f"{module}.GerritClient", return_value=inst),
    ):
        yield inst


def head_change_id(repo: Path) -> str:
    """Change-Id from ``HEAD`` commit message."""
    raw = git_out("log", "-1", "--format=%B", "HEAD", cwd=repo)
    cid = parse_change_id(raw)
    assert cid is not None
    return cid
