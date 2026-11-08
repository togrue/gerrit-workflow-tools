# Spec: docu/spec/commands/comment.md

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_comment import main as comment_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.comment_anchor import CommentRange, LocationError, parse_location
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


WEB = "https://g.example"


def _store(repo: Path) -> tuple[ChangeStore, str]:
    """The stack on Gerrit, each commit its change's current patchset 1; returns HEAD's key."""
    git("config", "gerrit.webUrl", WEB, cwd=repo)
    details = build_details_by_change_id(stack_rows_mb_to_head(repo))
    return ChangeStore(details, web_base=WEB), list(details)[-1]


def _drafts(store: ChangeStore, key: str) -> list[dict[str, Any]]:
    return [row for rows in store.list_drafts(key).values() for row in rows]


def _comment(repo: Path, store: ChangeStore, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    return run_cli(repo, comment_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)


# --- LOCATION grammar ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "line", "rng"),
    [
        ("src/app.py", None, None),
        ("src/app.py:42", 42, None),
        ("src/app.py:40.5-42.17", 42, CommentRange(40, 4, 42, 17)),
        ("./src/app.py:7", 7, None),
    ],
)
def test_locations_parse_to_gerrit_fields(raw: str, line: int | None, rng: CommentRange | None) -> None:
    location = parse_location(raw)
    assert (location.path, location.line, location.range) == ("src/app.py", line, rng)


def test_a_whole_line_range_waits_for_the_file_to_close_it() -> None:
    location = parse_location("a.py:3-5")
    assert (location.start_line, location.line, location.end_column_open) == (3, 5, True)


@pytest.mark.parametrize("raw", ["a.py:5-3", "a.py:0", "a.py:2.4", "a.py:3.9-3.2", "../a.py:1", "/etc/passwd"])
def test_malformed_or_backwards_locations_are_rejected(raw: str) -> None:
    with pytest.raises(LocationError):
        parse_location(raw)


# --- Gerrit drafts --------------------------------------------------------------------


def test_a_line_comment_on_head_is_an_unresolved_draft_on_its_patchset(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store(stack_repo)
    code, out, _ = _comment(stack_repo, store, ["d.txt:1", "-m", "why?", "--json"], monkeypatch)
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["state"], payload["change"], payload["patch_set"], payload["line"]) == ("draft", key, 1, 1)
    assert payload["url"] == f"{WEB}/c/testproj/+/103/comment/{payload['id']}/"
    [draft] = _drafts(store, key)
    assert (draft["path"], draft["line"], draft["message"], draft["unresolved"]) == ("d.txt", 1, "why?", True)
    assert "in_reply_to" not in draft


def test_a_range_is_sent_to_the_character_and_a_whole_line_range_ends_at_the_line_end(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store(stack_repo)
    assert _comment(stack_repo, store, ["d.txt:1.2-1.4", "-m", "x"], monkeypatch)[0] == ExitCode.OK
    assert _comment(stack_repo, store, ["d.txt:1-1", "-m", "y", "--resolved"], monkeypatch)[0] == ExitCode.OK
    ranges = {d["message"]: (d["range"], d["unresolved"]) for d in _drafts(store, key)}
    assert ranges["x"] == ({"start_line": 1, "start_character": 1, "end_line": 1, "end_character": 4}, True)
    # "file d.txt" is 10 characters long.
    assert ranges["y"] == ({"start_line": 1, "start_character": 0, "end_line": 1, "end_character": 10}, False)


def test_a_file_comment_has_no_line(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store(stack_repo)
    code, out, _ = _comment(stack_repo, store, ["d.txt", "-m", "rename this file", "--json"], monkeypatch)
    assert code == ExitCode.OK
    assert json_stdout(out)["line"] is None
    [draft] = _drafts(store, key)
    assert "line" not in draft and "range" not in draft


def test_the_location_is_checked_against_the_commit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store(stack_repo)
    code, _, err = _comment(stack_repo, store, ["d.txt:2", "-m", "x"], monkeypatch)
    assert code == ExitCode.USAGE and "has 1 line(s)" in err
    # a.txt came in an older commit, but it is in HEAD's tree, so its lines are real there.
    assert _comment(stack_repo, store, ["a.txt:1", "-m", "x"], monkeypatch)[0] == ExitCode.OK
    code, _, err = _comment(stack_repo, store, ["nope.txt:1", "-m", "x"], monkeypatch)
    assert code == ExitCode.NOT_FOUND and "nope.txt is not in commit" in err
    assert len(_drafts(store, key)) == 1  # only the a.txt comment


def test_a_path_relative_to_a_subdirectory_is_rewritten(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "sub").mkdir()
    (stack_repo / "sub" / "x.py").write_text("one\ntwo\n", encoding="utf-8")
    git("add", "sub/x.py", cwd=stack_repo)
    git("commit", "-q", "-m", "Add x\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    store, key = _store(stack_repo)
    code, out, _ = _comment(stack_repo / "sub", store, ["x.py:2", "-m", "x", "--json"], monkeypatch)
    assert code == ExitCode.OK
    assert json_stdout(out)["path"] == "sub/x.py"
    assert _drafts(store, key)[0]["path"] == "sub/x.py"


def test_another_change_is_named_with_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = _store(stack_repo)
    first = next(iter(build_details_by_change_id(stack_rows_mb_to_head(stack_repo))))
    code, out, _ = _comment(stack_repo, store, ["a.txt:1", "-m", "x", "--change", first, "--json"], monkeypatch)
    assert code == ExitCode.OK
    assert json_stdout(out)["change"] == first
    assert _drafts(store, first)[0]["path"] == "a.txt"


def test_an_empty_or_missing_message_is_a_usage_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = _store(stack_repo)
    assert _comment(stack_repo, store, ["d.txt:1", "-m", "  "], monkeypatch)[0] == ExitCode.USAGE
    assert _comment(stack_repo, store, ["d.txt:1"], monkeypatch)[0] == ExitCode.USAGE
    assert _comment(stack_repo, store, ["-m", "x"], monkeypatch)[0] == ExitCode.USAGE


def test_a_commit_without_change_id_exits_9(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = _store(stack_repo)
    git("commit", "-q", "--allow-empty", "-m", "no footer", cwd=stack_repo)
    code, _, err = _comment(stack_repo, store, ["d.txt:1", "-m", "x"], monkeypatch)
    assert code == ExitCode.MISSING_CHANGE_ID and "no Change-Id" in err


def test_the_new_draft_shows_as_its_own_chain(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = _store(stack_repo)
    assert _comment(stack_repo, store, ["d.txt:1", "-m", "why?"], monkeypatch)[0] == ExitCode.OK
    _code, out, _ = run_cli(stack_repo, show_main, ["--json"], monkeypatch, gerrit=store)
    [chain] = json_stdout(out)["comment_chains"]
    assert (chain["path"], chain["line"], chain["resolved_after_publish"]) == ("d.txt", 1, False)
    assert chain["comments"][0]["draft"] is True


def test_discard_deletes_your_draft_and_prints_it(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store(stack_repo)
    _code, out, _ = _comment(stack_repo, store, ["d.txt:1", "-m", "why?", "--json"], monkeypatch)
    draft_id = json_stdout(out)["id"]
    code, out, _ = _comment(stack_repo, store, ["--discard", draft_id], monkeypatch)
    assert code == ExitCode.OK and 'discarded draft on d.txt:1: "why?"' in out
    assert _drafts(store, key) == []
    assert _comment(stack_repo, store, ["--discard", draft_id], monkeypatch)[0] == ExitCode.NOT_FOUND
