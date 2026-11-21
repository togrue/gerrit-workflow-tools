"""Call-budget tests: git subprocesses and Gerrit REST must stay O(1)/O(chunks), not O(N).

Protects the cache-first / batch-overlay principle in ``docu/plans/gerrit-log-performance.md``.
Counts are calibrated on ``stack_repo`` (and a larger synthetic stack for scaling).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.gerrit.rest import LIGHT_QUERY_OPTIONS, LOG_QUERY_OPTIONS
from gerrit_workflow_tools.core.git_run import _run_git, git, git_out
from tests.change_store import ChangeStore, RecordedCall
from tests.cli_gerrit_mocks import at_older_patchsets, build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target


# Calibrated on cold ``ger log`` / ``ger show`` / ``ger fix`` / ``ger resolve`` (Aug 2026).
# Leave modest headroom for small refactors; fail hard on O(N) storms.
_LOG_GIT_BUDGET = 6
_SHOW_GIT_BUDGET = 12
_FIX_GIT_BUDGET = 10
_RESOLVE_GIT_BUDGET = 10


def _git_subcommand_counts(run: MagicMock) -> dict[str, int]:
    counts: dict[str, int] = {}
    for call in run.call_args_list:
        git_args = call.args  # what follows ``git``: the seam is handed no program name
        if not git_args:
            continue
        if git_args[0] == "remote" and len(git_args) >= 2 and git_args[1] == "get-url":
            key = "remote get-url"
        elif git_args[0] == "branch" and "--show-current" in git_args:
            key = "branch --show-current"
        elif git_args[0] == "rev-parse" and "--show-toplevel" in git_args:
            key = "rev-parse --show-toplevel"
        else:
            key = git_args[0]
        counts[key] = counts.get(key, 0) + 1
    return counts


def _assert_log_git_budget(run: MagicMock) -> None:
    counts = _git_subcommand_counts(run)
    assert run.call_count <= _LOG_GIT_BUDGET, (
        f"git subprocesses={run.call_count} budget={_LOG_GIT_BUDGET}; counts={counts}; "
        f"args={[c.args[:5] for c in run.call_args_list]}"
    )
    assert counts.get("remote get-url", 0) == 0, counts
    assert counts.get("branch --show-current", 0) == 0, counts
    assert counts.get("rev-parse --show-toplevel", 0) <= 1, counts


def _configure_web(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


@contextmanager
def _count_git() -> Iterator[MagicMock]:
    """Wrap the git subprocess seam for the duration of one CLI invocation."""
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
    _assert_log_git_budget(run)


def test_log_git_and_rest_do_not_scale_with_stack_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doubling the stack must not double git or REST round-trips."""
    counts: list[tuple[int, int, int]] = []
    for n in (5, 20):
        # Both stacks share Change-Ids and the web base, so a shared cache would hand the
        # second run the first run's rows. Those name the first repo's commits, which differ
        # whenever the two stacks were committed in different seconds: ger log then checks
        # each one locally. Each run gets a cold cache of its own.
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / f"cache{n}"))
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
    _assert_log_git_budget(run)


def test_log_ahead_of_gerrit_does_not_scale_with_stack_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every commit rebased past its patch set: whether its code is the same is asked in one batch."""
    counts: list[int] = []
    for n in (5, 20):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / f"cache{n}"))
        repo = _make_linear_stack(tmp_path / f"n{n}", n)
        store = ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(repo)))
        git("rebase", "--force-rebase", "main", cwd=repo, env={"GIT_COMMITTER_DATE": "2001-01-01T00:00:00"})
        with _count_git() as run:
            code, out, err = run_cli(repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
        assert code in (0, 1), err
        assert "commit 1" in out
        counts.append(run.call_count)
    assert counts[1] == counts[0] <= _LOG_GIT_BUDGET + 3, counts


def _rev_parse_calls(run: MagicMock) -> list[tuple[str, ...]]:
    return [call.args for call in run.call_args_list if call.args[:1] == ("rev-parse",)]


def _all_merged(details: dict[str, dict[str, Any]], _repo: Path) -> dict[str, dict[str, Any]]:
    for detail in details.values():
        detail["status"] = "MERGED"
    return details


@pytest.mark.parametrize(
    ("argv", "gerrit_state"),
    [
        pytest.param([], None, id="pushed"),
        pytest.param(["-vv", "--show-change-id"], None, id="verbose"),
        pytest.param(["--json"], None, id="json"),
        pytest.param([], at_older_patchsets, id="ahead-of-gerrit"),
        pytest.param([], _all_merged, id="merged"),
        pytest.param(["main..feature"], None, id="explicit-range"),
        pytest.param(["--follow-merges"], None, id="follow-merges"),
    ],
)
def test_log_rev_parse_does_not_scale_with_stack_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    gerrit_state: Callable[[dict[str, dict[str, Any]], Path], dict[str, dict[str, Any]]] | None,
) -> None:
    """``ger log`` resolves its range and upstream with the same ``git rev-parse`` calls on any stack.

    Nothing about a commit is looked up with a ``rev-parse`` of its own: the stack's rows come
    from one ``git log``, and what the overlay asks about them is asked in batches.
    """
    rev_parses: list[list[tuple[str, ...]]] = []
    for n in (3, 12):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / f"cache{n}"))
        repo = _make_linear_stack(tmp_path / f"n{n}", n)
        details = build_details_by_change_id(stack_rows_mb_to_head(repo))
        store = ChangeStore(gerrit_state(details, repo) if gerrit_state else details)
        with _count_git() as run:
            code, out, err = run_cli(repo, log_main, ["--color=never", *argv], monkeypatch, gerrit=store)
        assert code in (0, 1), err
        assert "commit 1" in out
        rev_parses.append(_rev_parse_calls(run))
    small, large = rev_parses
    assert len(large) == len(small) <= 2, rev_parses


def test_show_call_budget_on_head(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger show HEAD`` resolves one change — a handful of git + REST calls, not a stack storm."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(
        build_details_by_change_id(rows),
        accounts={1000: {"_account_id": 1000, "username": "me", "name": "me"}},
        self_account_id=1000,
    )

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, show_main, ["HEAD", "--color=never"], monkeypatch, gerrit=store)

    assert code == 0, err
    # Resolution probe + detail query; no CI fetch without -v when Verified passes. Who you are is
    # asked once per user and host (the change has a reviewer, so whether it waits on you could
    # matter), then remembered: a second show does not ask it.
    assert len(store.calls_to("get_account")) == 1
    assert len(store.calls) <= 5, [c.method for c in store.calls]
    assert len(store.queries()) <= 2, store.queries()
    assert store.calls_to("list_change_reviewers") == []
    assert len(store.calls_to("get_change")) == 0
    assert run.call_count <= _SHOW_GIT_BUDGET, (
        f"git subprocesses={run.call_count} budget={_SHOW_GIT_BUDGET}; args={[c.args[:4] for c in run.call_args_list]}"
    )
    before = len(store.calls)
    code, _out, err = run_cli(stack_repo, show_main, ["HEAD", "--color=never"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert [c.method for c in store.calls[before:] if c.method == "get_account"] == []
    # Your own change does not list you as a reviewer: the to-review query is not asked.
    assert not [q for q in store.queries() if "reviewer:self" in q]


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


def test_no_request_asks_gerrit_for_the_diffstat(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every changes query says ``SKIP_DIFFSTAT``: nothing reads Gerrit's line counts.

    Without it Gerrit opens the commits of every row to count inserted and deleted lines.
    """
    from gerrit_workflow_tools.core.gerrit.rest import LOG_QUERY_OPTIONS

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    cid = rows[0].change_id
    assert cid
    store = ChangeStore(build_details_by_change_id(rows))

    for main, argv in ((log_main, ["--color=never"]), (show_main, ["HEAD", "--color=never"]), (resolve_main, [cid])):
        code, _out, err = run_cli(stack_repo, main, argv, monkeypatch, gerrit=store)
        assert code in (0, 1), err

    queries = store.calls_to("query_changes")
    assert len(queries) >= 3
    assert [q.args[0] for q in queries if "SKIP_DIFFSTAT" not in (q.kwargs.get("options") or [])] == []
    assert "SKIP_DIFFSTAT" in LOG_QUERY_OPTIONS  # what GET changes/<id> sends


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


def test_log_warm_second_run_zero_rest_calls(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Warm ``ger log`` inside the trust window makes no new Gerrit calls."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    after_first = len(store.calls)
    assert after_first >= 1, "cold run should populate cache via REST"

    with _count_git() as run:
        code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    assert len(store.calls) == after_first, (
        f"warm run added REST calls: {[c.method for c in store.calls[after_first:]]}"
    )
    _assert_log_git_budget(run)


def test_log_warm_second_run_no_reviewer_follow_ups(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Payloads with ``reviewers`` must not trigger ``list_change_reviewers`` on cold or warm run."""
    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    assert store.calls_to("list_change_reviewers") == []

    code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    assert store.calls_to("list_change_reviewers") == []


def test_log_after_the_trust_window_is_one_probe_and_then_nothing(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the trust window an unchanged stack costs Gerrit one light query, naming the stack's changes.

    Never a query over the project (``since:``): on a busy project that returns everybody's
    changes in full to learn nothing the probe does not say (ADR-0013). The probe's answer
    is trusted for the next window like a fetch, so a second command right after asks nothing.
    """
    import gerrit_workflow_tools.core.gerrit.cache as cache_mod

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    for idx, payload in enumerate(details.values()):
        payload["updated"] = f"2024-01-01 10:00:{idx:02d}.000000000"
    store = ChangeStore(details)

    clock = {"t": 1000}
    monkeypatch.setattr(cache_mod, "_now", lambda: clock["t"])

    def log_calls() -> list[RecordedCall]:
        before = len(store.calls)
        code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
        assert code in (0, 1), err
        return store.calls[before:]

    assert len(log_calls()) == 1

    clock["t"] = 1020
    (probe,) = log_calls()
    assert probe.method == "query_changes"
    assert "change:" in probe.args[0] and "since:" not in probe.args[0], probe.args[0]
    assert probe.kwargs.get("options") == ["SKIP_DIFFSTAT"], probe

    clock["t"] = 1029
    assert log_calls() == []


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


def test_show_asks_who_carries_the_change_id_once_and_needs_no_probe(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the trust window ``ger show HEAD`` sends one changes query, and none when run again at once.

    The Change-Id lookup already says whether the cached change moved, so the stack's own
    probe is not sent after it; and its answer is kept for the trust window like a change.
    """
    import gerrit_workflow_tools.core.gerrit.cache as cache_mod

    _configure_web(stack_repo)
    details = build_details_by_change_id(stack_rows_mb_to_head(stack_repo))
    for idx, payload in enumerate(details.values()):
        payload["updated"] = f"2024-01-01 10:00:{idx:02d}.000000000"
    store = ChangeStore(details)
    clock = {"t": 1000}
    monkeypatch.setattr(cache_mod, "_now", lambda: clock["t"])

    def queries(main: object, argv: list[str]) -> list[RecordedCall]:
        before = len(store.calls)
        code, _out, err = run_cli(stack_repo, main, argv, monkeypatch, gerrit=store)  # type: ignore[arg-type]
        assert code in (0, 1), err
        return [call for call in store.calls[before:] if call.method == "query_changes"]

    queries(log_main, ["--color=never"])

    clock["t"] = 1020
    (lookup,) = queries(show_main, ["HEAD", "--color=never"])
    assert lookup.args[0].startswith("change:I"), lookup.args[0]
    assert lookup.kwargs["options"] == list(LIGHT_QUERY_OPTIONS)  # the cached change is what it is held against

    clock["t"] = 1025
    assert queries(show_main, ["HEAD", "--color=never"]) == []
    assert queries(resolve_main, ["HEAD"]) == []

    clock["t"] = 1040
    assert len(queries(resolve_main, ["HEAD"])) == 1


def test_show_on_a_cold_cache_asks_for_the_change_once_in_full(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing cached for the Change-Id, the lookup asks for the details too: no second query for the stack.

    A light answer is only worth asking for when there is a cached row to hold it against.
    """
    _configure_web(stack_repo)
    store = ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(stack_repo)))

    code, _out, err = run_cli(stack_repo, show_main, ["HEAD", "--color=never"], monkeypatch, gerrit=store)

    assert code == 0, err
    (lookup,) = store.calls_to("query_changes")
    assert lookup.args[0].startswith("change:I"), lookup.args[0]
    assert lookup.kwargs["options"] == list(LOG_QUERY_OPTIONS)
    assert store.calls_to("get_change") == []
