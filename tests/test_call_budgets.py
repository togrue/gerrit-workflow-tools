"""Call-budget tests: git subprocesses and Gerrit REST must stay O(1)/O(chunks), not O(N).

Protects the cache-first / batch-overlay principle in ``docu/plans/gerrit-log-performance.md``.
Counts are calibrated on ``stack_repo`` (and a larger synthetic stack for scaling).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import _run_git, clear_git_cache, git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target

# Calibrated on cold ``ger log`` / ``ger show`` / ``ger fix`` / ``ger resolve`` (Jul 2026).
# Leave modest headroom for small refactors; fail hard on O(N) storms.
_LOG_GIT_BUDGET = 20
_SHOW_GIT_BUDGET = 20
_FIX_GIT_BUDGET = 10
_RESOLVE_GIT_BUDGET = 10


def _configure_web(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


@contextmanager
def _count_git() -> Iterator[MagicMock]:
    """Wrap the git subprocess seam for the duration of one CLI invocation."""
    clear_git_cache()
    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        yield run


def _make_linear_stack(root: Path, n: int) -> Path:
    """``main`` + ``feature`` with *n* Change-Id commits (oldest → newest)."""
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    git("add", "f.txt", cwd=repo)
    git("commit", "-m", "base\n\nChange-Id: I0000000000000000000000000000000000000000", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    for i in range(1, n + 1):
        (repo / "f.txt").write_text(f"c{i}\n", encoding="utf-8")
        git("add", "f.txt", cwd=repo)
        git("commit", "-m", f"commit {i}\n\nChange-Id: I{i:040d}", cwd=repo)
    git("remote", "add", "origin", repo.as_uri(), cwd=repo)
    git("update-ref", "refs/remotes/origin/main", "main", cwd=repo)
    configure_gerrit_target(repo, "main")
    git("config", "gerrit.project", "testproj", cwd=repo, check=False)
    _configure_web(repo)
    return repo


def test_log_call_budget_on_stack_repo(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cold ``ger log``: one project-scoped batch query, no bare change:I, ≪ N git calls."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 3
    store = ChangeStore(build_details_by_change_id(rows))

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)

    assert code in (0, 1), err
    assert len(store.queries()) == 1, store.queries()
    assert all("project:" in q for q in store.queries()), store.queries()
    assert not any(q.startswith("change:") for q in store.queries()), store.queries()
    assert store.calls_to("list_change_reviewers") == [], "reviewers already on ChangeInfo"
    assert store.calls_to("get_change") == []
    assert run.call_count <= _LOG_GIT_BUDGET, (
        f"git subprocesses={run.call_count} budget={_LOG_GIT_BUDGET}; args={[c.args[:4] for c in run.call_args_list]}"
    )


def test_log_git_and_rest_do_not_scale_with_stack_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doubling the stack must not double git or REST round-trips."""
    counts: list[tuple[int, int, int]] = []
    for n in (5, 20):
        repo = _make_linear_stack(tmp_path / f"n{n}", n)
        rows = stack_rows_mb_to_head(repo)
        assert len(rows) == n
        store = ChangeStore(build_details_by_change_id(rows))
        with _count_git() as run:
            code, _out, err = run_cli(repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
        assert code in (0, 1), err
        counts.append((n, run.call_count, len(store.calls)))
        assert len(store.queries()) == 1
        assert not any(q.startswith("change:") for q in store.queries())

    (_n_small, git_small, rest_small), (_n_large, git_large, rest_large) = counts
    assert git_large <= git_small + 2, f"git scaled with N: {counts}"
    assert rest_large == rest_small == 1, f"REST scaled with N: {counts}"
    assert git_large <= _LOG_GIT_BUDGET


def test_show_call_budget_on_head(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger show HEAD`` resolves one change — a handful of git + REST calls, not a stack storm."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, show_main, ["HEAD", "--color=never"], monkeypatch, gerrit=store)

    assert code == 0, err
    # Resolution probe + detail query + comments follow-up (not a stack-wide storm).
    assert len(store.calls) <= 3, [c.method for c in store.calls]
    assert len(store.queries()) <= 2, store.queries()
    assert store.calls_to("list_change_reviewers") == []
    assert len(store.calls_to("get_change")) == 0
    assert run.call_count <= _SHOW_GIT_BUDGET, (
        f"git subprocesses={run.call_count} budget={_SHOW_GIT_BUDGET}; args={[c.args[:4] for c in run.call_args_list]}"
    )


def test_resolve_call_budget_for_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger resolve`` for one Change-Id: one query, O(1) git."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    cid = rows[0].change_id
    assert cid
    store = ChangeStore(build_details_by_change_id(rows))

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, resolve_main, [cid, "--json"], monkeypatch, gerrit=store)

    assert code == 0, err
    assert len(store.queries()) == 1
    assert len(store.calls) == 1
    assert run.call_count <= _RESOLVE_GIT_BUDGET, f"git subprocesses={run.call_count} budget={_RESOLVE_GIT_BUDGET}"


def test_fix_local_ref_makes_no_gerrit_calls(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger fix`` on a local stack ref never asks Gerrit (ADR-0003)."""
    _configure_web(stack_repo)
    (stack_repo / "a.txt").write_text("fix budget\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    store = ChangeStore({})

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, fix_main, ["HEAD~1"], monkeypatch, gerrit=store)

    assert code == 0, err
    assert store.calls == []
    assert run.call_count <= _FIX_GIT_BUDGET, (
        f"git subprocesses={run.call_count} budget={_FIX_GIT_BUDGET}; args={[c.args[:4] for c in run.call_args_list]}"
    )
    assert git_out("log", "-1", "--format=%s", cwd=stack_repo).startswith("fixup! ")


def test_fix_change_id_makes_no_gerrit_calls(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare Change-Id is matched on the local stack — zero REST (ADR-0003)."""
    rows = stack_rows_mb_to_head(stack_repo)
    cid = rows[1].change_id
    assert cid
    (stack_repo / "b.txt").write_text("fix by cid\n", encoding="utf-8")
    git("add", "b.txt", cwd=stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, fix_main, [cid], monkeypatch, gerrit=store)

    assert code == 0, err
    assert store.calls == []
    assert run.call_count <= _FIX_GIT_BUDGET
