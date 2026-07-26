"""ChangeInfo fixture builders for CLI tests (no network).

Commands take a ``GerritRest`` directly, so tests seed a
:class:`~gerrit_workflow_tools.core.gerrit.change_store.ChangeStore` with payloads built
here and pass it in. No module-path patching is involved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, merge_base_with_target, parse_change_id
from tests.change_store import ChangeStore


def change_info_for_sha(
    sha: str,
    change_id: str,
    *,
    project: str = "testproj",
    branch: str = "main",
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
        "id": f"{project}~{branch}~{change_id}",
        "change_id": change_id,
        "project": project,
        "branch": branch,
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
    Map triplet ``id`` -> ChangeInfo for each row with a Change-Id.

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
        out[str(detail["id"])] = detail
    return out


def make_query_changes_impl(details: dict[str, dict[str, Any]]):
    """Return a ``query_changes`` callable over *details* (delegates to :class:`ChangeStore`)."""

    store = ChangeStore(details)

    def query_changes(q: str, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        return store.query_changes(q, n=n, options=options)

    return query_changes


def gerrit_client_class_stub(inst: object) -> MagicMock:
    """Stand-in for the ``HttpGerritRest`` class where both construction paths yield *inst*.

    Production builds clients via ``HttpGerritRest.from_cwd(web_base, cwd)``; patching the
    class with a plain ``return_value`` only covers ``HttpGerritRest(...)`` and leaves
    ``.from_cwd()`` handing back a fresh auto-mock.
    """
    cls = MagicMock(return_value=inst)
    cls.from_cwd.return_value = inst
    return cls


def head_change_id(repo: Path) -> str:
    """Change-Id from ``HEAD`` commit message."""
    raw = git_out("log", "-1", "--format=%B", "HEAD", cwd=repo)
    cid = parse_change_id(raw)
    assert cid is not None
    return cid
