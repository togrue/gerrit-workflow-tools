"""The upstream suggestion: the nearest remote branch, the same answer on every git, and git calls
that do not grow per remote branch.

``infer_nearest_remote_tracking_branch`` measures every remote branch in one ``for-each-ref`` on
git 2.41+, else one by one. The one-by-one path is pinned against the four-calls-per-ref
implementation it replaced (kept below as the reference) and against the 2.41+ path.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.core import upstream
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import _run_git, git, git_out
from gerrit_workflow_tools.core.upstream import infer_nearest_remote_tracking_branch


PREFIX = "refs/remotes/origin/"


def _reference_one_by_one(cwd: Path | str | None, head: str, prefix: str) -> list[tuple[str, int, int]]:
    """The one-by-one fallback as it was before it was batched: merge-base, two counts, abbrev, per ref.

    Frozen on purpose, kept only as an oracle for :func:`upstream._divergence_one_by_one`; delete it
    (and the tests comparing against it) once the fallback is settled.
    """
    p = git("for-each-ref", "--format=%(refname)", prefix, cwd=cwd, check=False)
    rows: list[tuple[str, int, int]] = []
    for line in p.stdout.splitlines() if p.returncode == 0 else []:
        ref = line.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        mb_p = git("merge-base", head, ref, cwd=cwd, check=False)
        if mb_p.returncode != 0:
            continue
        mb = mb_p.stdout.strip()
        ahead_p = git("rev-list", "--count", f"{mb}..{head}", cwd=cwd, check=False)
        behind_p = git("rev-list", "--count", f"{mb}..{ref}", cwd=cwd, check=False)
        abbrev_p = git("rev-parse", "--abbrev-ref", ref, cwd=cwd, check=False)
        counts = (ahead_p.stdout.strip(), behind_p.stdout.strip())
        if any(c.returncode != 0 for c in (ahead_p, behind_p, abbrev_p)) or not all(n.isdigit() for n in counts):
            continue
        rows.append((abbrev_p.stdout.strip(), int(counts[0]), int(counts[1])))
    return rows


def test_infer_nearest_remote_tracking_branch(tmp_path: Path) -> None:
    """Symmetric divergence vs origin/main: one local commit on feature."""
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    main_sha = git_out("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    (repo / "f").write_text("2\n", encoding="utf-8")
    git("commit", "-am", "feat", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", main_sha, cwd=repo)
    got = infer_nearest_remote_tracking_branch(repo, settings=Settings.from_cwd(repo))
    assert got is not None
    abbrev, sym, ahead, behind = got
    assert abbrev == "origin/main"
    assert sym == 1 and ahead == 1 and behind == 0


def _commit(repo: Path, msg: str) -> str:
    (repo / "f.txt").write_text(msg + "\n", encoding="utf-8")
    git("add", "f.txt", cwd=repo)
    git("commit", "-q", "-m", msg, cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def _remote_branch(repo: Path, name: str, rev: str) -> None:
    git("update-ref", f"refs/remotes/origin/{name}", rev, cwd=repo)


def _repo(tmp_path: Path, n_main: int = 10) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    for i in range(n_main):
        _commit(repo, f"main {i}")
    git("remote", "add", "origin", repo.as_uri(), cwd=repo)
    return repo


def _topic_on_ancestors(tmp_path: Path) -> Path:
    """Remote branches at every commit of main; topic forks from the middle."""
    repo = _repo(tmp_path)
    for i in range(10):
        _remote_branch(repo, f"rel{i}", f"main~{i}")
    git("checkout", "-q", "-b", "topic", "main~5", cwd=repo)
    _commit(repo, "topic 1")
    _commit(repo, "topic 2")
    return repo


def _topic_among_diverged(tmp_path: Path) -> Path:
    """Release branches forked from main with commits of their own; topic forks from main too."""
    repo = _repo(tmp_path)
    for i in range(6):
        git("checkout", "-q", "-b", f"r{i}", f"main~{i}", cwd=repo)
        for k in range(i % 3 + 1):
            _commit(repo, f"r{i} {k}")
        _remote_branch(repo, f"release{i}", "HEAD")
    _remote_branch(repo, "main", "main")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=repo)
    git("checkout", "-q", "-b", "topic", "main~2", cwd=repo)
    _commit(repo, "topic 1")
    return repo


def _topic_with_ties_and_descendants(tmp_path: Path) -> Path:
    """Several branches at the same commit (a tie broken by name), one at topic, one past it."""
    repo = _repo(tmp_path)
    git("checkout", "-q", "-b", "topic", "main~3", cwd=repo)
    _commit(repo, "topic 1")
    for name in ("zeta", "alpha", "mid"):
        _remote_branch(repo, name, "main~3")
    _remote_branch(repo, "same", "topic")
    git("checkout", "-q", "-b", "ahead", "topic", cwd=repo)
    _commit(repo, "past topic")
    _remote_branch(repo, "past", "HEAD")
    git("checkout", "-q", "topic", cwd=repo)
    return repo


def _topic_with_a_symbolic_ref(tmp_path: Path) -> Path:
    """The ties-and-descendants repo, plus ``origin/aaa``: a symbolic ref to ``origin/same``, not HEAD."""
    repo = _topic_with_ties_and_descendants(tmp_path)
    git("symbolic-ref", "refs/remotes/origin/aaa", "refs/remotes/origin/same", cwd=repo)
    return repo


def _topic_beside_a_release_past_main(tmp_path: Path) -> Path:
    """origin/main, origin/release one commit past it, topic one commit on main."""
    repo = tmp_path / "r3"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    _commit(repo, "one")
    _remote_branch(repo, "main", "HEAD")
    _commit(repo, "two")
    _remote_branch(repo, "release", "HEAD")
    git("checkout", "-q", "-b", "topic", "main", cwd=repo)
    git("reset", "-q", "--hard", "HEAD~1", cwd=repo)
    (repo / "g").write_text("x\n", encoding="utf-8")
    git("add", "g", cwd=repo)
    git("commit", "-q", "-m", "feat", cwd=repo)
    return repo


def _topic_criss_cross(tmp_path: Path) -> Path:
    """Two merge bases between topic and the remote branch, and an unrelated history beside it."""
    repo = _repo(tmp_path, n_main=2)
    git("checkout", "-q", "-b", "a", "main", cwd=repo)
    a1 = _commit(repo, "a1")
    git("checkout", "-q", "-b", "b", "main", cwd=repo)
    b1 = _commit(repo, "b1")
    git("checkout", "-q", "a", cwd=repo)
    git("merge", "-q", "--no-edit", "-s", "ours", b1, cwd=repo)
    _commit(repo, "a2")
    git("checkout", "-q", "b", cwd=repo)
    git("merge", "-q", "--no-edit", "-s", "ours", a1, cwd=repo)
    _commit(repo, "b2")
    _remote_branch(repo, "b", "b")
    _remote_branch(repo, "main", "main")
    git("checkout", "-q", "--orphan", "lonely", cwd=repo)
    _commit(repo, "unrelated")
    _remote_branch(repo, "lonely", "lonely")
    git("checkout", "-q", "-b", "topic", "a", cwd=repo)
    return repo


SCENARIOS: dict[str, Callable[[Path], Path]] = {
    "ancestors": _topic_on_ancestors,
    "diverged": _topic_among_diverged,
    "ties-and-descendants": _topic_with_ties_and_descendants,
    "criss-cross": _topic_criss_cross,
    "symref": _topic_with_a_symbolic_ref,
    "release-past-main": _topic_beside_a_release_past_main,
}

# The old fallback named a ref by ``rev-parse --abbrev-ref``, which follows a symbolic ref and names
# its target; ``%(refname:short)`` (both paths now) names the symbolic ref itself. Mapping the new
# names to the target lets the oracle check the counts; the name is pinned against 2.41+ instead.
_OLD_FALLBACK_NAMES: dict[str, dict[str, str]] = {"symref": {"origin/aaa": "origin/same"}}

# Two merge bases: ``<merge-base>..<head>`` is not git's ahead count, so the paths may differ there.
_PATHS_MAY_DIFFER = {"criss-cross"}


@contextmanager
def _count_git() -> Iterator[MagicMock]:
    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        yield run


def _older_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upstream, "_divergence_at_once", lambda *_a, **_k: None)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_older_git_measures_every_remote_branch_as_before(tmp_path: Path, scenario: str) -> None:
    repo = SCENARIOS[scenario](tmp_path)
    expected = _reference_one_by_one(repo, "topic", PREFIX)
    assert expected, "the scenario has remote branches to measure"
    renamed = _OLD_FALLBACK_NAMES.get(scenario, {})
    got = [
        (renamed.get(abbrev, abbrev), ahead, behind)
        for abbrev, ahead, behind in upstream._divergence_one_by_one(repo, "topic", PREFIX)
    ]
    assert sorted(got) == sorted(expected)


@pytest.mark.parametrize("scenario", sorted(set(SCENARIOS) - _PATHS_MAY_DIFFER))
def test_older_git_measures_every_remote_branch_as_git_2_41_does(tmp_path: Path, scenario: str) -> None:
    repo = SCENARIOS[scenario](tmp_path)
    at_once = upstream._divergence_at_once(repo, "topic", PREFIX)
    if at_once is None:
        pytest.skip("this git has no %(ahead-behind)")
    assert sorted(upstream._divergence_one_by_one(repo, "topic", PREFIX)) == sorted(at_once)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_suggestion_is_the_same_with_and_without_ahead_behind(
    tmp_path: Path, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = SCENARIOS[scenario](tmp_path)
    settings = Settings.from_cwd(repo)
    batched = upstream.upstream_choice(repo, "topic", settings=settings)
    _older_git(monkeypatch)
    one_by_one = upstream.upstream_choice(repo, "topic", settings=settings)
    monkeypatch.setattr(upstream, "_divergence_one_by_one", _reference_one_by_one)
    reference = upstream.upstream_choice(repo, "topic", settings=settings)
    assert one_by_one.suggested is not None
    if scenario not in _PATHS_MAY_DIFFER:
        assert one_by_one == batched
    if scenario not in _OLD_FALLBACK_NAMES:
        assert one_by_one == reference


def test_a_symbolic_ref_of_the_remote_is_suggested_by_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``origin/aaa`` -> ``origin/same`` (at topic) wins the tie by name, on every git."""
    repo = _topic_with_a_symbolic_ref(tmp_path)
    settings = Settings.from_cwd(repo)
    _older_git(monkeypatch)
    assert infer_nearest_remote_tracking_branch(repo, "topic", settings=settings) == ("origin/aaa", 0, 0, 0)


def test_the_nearest_branch_is_the_one_topic_left_not_a_release_past_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _topic_beside_a_release_past_main(tmp_path)
    settings = Settings.from_cwd(repo)
    assert infer_nearest_remote_tracking_branch(repo, "topic", settings=settings) == ("origin/main", 1, 1, 0)
    _older_git(monkeypatch)
    assert infer_nearest_remote_tracking_branch(repo, "topic", settings=settings) == ("origin/main", 1, 1, 0)


def test_older_git_measures_nothing_for_a_head_that_does_not_resolve(tmp_path: Path) -> None:
    repo = _topic_on_ancestors(tmp_path)
    assert upstream._divergence_one_by_one(repo, "no-such-branch", PREFIX) == []


def test_older_git_measures_every_ref_when_merged_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the ``--merged`` list every ref gets its ``merge-base``; the answer is the same."""
    repo = _topic_on_ancestors(tmp_path)
    expected = upstream._divergence_one_by_one(repo, "topic", PREFIX)
    real_git = upstream.git

    def git_without_merged(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(a.startswith("--merged") for a in args):
            return subprocess.CompletedProcess(list(args), 129, "", "error: unknown option\n")
        return real_git(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(upstream, "git", git_without_merged)
    with _count_git() as run:
        got = upstream._divergence_one_by_one(repo, "topic", PREFIX)
    assert sorted(got) == sorted(expected) and len(got) == 10
    merge_bases = [call.args for call in run.call_args_list if call.args[0] == "merge-base"]
    assert len(merge_bases) == len(got), merge_bases


def _many_remote_branches(tmp_path: Path, n: int) -> Path:
    repo = _repo(tmp_path / f"n{n}", n_main=n + 2)
    for i in range(n):
        _remote_branch(repo, f"rel{i}", f"main~{i + 2}")
    git("checkout", "-q", "-b", "topic", "main", cwd=repo)
    _commit(repo, "topic 1")
    return repo


def test_the_suggestion_costs_the_same_git_calls_for_ten_or_thirty_remote_branches(
    tmp_path: Path,
) -> None:
    """git 2.41+ only: on an older git the fallback grows per branch (pinned below)."""
    if upstream._divergence_at_once(_many_remote_branches(tmp_path / "probe", 1), "topic", PREFIX) is None:
        pytest.skip("this git has no %(ahead-behind)")
    counts = []
    for n in (10, 30):
        repo = _many_remote_branches(tmp_path, n)
        settings = Settings.from_cwd(repo)
        with _count_git() as run:
            choice = upstream.upstream_choice(repo, "topic", settings=settings)
        assert choice.suggested == "origin/rel0"
        counts.append(run.call_count)
    assert counts[0] == counts[1] <= 6, counts


def test_older_git_skips_merge_base_for_remote_branches_under_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thirty ancestors of the topic: one ``rev-list`` each, no ``merge-base`` (it was four calls each)."""
    n = 30
    repo = _many_remote_branches(tmp_path, n)
    with _count_git() as run:
        rows = upstream._divergence_one_by_one(repo, "topic", PREFIX)
    assert len(rows) == n
    subcommands = [call.args[0] for call in run.call_args_list]
    assert "merge-base" not in subcommands, subcommands
    assert run.call_count <= 3 + n, run.call_count
