# Spec: docu/spec/commands/log.md, show.md, ci.md
# Covers: rows fitted to the terminal, the chain blocker, next steps, wording of ger log / show / ci

"""What ``ger log``, ``ger show`` and ``ger ci`` tell the author at each step (mocked Gerrit)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.render import style
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


LONG = "Teach the parser to recover from a missing closing bracket in nested argument lists"


def _on_terminal(monkeypatch: pytest.MonkeyPatch, columns: int) -> None:
    """Output goes to a terminal *columns* wide (colour and links off, so the text can be compared)."""
    monkeypatch.setattr(style, "_stdout_is_tty", lambda: True)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", "50")


def _store(repo: Path, overrides: list[dict[str, Any]]) -> ChangeStore:
    rows = stack_rows_mb_to_head(repo)
    return ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))


def _long_subject_stack(repo: Path) -> None:
    git("commit", "--allow-empty", "-m", f"{LONG}\n\nChange-Id: I{'7' * 40}", cwd=repo)


def test_log_on_a_terminal_cuts_subjects_so_rows_and_hints_fit(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _long_subject_stack(stack_repo)
    store = _store(stack_repo, [{}, {"verified": -1, "submittable": False}, {}, {}, {}])
    _on_terminal(monkeypatch, 80)
    code, out, err = run_cli(
        stack_repo, log_main, ["--color", "never", "--hyperlinks", "never"], monkeypatch, gerrit=store
    )
    assert code == 0, err
    lines = out.splitlines()
    assert all(len(line) <= 80 for line in lines), "\n".join(line for line in lines if len(line) > 80)
    long_row = next(line for line in lines if line.startswith(stack_rows_mb_to_head(stack_repo)[-1].short_sha))
    assert "Teach the parser" in long_row
    assert "…" in long_row
    assert "# build failed" in out  # the hint is not pushed off the screen


def test_log_piped_is_never_cut(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _long_subject_stack(stack_repo)
    store = _store(stack_repo, [{}, {"verified": -1, "submittable": False}, {}, {}, {}])
    monkeypatch.setenv("COLUMNS", "80")
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert code == 0, err
    assert LONG in out
    assert "…" not in out


def _plain_log(repo: Path, monkeypatch: pytest.MonkeyPatch, store: ChangeStore, *args: str) -> list[str]:
    code, out, err = run_cli(repo, log_main, ["--color", "never", *args], monkeypatch, gerrit=store)
    assert code == 0, err
    return out.splitlines()


def test_log_names_the_commit_that_blocks_the_chain_once(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The blocker says why on its own row; rows above do not repeat it; the footer names it."""
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = _store(stack_repo, [{}, {"cr": 1, "submittable": False}, {"cr": -1, "submittable": False}, {}])
    lines = _plain_log(stack_repo, monkeypatch, store)
    assert lines[1].startswith(rows[1].short_sha) and lines[1].endswith("# awaiting review")
    assert lines[2].endswith("# review issues")
    assert lines[3].endswith("# submittable")
    assert not any("chain blocked" in line for line in lines[: len(rows)])
    assert lines[-1] == f"chain blocked by {rows[1].short_sha} (awaiting review)"
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    payload = json.loads(out)
    assert [c["blocks_chain"] for c in payload["commits"]] == [False, True, False, False]
    assert payload["next"][-1] == {
        "kind": "chain-blocked",
        "shas": [rows[1].sha],
        "beyond_boundary": False,
        "reason": "awaiting-review",
    }


def test_log_counts_and_advises_a_commit_to_upload(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit amended after its push (ahead of Gerrit) is what `ger push` uploads: counted, and a next: line."""
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    store = _store(stack_repo, [{}, {}, {}, {}])
    (stack_repo / "amended.txt").write_text("new content", encoding="utf-8")
    git("add", "amended.txt", cwd=stack_repo)
    git("commit", "--amend", "--no-edit", cwd=stack_repo)
    lines = _plain_log(stack_repo, monkeypatch, store)
    assert "summary: 4 on Gerrit · 1 to upload" in lines
    assert "next: 1 commit to upload with `ger push`" in lines
    ai = _plain_log(stack_repo, monkeypatch, store, "--ai")
    assert ai[0].endswith("ger push uploads 1: commit 4")


def test_log_says_a_push_uploads_nothing_when_gerrit_has_every_commit(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    store = _store(stack_repo, [{}, {}, {}, {}])
    ai = _plain_log(stack_repo, monkeypatch, store, "--ai")
    assert ai[0] == "stack: testproj, branch main, 4 commits, ger push uploads nothing"
    assert not any(line.startswith("next:") for line in ai)


def test_log_advises_on_an_abandoned_change_and_marks_a_merged_one(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict[str, Any]] = [
        {"status": "MERGED", "submittable": False},
        {"status": "ABANDONED", "submittable": False},
        {},
        {},
    ]
    lines = _plain_log(stack_repo, monkeypatch, _store(stack_repo, overrides))
    assert lines[0].startswith(rows[0].short_sha) and lines[0].endswith("# merged")
    assert "summary: 2 on Gerrit · 1 merged · 1 abandoned" in lines
    drop = rows[1].short_sha
    assert (
        f"next: {drop} is abandoned on Gerrit; restore it there or drop the commit (`ger edit --drop {drop}`)" in lines
    )


def test_log_help_explains_every_status_token_it_can_print(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The legend comes from the tokens' one definition: every token the column can show is explained."""
    from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
    from gerrit_workflow_tools.render.status_fmt import code_review_token, patchset_token, verified_token

    def make_log_commit(
        *, patchset_status: PatchsetStatus = PatchsetStatus.ACTIVE, abandoned: bool = False
    ) -> LogCommit:
        return LogCommit("a" * 40, "aaaaaaa", "s", None, True, abandoned, patchset_status, None, None, 0)

    code, out, _err = run_cli(stack_repo, log_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    legend = out[out.index("status columns:") :].splitlines()[1:]
    explained = {word for line in legend for word in line.strip().split("  ")[0].split()}
    shown = {patchset_token(make_log_commit(patchset_status=status)) for status in PatchsetStatus}
    shown.add(patchset_token(make_log_commit(abandoned=True)))
    shown |= {verified_token(v).strip() for v in (1, -1, None)} | {code_review_token(None).strip(), "com"}
    assert shown <= explained, shown - explained
    assert "examples:" in out and "ger log origin/main..HEAD" in out


def _commented_head(repo: Path, **head: Any) -> tuple[ChangeStore, str]:
    """The stack on Gerrit; HEAD's change has a patchset-level comment from an hour ago and an inline one."""
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=repo)
    rows = stack_rows_mb_to_head(repo)
    store = _store(repo, [{}, {}, {}, {"unresolved_comment_count": 2, "submittable": False, **head}])
    triplet = f"testproj~main~{rows[-1].change_id}"
    hour_ago = (datetime.now(timezone.utc) - timedelta(minutes=61)).strftime("%Y-%m-%d %H:%M:%S.000000000")
    alice = {"username": "alice", "name": "Alice"}
    common = {"unresolved": True, "updated": hour_ago, "author": alice, "patch_set": 1}
    store.set_comments(
        triplet,
        {
            "/PATCHSET_LEVEL": [{"id": "ps0level_0001", "message": "Overall fine, but please add tests.", **common}],
            "calc.py": [{"id": "inline_0002", "line": 3, "message": "Could this be lazy? " * 6, **common}],
        },
    )
    return store, triplet


def test_show_comment_boxes_name_the_reply_id_patchset_and_age_and_fit_the_terminal(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.cli_show import main as show_main

    store, _triplet = _commented_head(stack_repo)
    _on_terminal(monkeypatch, 80)
    args = ["HEAD", "--color", "never", "--hyperlinks", "never"]
    code, out, err = run_cli(stack_repo, show_main, args, monkeypatch, gerrit=store)
    assert code == 0, err
    box = [line for line in out.splitlines() if line.lstrip().startswith(("╭", "│", "╰"))]
    assert all(len(line) <= 80 for line in box), "\n".join(box)
    assert len({len(line) for line in box}) == 1, "every box of a commit is as wide as the others"
    tops = [line for line in box if "╭" in line]
    assert "─ Patchset-level comment ─" in tops[0] and tops[0].rstrip("╮─ ").endswith("ps0level_0001")
    assert tops[1].rstrip("╮─ ").endswith("inline_0002")
    assert "/PATCHSET_LEVEL" not in out
    assert "│ alice · PS1 · 1h ago" in out
    assert all(line.endswith(" │") or "╭" in line or "╰" in line for line in box), "text never touches the border"


def test_show_ai_says_how_to_reply_to_each_comment(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_show import main as show_main

    store, _triplet = _commented_head(stack_repo)
    code, out, err = run_cli(stack_repo, show_main, ["HEAD", "--ai"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "Reply with `ger reply ps0level_0001`" in out
    assert "Reply with `ger reply inline_0002`" in out
    assert "**alice** · PS1 · 1h ago" in out


def test_show_ai_carries_the_facts_of_the_text_view_under_nested_headings(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.cli_show import main as show_main

    reviewers = {"REVIEWER": [{"username": "bob", "_account_id": 2}], "CC": [{"username": "carol", "_account_id": 3}]}
    store, _triplet = _commented_head(stack_repo, private=True, work_in_progress=True, reviewers=reviewers)
    code, out, err = run_cli(stack_repo, show_main, ["HEAD", "--ai"], monkeypatch, gerrit=store)
    assert code == 0, err
    lines = out.splitlines()
    assert "- Patchset: pushed: Gerrit's current patchset" in lines
    assert "- Change is work in progress and private" in lines
    assert "- Reviewers: bob (cc: carol)" in lines
    section = lines.index("### Unresolved comments")
    threads = [line for line in lines[section:] if line.startswith("#")]
    assert threads[1:] == ["#### Patchset-level comment", "#### `calc.py:3`"]


def test_read_commands_list_refresh_and_it_asks_gerrit_again(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--refresh`` is in the help of the commands that read Gerrit, and bypasses the trust window."""
    from gerrit_workflow_tools.cli_ci import main as ci_main
    from gerrit_workflow_tools.cli_config import main as config_main
    from gerrit_workflow_tools.cli_show import main as show_main

    for main in (log_main, show_main, ci_main):
        _code, out, _err = run_cli(stack_repo, main, ["--help"], monkeypatch, catch_sys_exit=True)
        assert "--refresh" in out, main
    _code, out, _err = run_cli(stack_repo, config_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert "--refresh" not in out  # nothing cached to bypass

    store = _store(stack_repo, [{}, {}, {}, {}])
    _plain_log(stack_repo, monkeypatch, store)
    before = len(store.calls)
    _plain_log(stack_repo, monkeypatch, store)
    assert len(store.calls) == before, "inside the trust window the cache answers"
    _plain_log(stack_repo, monkeypatch, store, "--refresh")
    assert len(store.calls) > before
