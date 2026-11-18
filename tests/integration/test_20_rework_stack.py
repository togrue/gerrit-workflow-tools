"""Integration: reworking a pushed chain (reorder, squash, split, amend below), then pushing it again.

Each test pushes a chain of four commits, reworks it the way a user would (``ger rebase`` with
an edited todo, ``ger edit`` and an amend), pushes again with ``ger push`` and then reads
Gerrit's state back over REST:

- every change of the local chain is at the local commit, with a new patchset exactly where
  the commit changed (its content or its parent);
- each current patchset sits on the current patchset of the change below it, and Gerrit's
  relation chain lists the local order with every member at its current patchset (the web UI
  marks a member that is not with *NOT CURRENT*);
- ``ger log --json`` links each local commit to the change Gerrit has for its Change-Id.

The todo is edited by a small script given to ``ger rebase`` as ``GREBASE_EDITOR`` (the editor
the enricher opens after annotating the todo), so the rework goes through ger's own rebase.
"""

from __future__ import annotations

import secrets
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_edit import main as ger_edit_main
from gerrit_workflow_tools.cli_rebase import main as ger_rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    ChainCommit,
    chain_commits_oldest_first,
    change_detail,
    ger_push,
    log_commits_by_change_id,
    prepare_topic_repo,
    run_ger,
)


@dataclass(frozen=True)
class _OnGerrit:
    """What Gerrit has for one change of the chain after a push."""

    number: int
    patchset: int
    sha: str


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    """One commit writing *files*; the commit-msg hook adds the Change-Id.

    A random line per file keeps two runs on the same base from making byte-identical
    commits (Gerrit creates no change for a commit it already has).
    """
    for name, text in files.items():
        (repo / name).write_text(f"{text}\n{secrets.token_hex(8)}\n", encoding="utf-8")
        git("add", name, cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)


def _pushed_chain(
    ctx: GerritIntegrationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[Path, str, list[ChainCommit]]:
    """Push four commits, each on its own files; commit 1 writes two (so it can be split)."""
    branch = f"{name}_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, branch)
    _commit(repo, f"{name} 0", {"rework_0.txt": "zero"})
    _commit(repo, f"{name} 1", {"rework_1a.txt": "one a", "rework_1b.txt": "one b"})
    _commit(repo, f"{name} 2", {"rework_2.txt": "two"})
    _commit(repo, f"{name} 3", {"rework_3.txt": "three"})
    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    assert len(chain) == 4
    return repo, branch, chain


def _related(session: GerritHttpSession, change_number: int) -> list[dict[str, Any]]:
    """Gerrit's relation chain of the current patchset, newest first (as the web UI lists it)."""
    data = session.get_json(f"changes/{change_number}/revisions/current/related")
    return list(data.get("changes") or [])


def _assert_gerrit_agrees(
    session: GerritHttpSession,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project: str,
    branch: str,
    before: dict[str, _OnGerrit],
) -> dict[str, _OnGerrit]:
    """Check Gerrit and ``ger log`` against the local chain; return what Gerrit has now.

    *before* is what Gerrit had before the push: a change whose commit is unchanged keeps its
    patchset, a changed one gets exactly one more, a Change-Id Gerrit did not know starts at 1.
    """
    local = chain_commits_oldest_first(repo)
    after: dict[str, _OnGerrit] = {}
    parent_sha = git_out("rev-parse", f"{local[0].sha}^", cwd=repo).strip()
    for commit in local:
        detail = change_detail(session, f"{project}~{branch}~{commit.change_id}")
        assert detail["status"] == "NEW", commit.subject
        current = detail["current_revision"]
        assert current == commit.sha, f"{commit.subject}: Gerrit's current patchset is not the local commit"
        patchset = int(detail["revisions"][current]["_number"])
        old = before.get(commit.change_id)
        expected = 1 if old is None else old.patchset + (0 if old.sha == commit.sha else 1)
        assert patchset == expected, commit.subject
        # The current patchset sits on the current patchset of the change below (or the branch).
        info = session.get_json(f"changes/{detail['_number']}/revisions/current/commit")
        assert [p["commit"] for p in info["parents"]] == [parent_sha], commit.subject
        after[commit.change_id] = _OnGerrit(number=int(detail["_number"]), patchset=patchset, sha=commit.sha)
        parent_sha = commit.sha

    newest_first = [c.change_id for c in reversed(local)]
    # From the tip, the relation chain is exactly the local chain: its ancestors, nothing above.
    tip_related = _related(session, after[local[-1].change_id].number)
    assert [r["change_id"] for r in tip_related] == newest_first
    for commit in local:
        related = _related(session, after[commit.change_id].number)
        members = [r for r in related if r["change_id"] in after]
        assert [r["change_id"] for r in members] == newest_first, commit.subject
        for r in members:
            # The web UI marks a member whose listed patchset is not its current one NOT CURRENT.
            assert r["_revision_number"] == r["_current_revision_number"], (commit.subject, r["change_id"])
            assert r["commit"]["commit"] == after[r["change_id"]].sha, (commit.subject, r["change_id"])

    rows = log_commits_by_change_id(repo, monkeypatch)
    assert list(rows) == [c.change_id for c in local]
    for commit in local:
        row = rows[commit.change_id]
        assert row["sha"] == commit.sha
        assert row["number"] == after[commit.change_id].number
        assert row["patchset_status"] == "active", commit.subject
    return after


def _first_push(
    session: GerritHttpSession,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project: str,
    branch: str,
) -> dict[str, _OnGerrit]:
    state = _assert_gerrit_agrees(session, repo, monkeypatch, project=project, branch=branch, before={})
    assert {s.patchset for s in state.values()} == {1}
    return state


def _ger_rebase_with_todo(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, todo: list[str], *, shown: list[ChainCommit]
) -> None:
    """``ger rebase`` whose editor replaces the annotated todo with *todo* lines (full SHAs).

    Checks first that the todo ger opened listed *shown* in order: ger's annotations sit on
    the ``pick`` lines, so only the verb and the SHA are compared.
    """
    seen = tmp_path / f"todo_seen_{secrets.token_hex(4)}.txt"
    todo_text = "".join(f"{line}\n" for line in todo)
    script = tmp_path / f"todo_editor_{secrets.token_hex(4)}.py"
    script.write_text(
        "import pathlib, sys\n"
        "todo = pathlib.Path(sys.argv[1])\n"
        f"pathlib.Path({str(seen)!r}).write_text(todo.read_text(encoding='utf-8'), encoding='utf-8')\n"
        f"todo.write_text({todo_text!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    # run_git_editor splits the command with shlex: forward slashes survive it on Windows too.
    editor = f"{shlex.quote(Path(sys.executable).as_posix())} -I {shlex.quote(script.as_posix())}"
    monkeypatch.setenv("GREBASE_EDITOR", editor)
    code, out, err = run_ger(repo, ger_rebase_main, [], monkeypatch)
    monkeypatch.delenv("GREBASE_EDITOR")
    assert code == 0, f"{out}\n{err}"
    seen_text = seen.read_text(encoding="utf-8")
    assert "Gerrit enrichment failed" not in seen_text
    picks = [line.split()[:2] for line in seen_text.splitlines() if line.startswith("pick ")]
    assert [verb for verb, _sha in picks] == ["pick"] * len(shown)
    assert all(c.sha.startswith(sha) for (_verb, sha), c in zip(picks, shown, strict=True))
    assert not (repo / ".git" / "rebase-merge").exists(), "the rebase stopped"


def test_reordered_chain_repushes_with_gerrit_relation_chain_in_the_new_order(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch, chain = _pushed_chain(ctx, tmp_path, monkeypatch, "reorder")
    c0, c1, c2, c3 = chain
    before = _first_push(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch)

    # Swap commits 1 and 2: they touch different files, so the picks apply cleanly.
    _ger_rebase_with_todo(repo, tmp_path, monkeypatch, [f"pick {c.sha}" for c in (c0, c2, c1, c3)], shown=chain)
    reordered = chain_commits_oldest_first(repo)
    assert [c.change_id for c in reordered] == [c0.change_id, c2.change_id, c1.change_id, c3.change_id]
    assert reordered[0].sha == c0.sha

    ger_push(repo, monkeypatch)
    after = _assert_gerrit_agrees(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch, before=before)
    # Commit 0 did not move; the three above it each have a new parent.
    assert [after[c.change_id].patchset for c in reordered] == [1, 2, 2, 2]


def test_squashed_chain_repushes_and_the_squashed_away_change_stays_open_on_gerrit(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch, chain = _pushed_chain(ctx, tmp_path, monkeypatch, "squash")
    c0, c1, c2, c3 = chain
    before = _first_push(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch)

    # Fold commit 2 into commit 1 with `fixup`: the result keeps commit 1's message, so its
    # Change-Id. (A `squash` would join both messages and leave commit 2's Change-Id last,
    # in the footer, where git, Gerrit and ger all read it.)
    _ger_rebase_with_todo(
        repo,
        tmp_path,
        monkeypatch,
        [f"pick {c0.sha}", f"pick {c1.sha}", f"fixup {c2.sha}", f"pick {c3.sha}"],
        shown=chain,
    )
    squashed = chain_commits_oldest_first(repo)
    assert [c.change_id for c in squashed] == [c0.change_id, c1.change_id, c3.change_id]
    assert (repo / "rework_2.txt").is_file()
    assert git_out("show", "--format=", "--name-only", squashed[1].sha, cwd=repo).split() == sorted(
        ["rework_1a.txt", "rework_1b.txt", "rework_2.txt"]
    )

    ger_push(repo, monkeypatch)
    after = _assert_gerrit_agrees(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch, before=before)
    assert [after[c.change_id].patchset for c in squashed] == [1, 2, 2]

    # Gerrit keeps the change that was squashed away: open, at the patchset it had, on
    # commit 1's old patchset. Nothing in the local chain names it any more.
    orphan = change_detail(gerrit_admin_session, f"{proj}~{branch}~{c2.change_id}")
    assert orphan["status"] == "NEW"
    assert orphan["current_revision"] == c2.sha
    assert int(orphan["revisions"][c2.sha]["_number"]) == 1
    orphan_commit = gerrit_admin_session.get_json(f"changes/{orphan['_number']}/revisions/current/commit")
    assert [p["commit"] for p in orphan_commit["parents"]] == [c1.sha]
    assert c2.change_id not in log_commits_by_change_id(repo, monkeypatch)


def test_split_commit_repushes_as_its_change_and_a_new_change_on_top(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch, chain = _pushed_chain(ctx, tmp_path, monkeypatch, "split")
    c0, c1, c2, c3 = chain
    before = _first_push(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch)

    # Stop at commit 1 with `ger edit` (by change number) and split it in two: the first part
    # reuses its message, so its Change-Id; the second is a new commit the hook gives a new one.
    code, out, err = run_ger(repo, ger_edit_main, [f"change:{before[c1.change_id].number}"], monkeypatch)
    assert code == 0, f"{out}\n{err}"
    assert git_out("rev-parse", "HEAD", cwd=repo).strip() == c1.sha
    git("reset", "-q", "HEAD^", cwd=repo)
    git("add", "rework_1a.txt", cwd=repo)
    git("commit", "-q", "-C", c1.sha, cwd=repo)
    git("add", "rework_1b.txt", cwd=repo)
    git("commit", "-q", "-m", "split 1b", cwd=repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    git("rebase", "--continue", cwd=repo)

    split = chain_commits_oldest_first(repo)
    assert len(split) == 5
    assert [c.change_id for c in split[:2]] == [c0.change_id, c1.change_id]
    assert [c.change_id for c in split[3:]] == [c2.change_id, c3.change_id]
    new_change_id = split[2].change_id
    assert new_change_id not in {c.change_id for c in chain}
    assert split[2].subject == "split 1b"

    ger_push(repo, monkeypatch)
    after = _assert_gerrit_agrees(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch, before=before)
    assert [after[c.change_id].patchset for c in split] == [1, 2, 1, 2, 2]


def test_amending_the_lowest_commit_repushes_every_change_on_its_new_patchset(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch, chain = _pushed_chain(ctx, tmp_path, monkeypatch, "amend")
    c0 = chain[0]
    before = _first_push(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch)

    # `ger edit` by Change-Id stops at the lowest commit; amend it and let the rest replay.
    code, out, err = run_ger(repo, ger_edit_main, [c0.change_id], monkeypatch)
    assert code == 0, f"{out}\n{err}"
    (repo / "rework_0.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    git("rebase", "--continue", cwd=repo)
    amended = chain_commits_oldest_first(repo)
    assert [c.change_id for c in amended] == [c.change_id for c in chain]

    ger_push(repo, monkeypatch)
    after = _assert_gerrit_agrees(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch, before=before)
    # Only commit 0's content changed, but every change above has a new parent: all renewed.
    assert [after[c.change_id].patchset for c in amended] == [2, 2, 2, 2]

    # Amending again and pushing renews the lowest change once more, and the chain follows
    # (patchset 3 everywhere), so no member is left on the parent's patchset 2.
    code, out, err = run_ger(repo, ger_edit_main, [c0.change_id], monkeypatch)
    assert code == 0, f"{out}\n{err}"
    (repo / "rework_0.txt").write_text("amended twice\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    git("rebase", "--continue", cwd=repo)
    ger_push(repo, monkeypatch)
    again = _assert_gerrit_agrees(gerrit_admin_session, repo, monkeypatch, project=proj, branch=branch, before=after)
    assert {s.patchset for s in again.values()} == {3}
