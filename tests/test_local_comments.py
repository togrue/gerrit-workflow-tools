# Spec: docu/spec/commands/comment.md (notes, local drafts, the local comment store)

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_comment import main as comment_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.comment_anchor import Anchor, CommentLocation
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.local_comments import REF, LocalCommentStore, new_comment
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


WEB = "https://g.example"


def _comment(cwd: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch, store: ChangeStore | None = None) -> Any:
    return run_cli(cwd, comment_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)


def _on_gerrit(repo: Path) -> ChangeStore:
    git("config", "gerrit.webUrl", WEB, cwd=repo)
    return ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(repo)), web_base=WEB)


def _nothing_on_gerrit(repo: Path) -> ChangeStore:
    git("config", "gerrit.webUrl", WEB, cwd=repo)
    return ChangeStore({}, web_base=WEB)


def _show_json(repo: Path, store: ChangeStore, monkeypatch: pytest.MonkeyPatch, *argv: str) -> dict[str, Any]:
    _code, out, _ = run_cli(repo, show_main, ["--json", *argv], monkeypatch, gerrit=store)
    return json_stdout(out)


def test_a_note_needs_no_gerrit_and_never_touches_the_index(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "d.txt").write_text("my edit\n", encoding="utf-8")
    git("add", "d.txt", cwd=stack_repo)
    staged_before = git_out("diff", "--cached", "--name-only", cwd=stack_repo)
    code, out, _ = _comment(stack_repo, ["d.txt:1", "-m", "think about this", "--note", "--json"], monkeypatch)
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["state"], payload["url"], payload["patch_set"]) == ("note", None, None)
    assert payload["change"].startswith("testproj~main~I")
    assert git_out("diff", "--cached", "--name-only", cwd=stack_repo) == staged_before
    [stored] = LocalCommentStore(stack_repo).all()
    assert (stored.id, stored.state, stored.message) == (payload["id"], "note", "think about this")
    # The anchor is the committed file, not the edited one, and the store keeps its blob.
    head_blob = git_out("rev-parse", "HEAD:d.txt", cwd=stack_repo)
    assert (stored.anchor.blob, stored.anchor.text) == (head_blob, "file d.txt")
    assert git_out("ls-tree", "--name-only", f"{REF}:anchors", cwd=stack_repo) == head_blob


def test_an_unpushed_commit_gets_a_local_draft(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _nothing_on_gerrit(stack_repo)
    code, out, _ = _comment(stack_repo, ["d.txt:1", "-m", "fix later"], monkeypatch, store)
    assert code == ExitCode.OK
    assert out.startswith("local draft on d.txt:1 (commit ") and "not on Gerrit) id " in out
    assert store.calls_to("put_draft") == []


def test_a_commit_rewritten_locally_gets_a_local_draft(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _on_gerrit(stack_repo)
    git("commit", "-q", "--amend", "--no-edit", "--date=2001-01-01T00:00:00", cwd=stack_repo)
    code, out, _ = _comment(stack_repo, ["d.txt:1", "-m", "x", "--json"], monkeypatch, store)
    assert (code, json_stdout(out)["state"]) == (ExitCode.OK, "local-draft")
    assert store.calls_to("put_draft") == []


def test_local_forces_a_local_draft_on_a_pushed_patchset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _on_gerrit(stack_repo)
    code, out, _ = _comment(stack_repo, ["d.txt:1", "-m", "x", "--local", "--json"], monkeypatch, store)
    assert (code, json_stdout(out)["state"]) == (ExitCode.OK, "local-draft")
    assert store.calls_to("put_draft") == []


def test_show_lists_local_comments_on_a_commit_gerrit_does_not_have(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _nothing_on_gerrit(stack_repo)
    _comment(stack_repo, ["d.txt:1", "-m", "fix later"], monkeypatch, store)
    _comment(stack_repo, ["d.txt", "-m", "private thought", "--note"], monkeypatch, store)
    payload = _show_json(stack_repo, store, monkeypatch)
    states = [
        (chain["path"], chain["line"], chain["url"], chain["comments"][0]["state"])
        for chain in payload["comment_chains"]
    ]
    assert sorted(states, key=str) == sorted(
        [("d.txt", 1, None, "local-draft"), ("d.txt", None, None, "note")], key=str
    )
    _code, out, _ = run_cli(stack_repo, show_main, ["--color=never"], monkeypatch, gerrit=store)
    assert "local draft (not on Gerrit)" in out and "note (private)" in out


def test_local_comments_sit_beside_gerrit_chains(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _on_gerrit(stack_repo)
    _comment(stack_repo, ["d.txt:1", "-m", "on gerrit"], monkeypatch, store)
    _comment(stack_repo, ["d.txt:1", "-m", "only mine", "--note"], monkeypatch, store)
    chains = _show_json(stack_repo, store, monkeypatch)["comment_chains"]
    assert [c["comments"][0]["state"] for c in chains] == ["draft", "note"]


def test_a_changed_file_marks_the_anchor_moved(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _nothing_on_gerrit(stack_repo)
    _comment(stack_repo, ["d.txt:1", "-m", "x"], monkeypatch, store)
    assert _show_json(stack_repo, store, monkeypatch)["comment_chains"][0]["anchor_moved"] is False
    (stack_repo / "d.txt").write_text("file d.txt, reworked\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)
    [chain] = _show_json(stack_repo, store, monkeypatch)["comment_chains"]
    assert chain["anchor_moved"] is True
    _code, out, _ = run_cli(stack_repo, show_main, ["--color=never"], monkeypatch, gerrit=store)
    assert "d.txt:1 (file changed since)" in out


def test_resolved_local_comments_follow_the_comments_filter(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _nothing_on_gerrit(stack_repo)
    _comment(stack_repo, ["d.txt:1", "-m", "fyi", "--resolved"], monkeypatch, store)
    assert _show_json(stack_repo, store, monkeypatch)["comment_chains"] == []
    [chain] = _show_json(stack_repo, store, monkeypatch, "--comments", "resolved")["comment_chains"]
    assert chain["resolved"] is True


def test_discard_removes_a_local_comment_and_prints_it(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _nothing_on_gerrit(stack_repo)
    _code, out, _ = _comment(stack_repo, ["d.txt:1", "-m", "never mind", "--note", "--json"], monkeypatch, store)
    comment_id = json_stdout(out)["id"]
    code, out, _ = _comment(stack_repo, ["--discard", comment_id], monkeypatch, store)
    assert code == ExitCode.OK and 'discarded note on d.txt:1: "never mind"' in out
    assert LocalCommentStore(stack_repo).all() == []
    # The removal is a commit too: history keeps what was deleted.
    assert git_out("rev-list", "--count", REF, cwd=stack_repo) == "2"


def test_every_worktree_shares_one_store(stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other"
    git("worktree", "add", "-q", "-b", "other", str(other), "HEAD", cwd=stack_repo)
    git("branch", "--set-upstream-to", "origin/main", "other", cwd=stack_repo)
    _code, out, _ = _comment(other, ["d.txt:1", "-m", "from the other worktree", "--note", "--json"], monkeypatch)
    assert [c.id for c in LocalCommentStore(stack_repo).all()] == [json_stdout(out)["id"]]


def _note(repo: Path, message: str) -> Any:
    head = git_out("rev-parse", "HEAD", cwd=repo)
    anchor = Anchor(commit=head, location=CommentLocation(path="d.txt", line=1), blob=None, text="")
    key = "testproj~main~I" + "1" * 40
    return new_comment(state="note", change=key, anchor=anchor, message=message, unresolved=True)


def test_a_writer_that_loses_the_race_reapplies_its_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalCommentStore(stack_repo)
    store.add(_note(stack_repo, "first"))
    real_tree = LocalCommentStore._tree
    raced: list[bool] = []

    def tree_then_lose(self: LocalCommentStore, *args: Any) -> str:
        built = real_tree(self, *args)
        if not raced:
            raced.append(True)
            LocalCommentStore(stack_repo).add(_note(stack_repo, "the other writer"))
        return built

    monkeypatch.setattr(LocalCommentStore, "_tree", tree_then_lose)
    store.add(_note(stack_repo, "second"))
    assert [c.message for c in store.all()] == ["first", "the other writer", "second"]
