"""Cross-command resolution consistency (unit tests).

Pins agreement *between* commands: the same changeish must resolve the same way in
``ger log``, ``ger show`` and ``ger resolve``. End-to-end coverage against a live Gerrit
instance lives in ``tests/integration/test_09_change_resolution.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_changeid import main as change_id_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.core.comment_chains import collect_comment_chains
from gerrit_workflow_tools.core.gerrit.stack_context import build_triplet
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.run_context import RunContext
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    at_older_patchsets,
    change_info_for_sha,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli
from tests.fixtures import _cid


_NO_COMMIT = "cannot resolve 'nosuch': no commit by that name"


@pytest.mark.parametrize(
    ("main_fn", "argv", "message"),
    [
        (log_main, ["nosuch..HEAD"], _NO_COMMIT),  # names the unknown side, not the range
        (change_id_main, ["nosuch"], _NO_COMMIT),
        (push_main, ["--dry-run", "nosuch"], _NO_COMMIT),
        (push_main, ["--dry-run", "--branch", "nosuch"], "branch 'nosuch' not found"),
        (gshow_main, ["nosuch"], _NO_COMMIT),
        (resolve_main, ["nosuch"], _NO_COMMIT),
    ],
)
def test_every_command_exits_the_same_way_on_an_unknown_revision(
    main_fn: Callable[..., int], argv: list[str], message: str, web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_cli(web_stack_repo, main_fn, argv, monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert err.strip() == f"error: {message}"


_NO_ID = "I" + "0" * 40


@pytest.mark.parametrize(
    ("command", "argv", "message"),
    [
        ("show", ["change:99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("resolve", ["change:99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("checkout", ["change:99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("checkout", ["99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("fix", ["change:99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("show", [_NO_ID], f"cannot resolve '{_NO_ID}': no Gerrit change has that Change-Id"),
        ("resolve", [_NO_ID], f"cannot resolve '{_NO_ID}': no Gerrit change has that Change-Id"),
        ("checkout", [_NO_ID], f"cannot resolve '{_NO_ID}': no Gerrit change has that Change-Id"),
        ("fix", [_NO_ID], f"cannot resolve '{_NO_ID}': no commit in the local stack has that Change-Id"),
        ("edit", [_NO_ID], f"cannot resolve '{_NO_ID}': no commit in the local stack has that Change-Id"),
        ("push", [_NO_ID], f"cannot resolve '{_NO_ID}': no commit in the local stack has that Change-Id"),
        ("push", ["change:99999"], "cannot resolve 'change:99999': Gerrit has no such change"),
        ("sha", [_NO_ID], f"cannot resolve '{_NO_ID}': no commit has that Change-Id"),
        ("show", ["99999"], "cannot resolve '99999': no commit by that name, and Gerrit has no such change"),
        ("fix", ["99999"], "cannot resolve '99999': no commit by that name, and Gerrit has no such change"),
        ("edit", ["99999"], "cannot resolve '99999': no commit by that name, and Gerrit has no such change"),
        ("push", ["99999"], "cannot resolve '99999': no commit by that name, and Gerrit has no such change"),
    ],
)
def test_not_found_reads_the_same_in_every_command(
    command: str, argv: list[str], message: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One family of not-found wordings: ``cannot resolve '<input>': <what is missing>``."""
    from gerrit_workflow_tools import cli_checkout, cli_edit, cli_fix, cli_sha

    mains = {
        "show": gshow_main,
        "resolve": resolve_main,
        "checkout": cli_checkout.main,
        "fix": cli_fix.main,
        "edit": cli_edit.main,
        "sha": cli_sha.main,
        "push": push_main,
    }
    argv = ["--dry-run", *argv] if command == "push" else argv
    _configure_web(stack_repo)
    gerrit = None if command == "sha" else ChangeStore({})
    code, _out, err = run_cli(stack_repo, mains[command], argv, monkeypatch, gerrit=gerrit)
    assert code == ExitCode.NOT_FOUND
    assert err.strip() == f"error: {message}"


def _configure_web(repo: Path) -> None:
    git("config", "ger.webUrl", "https://g.example", cwd=repo)


def _mock_dual_branch_details(
    *,
    change_id: str,
    sha: str,
    main_number: int = 120100,
    dev_number: int = 119900,
    main_cr: int = 2,
    dev_cr: int = -1,
) -> dict[str, dict]:
    main_row = change_info_for_sha(
        sha,
        change_id,
        number=main_number,
        branch="main",
        cr=main_cr,
    )
    dev_row = change_info_for_sha(
        sha,
        change_id,
        number=dev_number,
        branch="dev",
        cr=dev_cr,
    )
    return {str(main_row["id"]): main_row, str(dev_row["id"]): dev_row}


def _configure_dev_gerrit_target(repo: Path) -> None:
    """Point stack context at Gerrit destination branch ``dev``."""
    git("config", "branch.feature.gerTarget", "dev", cwd=repo)
    main_sha = git_out("rev-parse", "main", cwd=repo).strip()
    git("update-ref", "refs/remotes/origin/dev", main_sha, cwd=repo)


def test_resolve_and_show_json_agree_on_narrowed_change_id(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger resolve --json`` and ``ger show --json`` emit the same ``resolution`` block."""
    cid = _cid("a6")
    sha = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    details = _mock_dual_branch_details(change_id=cid, sha=sha)
    # One store for both commands: they must agree against identical Gerrit state.
    store = ChangeStore(details)
    code_r, out_r, err_r = run_cli(web_stack_repo, resolve_main, ["--json", cid], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(web_stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s == 0, err_s
    resolve_block = json_stdout(out_r)["resolution"]
    show_block = json_stdout(out_s)["resolution"]
    assert resolve_block == show_block
    assert resolve_block["selected_reason"] == "target-branch"
    assert resolve_block["selected"]["branch"] == "main"
    assert resolve_block["selected"]["number"] == 120100
    assert "119900" in err_r
    assert "119900" in err_s


@pytest.mark.parametrize("form", ["change-id", "change-number", "short", "hash", "bare", "triplet"])
def test_resolve_and_show_agree_on_the_local_commit_of_a_stack_change(
    form: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change with a commit on the local stack: both commands report that commit as ``local_sha``."""
    from tests.cli_gerrit_mocks import build_details_by_change_id

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    store = ChangeStore(details)
    row = rows[0]
    assert row.change_id is not None
    info = next(d for d in details.values() if d["change_id"] == row.change_id)
    changeish = {
        "change-id": row.change_id,
        "change-number": f"change:{info['_number']}",
        "short": f"c{info['_number']}",  # how ger prints a change: a commit first, else the change
        "hash": f"#{info['_number']}",
        "bare": str(info["_number"]),
        "triplet": build_triplet("testproj", "main", row.change_id),
    }[form]

    code_r, out_r, err_r = run_cli(stack_repo, resolve_main, ["--json", changeish], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(stack_repo, gshow_main, ["--json", changeish], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s == 0, err_s
    resolve_block = json_stdout(out_r)["resolution"]
    assert resolve_block == json_stdout(out_s)["resolution"]
    assert resolve_block["local_sha"] == row.sha


@pytest.mark.parametrize("form", ["change-id", "change-number", "short", "hash", "bare", "triplet"])
def test_push_and_edit_take_rev_as_the_same_stack_changeish(
    form: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger push REV`` resolves REV like every focus command: to the local stack commit of that change."""
    from gerrit_workflow_tools.core.config import Settings
    from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_changeish
    from tests.cli_gerrit_mocks import at_older_patchsets, build_details_by_change_id

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = at_older_patchsets(build_details_by_change_id(rows), stack_repo)
    store = ChangeStore(details)
    row = rows[0]
    assert row.change_id is not None
    info = next(d for d in details.values() if d["change_id"] == row.change_id)
    changeish = {
        "change-id": row.change_id,
        "change-number": f"change:{info['_number']}",
        "short": f"c{info['_number']}",  # how ger prints a change: a commit first, else the change
        "hash": f"#{info['_number']}",
        "bare": str(info["_number"]),
        "triplet": build_triplet("testproj", "main", row.change_id),
    }[form]

    settings = Settings.from_cwd(stack_repo)
    target = resolve_stack_changeish(
        stack_repo,
        changeish,
        service=RunContext.open(stack_repo, settings=settings, gerrit=store).gerrit,
        require_in_stack=True,
    )
    code, out, err = run_cli(stack_repo, push_main, ["--dry-run", changeish], monkeypatch, gerrit=store)

    assert code == 0, err
    assert target.sha == row.sha
    pushed = out.split("About to push commits:")[1].split("Branch")[0]
    assert row.short_sha in pushed and rows[1].short_sha not in pushed


def test_log_on_main_target_uses_main_triplet_not_dev(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger log --json`` on a main-target stack overlays the main-branch Gerrit change."""
    sha = git_out("rev-parse", "HEAD~2", cwd=web_stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha, main_cr=2, dev_cr=-1)
    code, out, err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is True
    assert row["code_review"] == 2
    assert "/+/120100" in (row.get("gerrit_url") or "")
    assert "resolution_note" in row
    assert "120100" in row["resolution_note"]
    assert "119900" in row["resolution_note"]


def test_log_resolution_notes_do_not_requery_per_change_id(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notes must come from cache/batch payloads — no bare change:I storm after overlay."""
    sha = git_out("rev-parse", "HEAD~2", cwd=web_stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha)
    store = ChangeStore(details)
    code, out, err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    bare = [query for query in store.queries() if query.startswith("change:")]
    assert bare == [], f"unexpected per-Change-Id queries: {bare!r}"
    row = next(c for c in json_stdout(out)["commits"] if c["change_id"] == cid)
    assert "resolution_note" in row


def test_log_on_dev_target_uses_dev_triplet_not_main(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger log --json`` with ``branch.*.gerTarget=dev`` overlays the dev-branch change."""
    _configure_dev_gerrit_target(web_stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=web_stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha, main_cr=2, dev_cr=-1)
    code, out, err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is True
    assert row["code_review"] == -1
    assert "/+/119900" in (row.get("gerrit_url") or "")


def test_log_absent_when_change_only_on_other_branch(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Target-branch overlay must not pick a Change-Id that exists only on another branch."""
    _configure_dev_gerrit_target(web_stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=web_stack_repo)
    cid = _cid("2")
    # Only main-branch row exists; stack target is dev → absent.
    main_only = {
        str(change_info_for_sha(sha, cid, number=120100, branch="main", cr=2)["id"]): change_info_for_sha(
            sha, cid, number=120100, branch="main", cr=2
        )
    }
    code, out, err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(main_only))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is False
    assert "/+/120100" not in (row.get("gerrit_url") or "")


def test_push_stack_resolve_and_show_agree_on_triplet(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After stack overlay, ``ger resolve`` and ``ger show`` pick the same triplet as ``ger log``."""
    from tests.cli_gerrit_mocks import build_details_by_change_id

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    cid = rows[0].change_id
    assert cid is not None
    expected_triplet = build_triplet("testproj", "main", cid)

    # One store for all three commands: they must pick the same triplet from one state.
    store = ChangeStore(details)
    code_r, out_r, err_r = run_cli(stack_repo, resolve_main, ["--json", cid], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=store)
    code_l, out_l, err_l = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s == 0, err_s
    assert code_l == 0, err_l

    resolve_triplet = json_stdout(out_r)["resolution"]["selected"]["triplet"]
    show_triplet = json_stdout(out_s)["resolution"]["selected"]["triplet"]
    assert resolve_triplet == show_triplet == expected_triplet

    log_row = next(c for c in json_stdout(out_l)["commits"] if c["change_id"] == cid)
    assert log_row["pushed"] is True
    assert f"/+/{json_stdout(out_r)['resolution']['selected']['number']}" in (log_row.get("gerrit_url") or "")


# --- Siblings: one Change-Id on main and on dev ------------------------------------

_DEV_SHA = "d" * 40
"""Dev's cherry-pick: a commit this repository does not have."""


def _siblings(repo: Path) -> tuple[ChangeStore, str, str]:
    """HEAD~1 is main's #120100 (Code-Review +2); dev's #119900 is its sibling, -1 with a comment.

    Returns the store, HEAD~1's Change-Id and its SHA. The stack pushes to ``main``.
    """
    _configure_web(repo)
    sha = git_out("rev-parse", "HEAD~1", cwd=repo)
    cid = next(row.change_id for row in stack_rows_mb_to_head(repo) if row.sha == sha)
    assert cid is not None
    main_row = change_info_for_sha(sha, cid, number=120100, branch="main", cr=2)
    dev_row = change_info_for_sha(_DEV_SHA, cid, number=119900, branch="dev", cr=-1, unresolved_comment_count=1)
    store = ChangeStore({str(main_row["id"]): main_row, str(dev_row["id"]): dev_row})
    comment = {"id": "c1", "line": 1, "message": "only on dev", "unresolved": True, "patch_set": 1}
    store.set_comments(str(dev_row["id"]), {"a.txt": [comment]})
    return store, cid, sha


@pytest.mark.parametrize(
    ("form", "number"),
    [("change-id", 120100), ("main-triplet", 120100), ("dev-triplet", 119900), ("dev-number", 119900)],
)
def test_show_resolve_and_serve_agree_on_which_sibling_is_shown(
    form: str, number: int, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The change a changeish names is the change shown; the local commit only when it pushes there."""
    from gerrit_workflow_tools.server.api import GerApi

    store, cid, sha = _siblings(stack_repo)
    changeish = {
        "change-id": cid,
        "main-triplet": build_triplet("testproj", "main", cid),
        "dev-triplet": build_triplet("testproj", "dev", cid),
        "dev-number": "change:119900",
    }[form]
    code_r, out_r, err_r = run_cli(stack_repo, resolve_main, ["--json", changeish], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(stack_repo, gshow_main, ["--json", changeish], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s in (0, 1), err_s
    shown = json_stdout(out_s)
    [served] = GerApi(stack_repo, gerrit=store).handle("GET", "/api/v1/show", {"rev": [changeish]})["commits"]
    resolution = json_stdout(out_r)["resolution"]
    assert resolution == shown["resolution"] == served["resolution"]
    assert resolution["selected"]["number"] == number

    for payload in (shown, served):
        assert payload["number"] == number
        assert f"/+/{number}" in payload["gerrit_url"]
        assert all(f"/+/{number}/" in chain["url"] for chain in payload["comment_chains"])
    if number == 120100:
        assert (shown["local_commit"], resolution["local_sha"], shown["code_review"]) == (True, sha, 2)
        assert "local_sibling" not in resolution
        assert shown["comment_chains"] == []
        return
    # Dev's change, as for any change that is not local, with a note about the local commit.
    assert (shown["local_commit"], shown["sha"], shown["code_review"]) == (False, _DEV_SHA, -1)
    assert "local_sha" not in resolution
    assert resolution["local_sibling"] == {"sha": sha, "branch": "main", "number": None}
    assert [chain["comments"][0]["message"] for chain in shown["comment_chains"]] == ["only on dev"]
    for err in (err_r, err_s):
        assert f"local stack commit {sha[:7]} carries the same Change-Id but targets 'main'" in err
    _code, text, _err = run_cli(stack_repo, gshow_main, [changeish], monkeypatch, gerrit=store)
    assert _DEV_SHA[:7] in text and _DEV_SHA[:8] not in text, "one abbreviation for every commit shown"


@pytest.mark.parametrize("form", ["dev-triplet", "dev-number"])
def test_fix_of_the_dev_sibling_fixes_the_stack_commit_and_says_it_targets_main(
    form: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger fix`` still targets the stack commit carrying the Change-Id (spec §4), and says so."""
    from gerrit_workflow_tools.cli_fix import main as fix_main

    store, cid, sha = _siblings(stack_repo)
    changeish = build_triplet("testproj", "dev", cid) if form == "dev-triplet" else "change:119900"
    (stack_repo / "a.txt").write_text("fixed\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, fix_main, ["--json", changeish], monkeypatch, gerrit=store)
    assert code == 0, err
    assert json_stdout(out)["fixup_sha"] == sha
    assert f"local stack commit {sha[:7]} carries the same Change-Id but targets 'main'" in err


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("comment", ["a.txt:1", "-m", "x", "--change", "99999"]),
        ("comment", ["a.txt:1", "-m", "x", "--change", "c99999"]),
        ("reply", ["abc_123", "-m", "x", "--change", "99999"]),
        ("review", ["--vote", "CR+1", "--change", "99999", "-y"]),
        ("checkout", ["c99999"]),
    ],
)
def test_a_bare_number_names_a_change_wherever_only_a_change_fits(
    command: str, argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--change N`` (and inbox's ``cN``) is change N in every command, as ``ger checkout N`` always was."""
    from gerrit_workflow_tools import cli_checkout, cli_comment, cli_reply, cli_review

    mains = {
        "comment": cli_comment.main,
        "reply": cli_reply.main,
        "review": cli_review.main,
        "checkout": cli_checkout.main,
    }
    _configure_web(stack_repo)
    git("config", "ger.user", "u", cwd=stack_repo)
    git("config", "ger.token", "t", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, mains[command], argv, monkeypatch, gerrit=ChangeStore({}))
    assert code == ExitCode.NOT_FOUND, err
    assert err.strip() == "error: cannot resolve 'change:99999': Gerrit has no such change"


# --- Every Change-Id of the stack on main and on dev ---------------------------------

_NOT_PER_CHANGE = frozenset({"query_changes", "query_accounts", "get_account", "get_plugin_project_reviewers"})
"""Store operations that do not address one change by ``/changes/{id}``."""


def _stack_with_dev_siblings(
    repo: Path, *, accounts: dict[int, dict[str, Any]] | None = None, older_patchsets: bool = False
) -> tuple[ChangeStore, dict[str, int]]:
    """Every stack commit is a change on ``main`` and has a sibling on ``dev`` (another number).

    Each main change carries one unresolved comment, so ``ger show`` fetches comments; with
    *older_patchsets* it is at an older patchset, so ``ger push`` has commits to send. Returns
    the store and, per Change-Id, the number of the change on ``main`` (the push branch).
    """
    _configure_web(repo)
    store = ChangeStore(accounts=accounts)
    on_main: dict[str, int] = {}
    for i, row in enumerate(stack_rows_mb_to_head(repo)):
        assert row.change_id is not None
        main_row = change_info_for_sha(row.sha, row.change_id, number=300 + i, unresolved_comment_count=1)
        dev_row = change_info_for_sha(_DEV_SHA, row.change_id, number=400 + i, branch="dev")
        if older_patchsets:  # Gerrit has an older patchset on main: a push sends the commit.
            at_older_patchsets({row.change_id: main_row}, repo)
        store.add_change(main_row)
        store.add_change(dev_row)
        comment = {"id": f"c{i}", "line": 1, "message": f"main {i}", "unresolved": True, "patch_set": 1}
        store.set_comments(str(main_row["id"]), {"a.txt": [comment]})
        on_main[row.change_id] = 300 + i
    return store, on_main


def _per_change_refs(store: ChangeStore) -> list[str]:
    """The ``{id}`` of every ``/changes/{id}/…`` request the store was asked."""
    return [str(call.args[0]) for call in store.calls if call.method not in _NOT_PER_CHANGE and call.args]


@pytest.mark.parametrize(
    "argv",
    [
        ["log", "--json"],
        ["log", "-v", "--json"],
        ["show", "--stack", "--json"],
        ["show", "--stack", "--comments", "all", "-v", "--color=never"],
        ["show", "HEAD", "--json"],
        ["show", "--json", "HEAD~1..HEAD"],
        ["resolve", "--json", "HEAD"],
        ["reply", "c0", "-m", "on it"],
        ["reply", "--publish", "--dry-run"],
    ],
    ids=lambda argv: " ".join(argv),
)
def test_every_command_addresses_a_change_on_two_branches_by_one_unique_id(
    argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``/changes/{id}`` request carries a bare Change-Id that Gerrit would answer 404.

    Every Change-Id of the stack is also on ``dev``, as after a cherry-pick: Gerrit then
    answers a bare Change-Id with "Multiple changes found" (the store 404s alike). Every
    command resolves a change once, by the stack's push branch, and every later request
    (comments, drafts, reviewers, checks, …) names that change, the one on ``main``.
    """
    from gerrit_workflow_tools.cli_reply import main as reply_main

    mains = {"log": log_main, "show": gshow_main, "resolve": resolve_main, "reply": reply_main}
    store, on_main = _stack_with_dev_siblings(stack_repo)
    code, _out, err = run_cli(stack_repo, mains[argv[0]], argv[1:], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert "Multiple changes found" not in err
    refs = _per_change_refs(store)
    assert all(ref not in on_main for ref in refs), refs
    named = {ref: store.get_change(ref)["_number"] for ref in refs}
    assert set(named.values()) <= set(on_main.values()), named


def test_show_stack_shows_the_stack_branch_change_of_a_change_id_on_two_branches(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger show --stack`` reports, for each commit, the change on the stack's own branch, comments and all."""
    store, on_main = _stack_with_dev_siblings(stack_repo)
    code, out, err = run_cli(
        stack_repo, gshow_main, ["--stack", "--json", "--comments", "all"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK, err
    shown = json_stdout(out)
    payloads = shown if isinstance(shown, list) else shown["commits"]
    assert {p["change_id"]: p["number"] for p in payloads} == on_main
    for p in payloads:
        assert [c["comments"][0]["message"] for c in p["comment_chains"]] == [f"main {on_main[p['change_id']] - 300}"]


def _service_entry_points(cid: str) -> dict[str, Callable[[Any], object]]:
    """Every per-change operation of :class:`GerritService`'s APIs, called with a bare *cid*."""
    root = {"id": "c0", "path": "a.txt", "line": 1, "message": "main 0", "unresolved": True, "patch_set": 1}
    chain = collect_comment_chains({"a.txt": [root]}, comments="all")[0]
    return {
        "comments.get_chains": lambda s: s.comments.get_chains(cid, comments="all"),
        "comments.get_drafts": lambda s: s.comments.get_drafts(cid),
        "comments.find_chain": lambda s: s.comments.find_chain({cid: None}, "c0"),
        "comments.save_reply": lambda s: s.comments.save_reply(cid, chain, "on it", unresolved=None),
        "comments.create_draft": lambda s: s.comments.create_draft(cid, 1, {"path": "a.txt", "message": "x"}),
        "comments.edit_draft": lambda s: s.comments.edit_draft(cid, "d0", "x"),
        "comments.discard_draft": lambda s: s.comments.discard_draft(cid, "d0"),
        "comments.publish_drafts": lambda s: s.comments.publish_drafts(cid, message="lgtm"),
        "changes.get": lambda s: s.changes.get(cid),
        "changes.set_reviewers": lambda s: s.changes.set_reviewers(cid, add=["bob"]),
    }


@pytest.mark.parametrize("entry", list(_service_entry_points(_cid("00"))))
def test_every_service_entry_point_narrows_a_bare_change_id_to_the_push_branch(
    entry: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handed a bare Change-Id, core asks Gerrit for the push branch's change, never by the bare id.

    Core is called without a terminal too (``ger serve``), so the guarantee is the service's,
    not each command's: a caller that passes a footer Change-Id cannot reach Gerrit's
    "Multiple changes found" 404.
    """
    store, on_main = _stack_with_dev_siblings(stack_repo)
    cid = next(iter(on_main))
    draft = {"id": "d0", "line": 1, "message": "draft", "unresolved": True, "patch_set": 1}
    store.set_drafts(build_triplet("testproj", "main", cid), {"a.txt": [draft]})
    monkeypatch.chdir(stack_repo)
    _service_entry_points(cid)[entry](RunContext.open(stack_repo, gerrit=store).gerrit)
    refs = _per_change_refs(store)
    assert refs, "expected a per-change request"
    assert cid not in refs, refs
    assert {store.get_change(ref)["_number"] for ref in refs} == {on_main[cid]}


@pytest.mark.parametrize(
    "entry", ["changes.get", "changes.set_reviewers", "comments.get_chains", "comments.publish_drafts"]
)
def test_a_lowercase_change_id_names_the_push_branch_change_as_gerrit_spells_it(
    entry: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Change-Id read case-insensitively (``i…``) is asked for by the triplet with Gerrit's ``I…``."""
    store, on_main = _stack_with_dev_siblings(stack_repo)
    cid = next(iter(on_main))
    monkeypatch.chdir(stack_repo)
    _service_entry_points(cid.lower())[entry](RunContext.open(stack_repo, gerrit=store).gerrit)
    assert set(_per_change_refs(store)) == {build_triplet("testproj", "main", cid)}


class _TtyStdin:
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("argv", [["-i", "--dry-run"], []], ids=["push -i", "push confirmation line"])
def test_push_prompt_completes_reviewers_on_the_push_branch_change(
    argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer completion on ``ger push``'s options line asks Gerrit about the change being pushed.

    The prompt hands the reviewer catalog the push branch's triplet for the tip's Change-Id.
    With that Change-Id also on ``dev``, a bare Change-Id would 404 ("Multiple changes found")
    and completion would silently turn off.
    """
    import subprocess
    import sys
    from unittest.mock import MagicMock

    from gerrit_workflow_tools.push_input_line import ParseResult, parse as parse_push_options_line
    from gerrit_workflow_tools.reviewer_catalog import ReviewerCatalog

    store, on_main = _stack_with_dev_siblings(
        stack_repo, accounts={7: {"_account_id": 7, "username": "alice"}}, older_patchsets=True
    )
    completed: list[list[str]] = []

    def _prompt(*_a: object, change_ref: str | None, **_k: object) -> ParseResult:
        catalog = ReviewerCatalog(client=store, candidates=[], change_ref=change_ref)
        try:
            catalog.complete_prefix("al")
            assert catalog.wait_until_idle(timeout=5.0)
            completed.append(catalog.complete_prefix("al"))
            assert catalog.status_note is None
        finally:
            catalog.close()
        return parse_push_options_line("")

    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr("gerrit_workflow_tools.cli_push.options_line_available", lambda: True)
    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_reviewers_line", _prompt)
    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_save_reviewers", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("the options line replaces the [Y/n/r] question"))
    pushed = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", pushed)

    code, _out, err = run_cli(stack_repo, push_main, argv, monkeypatch, gerrit=store)

    assert code == ExitCode.OK, err
    assert completed == [["alice"]], (_out, err)
    refs = [str(call.args[0]) for call in store.calls_to("suggest_change_reviewers")]
    assert refs and all(ref not in on_main for ref in refs), refs
    assert {store.get_change(ref)["_number"] for ref in refs} <= set(on_main.values())


@pytest.mark.parametrize("main_fn", [gshow_main, resolve_main])
def test_a_commit_named_like_a_change_wins_and_the_note_says_how_to_name_the_change(
    main_fn: Callable[..., int], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``c7`` is a branch here: a git revision first (spec §2.2), with a note naming the change."""
    _configure_web(stack_repo)
    git("branch", "c7", "HEAD~1", cwd=stack_repo)
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, main_fn, ["--json", "c7"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 0, err
    assert json_stdout(out)["resolution"]["local_sha"] == sha
    assert f"note: 'c7' is commit {sha[:7]}" in err and "write change:7" in err
