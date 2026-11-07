# Spec: docu/spec/commands/reply.md

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


WEB = "https://g.example"


def _comment(cid: str, message: str, *, unresolved: bool, updated: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "line": 3,
        "message": message,
        "unresolved": unresolved,
        "updated": updated,
        "patch_set": 1,
        **extra,
    }


def _store_with_open_chain(repo: Path, **root_extra: Any) -> tuple[ChangeStore, str]:
    """Stack on Gerrit; the oldest change has one open chain rooted at ``c-root`` on f.py:3."""
    git("config", "gerrit.webUrl", WEB, cwd=repo)
    details = build_details_by_change_id(stack_rows_mb_to_head(repo))
    store = ChangeStore(details, web_base=WEB)
    key = next(iter(details))
    root = _comment("c-root", "rename n", unresolved=True, updated="2024-01-01 10:00:00", **root_extra)
    store.set_comments(key, {"f.py": [root]})
    return store, key


def _drafts(store: ChangeStore, key: str) -> list[dict[str, Any]]:
    return [row for rows in store.list_drafts(key).values() for row in rows]


def test_done_writes_a_resolving_draft_on_the_answered_patchset(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(stack_repo)
    code, out, _err = run_cli(stack_repo, reply_main, ["c-root", "--done", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["action"], payload["change"], payload["root_id"]) == ("created", key, "c-root")
    assert (payload["patch_set"], payload["unresolved"]) == (1, False)
    assert payload["url"].endswith("/comment/c-root/")
    (draft,) = _drafts(store, key)
    assert draft["in_reply_to"] == "c-root"
    assert (draft["path"], draft["line"], draft["message"], draft["patch_set"]) == ("f.py", 3, "Done", 1)


def test_human_output_says_where_the_draft_went(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_open_chain(stack_repo)
    code, out, _err = run_cli(stack_repo, reply_main, ["c-root", "--done", "--color=never"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert "draft reply on f.py:3 (patchset 1) -> resolves" in out
    assert "/comment/c-root/" in out


def test_a_reply_without_a_state_flag_keeps_the_chain_open(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(stack_repo)
    code, _out, _err = run_cli(stack_repo, reply_main, ["c-root", "-m", "Why?"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert draft["unresolved"] is True


def test_replying_again_updates_your_draft_and_shows_what_it_replaced(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(stack_repo)
    run_cli(stack_repo, reply_main, ["c-root", "-m", "first", "--resolve"], monkeypatch, gerrit=store)
    code, out, _err = run_cli(stack_repo, reply_main, ["c-root", "-m", "second", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["action"], payload["replaced_message"]) == ("updated", "first")
    # No flag given: the existing draft's state is kept, not reset to the chain's.
    assert payload["unresolved"] is False
    (draft,) = _drafts(store, key)
    assert draft["message"] == "second"


def test_any_comment_names_the_chain_and_the_reply_answers_the_newest(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(stack_repo)
    root = _comment("c-root", "rename n", unresolved=True, updated="2024-01-01 10:00:00")
    follow_up = _comment("c-more", "and m", unresolved=True, updated="2024-01-02 10:00:00", in_reply_to="c-root")
    follow_up["patch_set"] = 2
    store.set_comments(key, {"f.py": [root, follow_up]})
    code, _out, _err = run_cli(stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert (draft["in_reply_to"], draft["patch_set"]) == ("c-more", 2)


def test_reply_on_the_parent_side_stays_on_the_parent_side(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(stack_repo, side="PARENT")
    run_cli(stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    (draft,) = _drafts(store, key)
    assert draft["side"] == "PARENT"


def test_message_from_stdin(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(stack_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("Computing it is cheaper.\n\nSee the benchmark.\n"))
    code, _out, _err = run_cli(stack_repo, reply_main, ["c-root", "-F", "-"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert draft["message"] == "Computing it is cheaper.\n\nSee the benchmark."


def test_comment_url_names_its_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(stack_repo)
    number = store.get_change(key)["_number"]
    url = f"{WEB}/c/testproj/+/{number}/comment/c-root/"
    code, _out, err = run_cli(stack_repo, reply_main, [url, "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert len(_drafts(store, key)) == 1


def test_unknown_comment_is_not_found(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_open_chain(stack_repo)
    code, _out, err = run_cli(stack_repo, reply_main, ["nope", "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.NOT_FOUND
    assert "no comment nope on the local stack" in err


def test_discard_removes_your_draft(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(stack_repo)
    run_cli(stack_repo, reply_main, ["c-root", "-m", "Will do"], monkeypatch, gerrit=store)
    code, out, _err = run_cli(stack_repo, reply_main, ["c-root", "--discard"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert 'discarded draft on f.py:3: "Will do"' in out
    assert _drafts(store, key) == []
    code, _out, _err = run_cli(stack_repo, reply_main, ["c-root", "--discard"], monkeypatch, gerrit=store)
    assert code == ExitCode.NOT_FOUND


@pytest.mark.parametrize(
    "argv",
    [
        ["c-root"],
        ["c-root", "-m", "x", "--done"],
        ["c-root", "--done", "--unresolve"],
        ["c-root", "-m", "   "],
        ["c-root", "--discard", "-m", "x"],
        [f"{WEB}/c/testproj/+/100/comment/c-root/", "--change", "change:100", "--done"],
    ],
)
def test_usage_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    store, key = _store_with_open_chain(stack_repo)
    code, _out, _err = run_cli(stack_repo, reply_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert _drafts(store, key) == []


def test_ger_show_reads_back_what_ger_reply_wrote(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two commands agree on the chain: the reply shows up where show puts drafts."""
    store, key = _store_with_open_chain(stack_repo)
    run_cli(stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    number = store.get_change(key)["_number"]
    code, out, _err = run_cli(stack_repo, show_main, ["--json", f"change:{number}"], monkeypatch, gerrit=store)
    assert code in (ExitCode.OK, ExitCode.ATTENTION)
    (chain,) = json_stdout(out)["comment_chains"]
    assert (chain["resolved"], chain["resolved_after_publish"]) == (False, True)
    assert [c["draft"] for c in chain["comments"]] == [False, True]
