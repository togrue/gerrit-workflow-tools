"""Integration: rewriting a pushed stack, then pushing it again.

``ger fix``, ``ger reword`` and ``ger edit`` keep each commit's Change-Id, so the next
``ger push`` updates the same changes. Gerrit makes a new patchset only for a commit whose SHA
changed: the touched commit and those above it. Pushing up to a revision and re-pushing an
unchanged stack are covered too. Patchset numbers are Gerrit's, read back over REST.

Reworking a chain (reorder, squash, split, amend below) pushes a chain of four commits, reworks
it the way a user would (``ger rebase`` with an edited todo, ``ger edit`` and an amend), pushes
again and checks with ``assert_gerrit_agrees``: a new patchset exactly where a commit changed,
Gerrit's relation chain in the local order with every member at its current patchset (the web
UI marks one that is not *NOT CURRENT*), and ``ger log --json`` linking each commit to its
change. A partial push after amending the lowest commit is the negative control: there Gerrit
does list a member *NOT CURRENT*, until the rest is pushed.

The todo is edited by a small script given to ``ger rebase`` as ``GREBASE_EDITOR`` (the editor
the enricher opens after annotating the todo, ahead of ``GIT_EDITOR``), so the rework goes
through ger's own rebase.
"""

from __future__ import annotations

import re
import secrets
import shlex
import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as ger_edit_main, main_reword as ger_reword_main
from gerrit_workflow_tools.cli_fix import main as ger_fix_main
from gerrit_workflow_tools.cli_rebase import main as ger_rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    ChainCommit,
    OnGerrit,
    assert_gerrit_agrees,
    chain_commits_oldest_first,
    change_detail,
    current_patchset,
    ger_push,
    log_commits_by_change_id,
    not_current_members,
    open_changes_on_branch,
    prepare_topic_repo,
    related_changes,
    run_ger,
)
from tests.integration.repo_builder import build_linear_chain


def _stack(ctx: GerritIntegrationContext, tmp_path: Path, name: str, size: int) -> tuple[Path, str]:
    branch = f"{name}_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, branch)
    build_linear_chain(repo, [f"{name} {i}" for i in range(size)])
    return repo, branch


def _patchsets(session: GerritHttpSession, project: str, branch: str, chain: list[ChainCommit]) -> list[int]:
    return [current_patchset(session, f"{project}~{branch}~{c.change_id}") for c in chain]


def test_push_up_to_a_revision_then_the_rest_then_nothing(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    repo, branch = _stack(ctx, tmp_path, "partial", 3)
    ger_push(repo, monkeypatch, "HEAD~1")
    assert len(open_changes_on_branch(gerrit_admin_session, ctx.project_verified, branch)) == 2

    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    assert _patchsets(gerrit_admin_session, ctx.project_verified, branch, chain) == [1, 1, 1]

    out, _err = ger_push(repo, monkeypatch)
    assert "nothing to push: Gerrit already has these commits" in out
    assert _patchsets(gerrit_admin_session, ctx.project_verified, branch, chain) == [1, 1, 1]


def test_fix_reword_and_edit_then_push_renew_only_the_touched_change_and_those_above(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch = _stack(ctx, tmp_path, "rewrite", 4)
    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    change_ids = [c.change_id for c in chain]
    upstream = git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=repo)

    def _push_and_read() -> list[int]:
        ger_push(repo, monkeypatch)
        assert [c.change_id for c in chain_commits_oldest_first(repo)] == change_ids
        return _patchsets(gerrit_admin_session, proj, branch, chain)

    # ger fix by Change-Id, squashed: commit 1 and the two above it move.
    (repo / "chain_1.txt").write_text("fixed\n", encoding="utf-8")
    git("add", "chain_1.txt", cwd=repo)
    code, _out, err = run_ger(repo, ger_fix_main, [chain[1].change_id], monkeypatch)
    assert code == 0, err
    git("-c", "sequence.editor=:", "rebase", "-q", "-i", "--autosquash", upstream, cwd=repo)
    assert _push_and_read() == [1, 2, 2, 2]

    # ger reword by change number: commit 2 and the one above it.
    number = change_detail(gerrit_admin_session, f"{proj}~{branch}~{chain[2].change_id}")["_number"]
    monkeypatch.setenv("GIT_EDITOR", "sed -i -e '1s/$/ (reworded)/'")
    code, _out, err = run_ger(repo, ger_reword_main, [f"change:{number}"], monkeypatch)
    assert code == 0, err
    assert git_out("log", "-1", "--format=%s", "HEAD~1", cwd=repo) == "rewrite 2 (reworded)"
    assert _push_and_read() == [1, 2, 3, 3]

    # ger edit by triplet stops at the tip; amend and continue: only the tip moves.
    code, _out, err = run_ger(repo, ger_edit_main, [f"{proj}~{branch}~{chain[3].change_id}"], monkeypatch)
    assert code == 0, err
    (repo / "chain_3.txt").write_text("edited\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    git("rebase", "--continue", cwd=repo)
    assert _push_and_read() == [1, 2, 3, 4]


# --- Reworking a pushed chain: reorder, squash, split, amend below ---------------------------


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
    ctx: GerritIntegrationContext,
    session: GerritHttpSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> tuple[Path, str, list[ChainCommit], dict[str, OnGerrit]]:
    """Push four commits, each on its own files (commit 1 writes two, so it can be split).

    Returns the repo, its branch, the chain and what Gerrit has for it (patchset 1 each).
    """
    branch = f"{name}_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, branch)
    _commit(repo, f"{name} 0", {"rework_0.txt": "zero"})
    _commit(repo, f"{name} 1", {"rework_1a.txt": "one a", "rework_1b.txt": "one b"})
    _commit(repo, f"{name} 2", {"rework_2.txt": "two"})
    _commit(repo, f"{name} 3", {"rework_3.txt": "three"})
    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    assert len(chain) == 4
    state = assert_gerrit_agrees(session, repo, monkeypatch, project=ctx.project_verified, branch=branch, before={})
    assert {s.patchset for s in state.values()} == {1}
    return repo, branch, chain, state


def _ger_rebase_with_todo(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, todo: list[str], *, shown: list[ChainCommit]
) -> None:
    """``ger rebase`` whose editor replaces the annotated todo with *todo* lines (full SHAs).

    Checks first that the todo ger opened listed *shown* in order, every line a ``pick``
    annotated by ger: ``# <subject>`` then the patchset column, ``p`` for a pushed commit
    that is its change's current patchset.
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
    # A `squash` asks git for the combined message: keep it as git folds it, even on a terminal.
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, out, err = run_ger(repo, ger_rebase_main, [], monkeypatch)
    monkeypatch.delenv("GREBASE_EDITOR")
    assert code == 0, f"{out}\n{err}"
    seen_text = seen.read_text(encoding="utf-8")
    assert "Gerrit enrichment failed" not in seen_text
    lines = [line for line in seen_text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert len(lines) == len(shown), seen_text
    for line, commit in zip(lines, shown, strict=True):
        verb, sha, annotation = line.split(None, 2)
        assert verb == "pick", line
        assert commit.sha.startswith(sha), line
        assert re.match(rf"# {re.escape(commit.subject)}\s+p\s", annotation), line
    assert not (repo / ".git" / "rebase-merge").exists(), "the rebase stopped"


def _amend_lowest(repo: Path, monkeypatch: pytest.MonkeyPatch, change_id: str, text: str) -> None:
    """``ger edit`` by Change-Id stops at the lowest commit; amend it and let the rest replay."""
    code, out, err = run_ger(repo, ger_edit_main, [change_id], monkeypatch)
    assert code == 0, f"{out}\n{err}"
    (repo / "rework_0.txt").write_text(f"{text}\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    git("rebase", "--continue", cwd=repo)


def test_reordered_chain_repushes_with_gerrit_relation_chain_in_the_new_order(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    session = gerrit_admin_session
    repo, branch, chain, before = _pushed_chain(ctx, session, tmp_path, monkeypatch, "reorder")
    c0, c1, c2, c3 = chain

    # Swap commits 1 and 2: they touch different files, so the picks apply cleanly.
    _ger_rebase_with_todo(repo, tmp_path, monkeypatch, [f"pick {c.sha}" for c in (c0, c2, c1, c3)], shown=chain)
    reordered = chain_commits_oldest_first(repo)
    assert [c.change_id for c in reordered] == [c0.change_id, c2.change_id, c1.change_id, c3.change_id]
    assert reordered[0].sha == c0.sha

    ger_push(repo, monkeypatch)
    assert len(open_changes_on_branch(session, ctx.project_verified, branch)) == 4
    after = assert_gerrit_agrees(session, repo, monkeypatch, project=ctx.project_verified, branch=branch, before=before)
    # Commit 0 did not move; the three above it each have a new parent.
    assert [after[c.change_id].patchset for c in reordered] == [1, 2, 2, 2]


@pytest.mark.parametrize(
    ("verb", "survivor", "orphan", "orphan_listed_by", "orphan_not_current"),
    [
        # `fixup` keeps commit 1's message, so its Change-Id; change 2 is squashed away.
        ("fixup", 1, 2, (0, 1), (3, 1)),
        # `squash` joins both messages: commit 1's Change-Id ends up in the body, commit 2's
        # stays last, in the footer, where git, Gerrit and ger read it. So change 2 survives
        # and change 1 is squashed away. The spec is silent on this; it pins what git does.
        ("squash", 2, 1, (0,), (3, 2)),
    ],
)
def test_squashed_chain_repushes_and_the_squashed_away_change_stays_open_on_gerrit(  # pylint: disable=too-many-arguments,too-many-locals
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    survivor: int,
    orphan: int,
    orphan_listed_by: tuple[int, ...],
    orphan_not_current: tuple[int, ...],
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    session = gerrit_admin_session
    repo, branch, chain, before = _pushed_chain(ctx, session, tmp_path, monkeypatch, f"squash_{verb}")
    c0, c1, c2, c3 = chain
    gone = chain[orphan]

    # Fold commit 2 into commit 1.
    _ger_rebase_with_todo(
        repo,
        tmp_path,
        monkeypatch,
        [f"pick {c0.sha}", f"pick {c1.sha}", f"{verb} {c2.sha}", f"pick {c3.sha}"],
        shown=chain,
    )
    squashed = chain_commits_oldest_first(repo)
    assert [c.change_id for c in squashed] == [c0.change_id, chain[survivor].change_id, c3.change_id]
    assert squashed[1].subject == c1.subject
    assert git_out("show", "--format=", "--name-only", squashed[1].sha, cwd=repo).split() == sorted(
        ["rework_1a.txt", "rework_1b.txt", "rework_2.txt"]
    )
    message = git_out("log", "-1", "--format=%B", squashed[1].sha, cwd=repo)
    # `squash` leaves the squashed-away Change-Id in the body; `fixup` drops commit 2's message.
    assert (gone.change_id in message) == (verb == "squash")

    out, err = ger_push(repo, monkeypatch)
    # Gerrit keeps the change that was squashed away: open, at the patchset it had. ger push
    # says nothing about it today (neither its Change-Id nor its change number). The spec is
    # silent on this; the assertion pins current behaviour.
    gone_number = before[gone.change_id].number
    assert gone.change_id not in out + err
    assert not re.search(rf"/\+/{gone_number}(?!\d)", out + err), out + err

    after = assert_gerrit_agrees(
        session, repo, monkeypatch, project=proj, branch=branch, before=before, orphans=frozenset({gone.change_id})
    )
    assert [after[c.change_id].patchset for c in squashed] == [1, 2, 2]

    # Where the orphan still shows up: Gerrit lists a change's direct descendants first, then
    # the "indirect" ones it finds from the change's older patchsets and from patchsets of the
    # same base. The tip never lists it.
    listed_by = [
        i
        for i, c in enumerate(chain)
        if c.change_id in after
        and gone.change_id in [r["change_id"] for r in related_changes(session, after[c.change_id].number)]
    ]
    assert listed_by == list(orphan_listed_by)

    orphan_detail = change_detail(session, f"{proj}~{branch}~{gone.change_id}")
    assert orphan_detail["status"] == "NEW"
    assert orphan_detail["current_revision"] == gone.sha
    assert int(orphan_detail["revisions"][gone.sha]["_number"]) == 1
    assert gone.change_id not in log_commits_by_change_id(repo, monkeypatch)

    # The orphan's own relation chain is the old one, where Gerrit marks the renewed changes
    # NOT CURRENT: listed at patchset 1 while their current patchset is 2.
    orphan_related = related_changes(session, gone_number)
    assert [r["change_id"] for r in orphan_related] == [c.change_id for c in reversed(chain)]
    assert not_current_members(orphan_related) == [chain[i].change_id for i in orphan_not_current]
    by_change = {r["change_id"]: r for r in orphan_related}
    parent = by_change[chain[orphan - 1].change_id]
    if verb == "fixup":
        # Its parent, commit 1, has a new patchset: the orphan sits on the old one.
        assert (parent["_revision_number"], parent["_current_revision_number"]) == (1, 2)
    else:
        # Its parent, commit 0, did not change.
        assert (parent["_revision_number"], parent["_current_revision_number"]) == (1, 1)
    assert all(r["commit"]["commit"] == c.sha for r, c in zip(orphan_related, reversed(chain), strict=True))


def test_split_commit_repushes_as_its_change_and_a_new_change_on_top(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    session = gerrit_admin_session
    repo, branch, chain, before = _pushed_chain(ctx, session, tmp_path, monkeypatch, "split")
    c0, c1, c2, c3 = chain

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
    after = assert_gerrit_agrees(session, repo, monkeypatch, project=ctx.project_verified, branch=branch, before=before)
    assert [after[c.change_id].patchset for c in split] == [1, 2, 1, 2, 2]


def test_amending_the_lowest_commit_repushes_every_change_on_its_new_patchset(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    session = gerrit_admin_session
    repo, branch, chain, before = _pushed_chain(ctx, session, tmp_path, monkeypatch, "amend")
    c0 = chain[0]

    _amend_lowest(repo, monkeypatch, c0.change_id, "amended")
    amended = chain_commits_oldest_first(repo)
    assert [c.change_id for c in amended] == [c.change_id for c in chain]

    ger_push(repo, monkeypatch)
    after = assert_gerrit_agrees(session, repo, monkeypatch, project=proj, branch=branch, before=before)
    # Only commit 0's content changed, but every change above has a new parent: all renewed.
    assert [after[c.change_id].patchset for c in amended] == [2, 2, 2, 2]

    # Amending again and pushing renews the lowest change once more, and the chain follows
    # (patchset 3 everywhere), so no member is left on the parent's patchset 2.
    _amend_lowest(repo, monkeypatch, c0.change_id, "amended twice")
    ger_push(repo, monkeypatch)
    again = assert_gerrit_agrees(session, repo, monkeypatch, project=proj, branch=branch, before=after)
    assert {s.patchset for s in again.values()} == {3}


def test_pushing_only_the_amended_lowest_commit_leaves_the_chain_not_current_until_the_rest_is_pushed(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for ``assert_gerrit_agrees``: a partial push does leave a member NOT CURRENT."""
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    session = gerrit_admin_session
    repo, branch, chain, before = _pushed_chain(ctx, session, tmp_path, monkeypatch, "partial_amend")
    c0, c1, c2, c3 = chain

    _amend_lowest(repo, monkeypatch, c0.change_id, "amended")
    amended = chain_commits_oldest_first(repo)
    assert [c.change_id for c in amended] == [c.change_id for c in chain]

    # `ger push REV`: only through the amended lowest commit.
    ger_push(repo, monkeypatch, amended[0].sha)
    assert [current_patchset(session, f"{proj}~{branch}~{c.change_id}") for c in chain] == [2, 1, 1, 1]

    # Change 1 still sits on change 0's patchset 1: its relation chain lists change 0 there,
    # while change 0's current patchset is 2. That is what the web UI marks NOT CURRENT.
    newest_first = [c.change_id for c in reversed(chain)]
    c1_related = related_changes(session, before[c1.change_id].number)
    assert [r["change_id"] for r in c1_related] == newest_first
    assert not_current_members(c1_related) == [c0.change_id]
    c0_listed = c1_related[-1]
    assert (c0_listed["_revision_number"], c0_listed["_current_revision_number"]) == (1, 2)
    assert c0_listed["commit"]["commit"] == c0.sha
    # Change 0's new patchset still lists the old chain above it (Gerrit's indirect
    # descendants, found from its patchset 1), each at its own current patchset.
    c0_related = related_changes(session, before[c0.change_id].number)
    assert [r["change_id"] for r in c0_related] == newest_first
    assert not_current_members(c0_related) == []
    assert [r["commit"]["commit"] for r in c0_related] == [c3.sha, c2.sha, c1.sha, amended[0].sha]

    # ger log: the commits above are newer than what Gerrit has for them.
    rows = log_commits_by_change_id(repo, monkeypatch)
    assert [rows[c.change_id]["patchset_status"] for c in amended] == ["active", "newer", "newer", "newer"]

    # Pushing the rest puts every change on its parent's current patchset again.
    ger_push(repo, monkeypatch)
    partial = {**before, c0.change_id: OnGerrit(before[c0.change_id].number, 2, amended[0].sha)}
    after = assert_gerrit_agrees(session, repo, monkeypatch, project=proj, branch=branch, before=partial)
    assert [after[c.change_id].patchset for c in amended] == [2, 2, 2, 2]
    assert not_current_members(related_changes(session, after[c1.change_id].number)) == []
