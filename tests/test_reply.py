# Spec: docu/spec/commands/reply.md

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
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


# --- publishing -------------------------------------------------------------------


def _store_with_draft(repo: Path) -> tuple[ChangeStore, str]:
    store, key = _store_with_open_chain(repo)
    store.set_drafts(key, {"f.py": [_comment("d-1", "Done", unresolved=False, updated="", in_reply_to="c-root")]})
    return store, key


def test_publish_with_yes_publishes_every_draft_on_the_stack(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_draft(stack_repo)
    code, out, _err = run_cli(stack_repo, reply_main, ["--publish", "-y", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    rows = {row["change"]: row for row in json_stdout(out)["changes"]}
    assert (rows[key]["status"], rows[key]["drafts"]) == ("published", 1)
    assert {row["status"] for k, row in rows.items() if k != key} == {"no-drafts"}
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == [key]
    assert _drafts(store, key) == []


def test_publish_passes_the_change_message(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(stack_repo)
    run_cli(stack_repo, reply_main, ["--publish", "-y", "-m", "Addressed all comments."], monkeypatch, gerrit=store)
    (call,) = store.calls_to("publish_drafts")
    assert call.kwargs["message"] == "Addressed all comments."


def test_dry_run_lists_drafts_and_publishes_nothing(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(stack_repo)
    code, out, _err = run_cli(stack_repo, reply_main, ["--publish", "--dry-run"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert 'f.py:3 -> resolves: "Done"' in out
    assert store.calls_to("publish_drafts") == []


def test_publish_without_a_terminal_needs_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_draft(stack_repo)
    code, _out, err = run_cli(stack_repo, reply_main, ["--publish"], monkeypatch, gerrit=store)
    assert code == ExitCode.ATTENTION
    assert "pass -y" in err
    assert store.calls_to("publish_drafts") == []
    assert len(_drafts(store, key)) == 1


@pytest.mark.parametrize(("answer", "published"), [("y\n", True), ("\n", False), ("n\n", False)])
def test_publish_prompt_defaults_to_no(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str, published: bool
) -> None:
    store, _key = _store_with_draft(stack_repo)
    monkeypatch.setattr("gerrit_workflow_tools.cli_reply.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    code, _out, _err = run_cli(stack_repo, reply_main, ["--publish"], monkeypatch, gerrit=store)
    assert code == (ExitCode.OK if published else ExitCode.ATTENTION)
    assert bool(store.calls_to("publish_drafts")) is published


def test_publish_guard_blocks_before_contacting_gerrit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(stack_repo)
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    code, _out, err = run_cli(
        stack_repo, reply_main, ["--publish", "-y"], monkeypatch, gerrit=store, catch_sys_exit=True
    )
    assert code == ExitCode.USAGE
    assert "GER_NO_PUBLISH" in err
    assert store.calls == []
    # Looking is still allowed.
    code, _out, _err = run_cli(stack_repo, reply_main, ["--publish", "--dry-run"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK


def test_a_failed_change_does_not_stop_the_others(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, first = _store_with_draft(stack_repo)
    second = next(k for k in build_details_by_change_id(stack_rows_mb_to_head(stack_repo)) if k != first)
    store.set_comments(second, {"g.py": [_comment("c-2", "why", unresolved=True, updated="2024-01-01 10:00:00")]})
    store.set_drafts(second, {"g.py": [_comment("d-2", "because", unresolved=True, updated="", in_reply_to="c-2")]})
    real = store.publish_drafts

    def flaky(change_id: str, *, message: str | None = None, labels: Any = None) -> dict[str, Any]:
        if change_id == first:
            raise GerritApiError("Gerrit HTTP 409", status=409)
        return real(change_id, message=message, labels=labels)

    monkeypatch.setattr(store, "publish_drafts", flaky)
    code, out, err = run_cli(stack_repo, reply_main, ["--publish", "-y", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.GERRIT
    rows = {row["change"]: row for row in json_stdout(out)["changes"]}
    assert rows[first]["status"] == "failed" and "409" in rows[first]["error"]
    assert rows[second]["status"] == "published"
    assert first in err


def test_ger_show_sees_the_published_chain_at_once(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing drops the cached rows, so show does not serve the pre-publish comments."""
    store, key = _store_with_open_chain(stack_repo)
    number = store.get_change(key)["_number"]
    run_cli(stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    run_cli(stack_repo, show_main, ["--json", f"change:{number}"], monkeypatch, gerrit=store)
    run_cli(stack_repo, reply_main, ["--publish", "-y"], monkeypatch, gerrit=store)
    _code, out, _err = run_cli(
        stack_repo, show_main, ["--json", "--comments", "all", f"change:{number}"], monkeypatch, gerrit=store
    )
    (chain,) = json_stdout(out)["comment_chains"]
    assert chain["resolved"] is True
    assert [c["draft"] for c in chain["comments"]] == [False, False]
