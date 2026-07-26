"""Reusable Gerrit API mocks for CLI tests (no network)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, merge_base_with_target, parse_change_id


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


def _lookup_detail(details: dict[str, dict[str, Any]], ref: str) -> dict[str, Any] | None:
    matches = _lookup_details(details, ref)
    return matches[0] if matches else None


def _lookup_details(details: dict[str, dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    if ref in details:
        return [details[ref]]
    if ref.isdigit():
        out = [row for row in details.values() if row.get("_number") == int(ref)]
        if out:
            return out
    m = re.search(r"~(I[a-fA-F0-9]{40})$", ref)
    if m:
        suffix = m.group(1)
        out = [row for row in details.values() if row.get("change_id") == suffix]
        if out:
            return out
    out = [row for row in details.values() if row.get("change_id") == ref]
    return out


def make_query_changes_impl(details: dict[str, dict[str, Any]]):
    """Return a ``query_changes`` callable for project-scoped OR and triplet queries."""

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        result: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(row: dict[str, Any] | None) -> None:
            if row is None:
                return
            key = (str(row.get("id") or ""), row.get("_number"))
            if key in seen:
                return
            seen.add(key)
            result.append(row)

        # Split on project: clauses so batch ``project:P (change:… OR …)`` works.
        parts = re.split(r"(?=project:)", q)
        for part in parts:
            part = part.strip().rstrip(")")
            if not part.startswith("project:"):
                continue
            pm = re.match(r"project:(\S+)\s+(.*)", part, flags=re.DOTALL)
            if not pm:
                continue
            project, rest = pm.group(1), pm.group(2).strip()
            # Exact triplet scope: project:P branch:B change:I (single / fallback)
            if re.match(r"branch:\S+\s+change:", rest) and "(" not in rest:
                for branch, change_id in re.findall(r"branch:(\S+)\s+change:(\S+)", rest):
                    change_id = change_id.rstrip(")")
                    triplet = f"{project}~{branch}~{change_id}"
                    _add(details.get(triplet))
                continue
            # Project-scoped Change-Id OR — return all branches for those ids.
            for change_id in re.findall(r"change:(\S+)", rest):
                change_id = change_id.rstrip(")")
                for row in details.values():
                    if row.get("project") == project and row.get("change_id") == change_id:
                        _add(row)

        # Bare ``change:`` when no project scope.
        if "project:" not in q:
            for change_ref in re.findall(r"change:(\S+)", q):
                change_ref = change_ref.rstrip(")")
                for row in _lookup_details(details, change_ref):
                    _add(row)

        return result

    return query_changes


def gerrit_client_class_stub(inst: MagicMock) -> MagicMock:
    """Stand-in for the ``GerritClient`` class where both construction paths yield *inst*.

    Production builds clients via ``GerritClient.from_cwd(web_base, cwd)``; patching the
    class with a plain ``return_value`` only covers ``GerritClient(...)`` and leaves
    ``.from_cwd()`` handing back a fresh auto-mock.
    """
    cls = MagicMock(return_value=inst)
    cls.from_cwd.return_value = inst
    return cls


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
        row = _lookup_detail(details_by_change_id, change_id)
        if row is None:
            raise AssertionError(f"test mock: no ChangeInfo for {change_id!r}")
        return row

    inst.get_change.side_effect = _get_change

    def _list_change_reviewers(change_id: str) -> list[dict[str, Any]]:
        row = _get_change(change_id)
        reviewers = row.get("reviewers")
        if isinstance(reviewers, list):
            return reviewers
        if isinstance(reviewers, dict):
            out: list[dict[str, Any]] = []
            for role in ("REVIEWER", "CC"):
                bucket = reviewers.get(role)
                if isinstance(bucket, list):
                    for account in bucket:
                        if isinstance(account, dict):
                            out.append({"account": account, "state": role})
            return out
        return []

    inst.list_change_reviewers.side_effect = _list_change_reviewers
    inst.add_reviewer.return_value = {}
    inst.delete_reviewer.return_value = None
    inst.get_comments.return_value = {}
    inst.web_base = web_base

    client_cls = gerrit_client_class_stub(inst)

    with (
        patch(f"{module}.resolve_gerrit_web_base", return_value=web_base, create=True),
        patch(f"{module}.GerritClient", client_cls, create=True),
        patch(
            "gerrit_workflow_tools.core.gerrit.service.resolve_gerrit_web_base",
            return_value=web_base,
        ),
        patch("gerrit_workflow_tools.core.gerrit.service.GerritClient", client_cls),
    ):
        yield inst


def head_change_id(repo: Path) -> str:
    """Change-Id from ``HEAD`` commit message."""
    raw = git_out("log", "-1", "--format=%B", "HEAD", cwd=repo)
    cid = parse_change_id(raw)
    assert cid is not None
    return cid
