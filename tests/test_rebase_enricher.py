"""Tests for rebase_enricher (GIT_SEQUENCE_EDITOR wrapper) and cli_rebase."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.cli_gerrit_mocks import (
    build_details_by_change_id,
    make_query_changes_impl,
    stack_rows_mb_to_head,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_todo(rows: list[tuple[str, str, str, str]]) -> str:
    """Minimal rebase todo from (sha, short, subject, raw) rows — oldest-first."""
    lines = [f"pick {short} {subject}\n" for _, short, subject, _ in rows]
    lines.append("\n# Rebase a1b2c3..d4e5f6 onto a1b2c3 (N commands)\n# ...\n")
    return "".join(lines)


def _patch_gerrit(details: dict, *, web_base: str = "https://g.example"):
    """Context manager that patches gerrit_web_url, resolve_gerrit_web_base, and GerritClient
    on the rebase_enricher module with mock data."""
    mock_client = MagicMock()
    mock_client.query_changes.side_effect = make_query_changes_impl(details)
    mock_client.get_comments.return_value = {}

    return (
        patch("gerrit_workflow_tools.rebase_enricher.gerrit_web_url", return_value=web_base),
        patch("gerrit_workflow_tools.rebase_enricher.resolve_gerrit_web_base", return_value=web_base),
        patch("gerrit_workflow_tools.rebase_enricher.GerritClient", return_value=mock_client),
    )


# ---------------------------------------------------------------------------
# Format helpers (unit)
# ---------------------------------------------------------------------------


def test_fmt_verified_all_variants():
    from gerrit_workflow_tools.rebase_enricher import _fmt_verified

    assert _fmt_verified(1) == "v+1"
    assert _fmt_verified(2) == "v+1"
    assert _fmt_verified(-1) == "v-1"
    assert _fmt_verified(-2) == "v-1"
    assert _fmt_verified(0) == "v0 "
    assert _fmt_verified(None) == "v? "
    assert len(_fmt_verified(1)) == 3
    assert len(_fmt_verified(None)) == 3


def test_fmt_cr_all_variants():
    from gerrit_workflow_tools.rebase_enricher import _fmt_cr

    assert _fmt_cr(2) == "cr+2"
    assert _fmt_cr(1) == "cr+1"
    assert _fmt_cr(0) == "cr0 "
    assert _fmt_cr(-1) == "cr-1"
    assert _fmt_cr(-2) == "cr-2"
    assert _fmt_cr(None) == "cr? "
    for v in (2, 1, 0, -1, -2, None):
        assert len(_fmt_cr(v)) == 4


def test_attention_text_variants():
    from gerrit_workflow_tools.gerrit_change_status import LogCommit
    from gerrit_workflow_tools.rebase_enricher import _attention_text

    def _commit(**kw) -> LogCommit:
        defaults = dict(
            sha="abc" * 13 + "ab",
            short_sha="abc1234",
            summary="x",
            change_id=None,
            pushed=True,
            abandoned=False,
            patchset_status="active",
            verified=1,
            code_review=2,
            comments_unresolved=0,
            submittable=True,
        )
        defaults.update(kw)
        return LogCommit(**defaults)

    assert _attention_text(_commit(abandoned=True)) == "abandoned"
    assert _attention_text(_commit(pushed=False, patchset_status="absent")) == "not-pushed"
    assert _attention_text(_commit(submittable=True)) == "submittable"
    assert _attention_text(_commit(verified=-1)) == "build failed"
    assert _attention_text(_commit(ci_failures=["Lint"])) == "CI failed: Lint"
    assert "2 unresolved comments" in _attention_text(_commit(comments_unresolved=2))
    assert "1 unresolved comment" in _attention_text(_commit(comments_unresolved=1))
    # When there are issues, submittable is suppressed
    result = _attention_text(_commit(verified=-1, comments_unresolved=1))
    assert "build failed" in result
    assert "unresolved" in result


def test_enriched_subject_format():
    from gerrit_workflow_tools.gerrit_change_status import LogCommit
    from gerrit_workflow_tools.rebase_enricher import _enriched_subject

    commit = LogCommit(
        sha="abc" * 13 + "ab",
        short_sha="abc1234",
        summary="perf: tweak hot path",
        change_id="Iabc123",
        pushed=True,
        abandoned=False,
        patchset_status="active",
        verified=1,
        code_review=2,
        comments_unresolved=0,
        submittable=True,
    )
    subj = _enriched_subject(commit)
    assert subj.startswith("# ")
    assert "perf: tweak hot path" in subj
    assert "p" in subj       # patchset letter
    assert "v+1" in subj
    assert "cr+2" in subj
    assert "submittable" in subj


def test_enriched_subject_truncates_long_summary():
    from gerrit_workflow_tools.gerrit_change_status import LogCommit
    from gerrit_workflow_tools.rebase_enricher import _SUBJECT_WIDTH, _enriched_subject

    long_summary = "x" * (_SUBJECT_WIDTH + 20)
    commit = LogCommit(
        sha="abc" * 13 + "ab",
        short_sha="abc1234",
        summary=long_summary,
        change_id=None,
        pushed=False,
        abandoned=False,
        patchset_status="absent",
        verified=None,
        code_review=None,
        comments_unresolved=0,
    )
    subj = _enriched_subject(commit)
    # Visible subject portion should be capped at SUBJECT_WIDTH chars (includes the truncation char)
    assert "\u2026" in subj  # ellipsis


def test_enriched_subject_not_pushed_shows_dash():
    from gerrit_workflow_tools.gerrit_change_status import LogCommit
    from gerrit_workflow_tools.rebase_enricher import _enriched_subject

    commit = LogCommit(
        sha="abc" * 13 + "ab",
        short_sha="abc1234",
        summary="wip: local only",
        change_id=None,
        pushed=False,
        abandoned=False,
        patchset_status="absent",
        verified=None,
        code_review=None,
        comments_unresolved=0,
    )
    subj = _enriched_subject(commit)
    assert "-" in subj
    assert "not-pushed" in subj


# ---------------------------------------------------------------------------
# _enrich_todo: passthrough cases
# ---------------------------------------------------------------------------


def test_enrich_todo_passthrough_without_gerrit_url(tmp_path: Path):
    """When gerrit.webUrl is not configured the todo is returned unchanged."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    text = "pick abc1234 some commit\n# comment\n"
    with patch("gerrit_workflow_tools.rebase_enricher.gerrit_web_url", return_value=None):
        result = _enrich_todo(text, tmp_path)

    assert result == text


def test_enrich_todo_preserves_comment_and_blank_lines(tmp_path: Path):
    """Comment lines and blank lines are preserved as-is."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    text = "\n# some comment\n\n"
    with patch("gerrit_workflow_tools.rebase_enricher.gerrit_web_url", return_value=None):
        result = _enrich_todo(text, tmp_path)

    assert result == text


def test_enrich_todo_passthrough_when_no_commit_lines(tmp_path: Path):
    """If todo has only comment lines (e.g. empty rebase), return unchanged."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    text = "# pick abc no pick lines here\n"
    with patch("gerrit_workflow_tools.rebase_enricher.gerrit_web_url", return_value="https://g.example"):
        result = _enrich_todo(text, tmp_path)

    assert result == text


# ---------------------------------------------------------------------------
# _enrich_todo: enrichment with real repo + mocked Gerrit
# ---------------------------------------------------------------------------


def test_enrich_todo_annotates_with_gerrit_status(stack_repo: Path):
    """Happy path: each pick line gets v+1 cr+2 etc. from the mocked Gerrit API."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    text = _make_todo(rows)

    wg, wrgb, wgc = _patch_gerrit(details)
    with wg, wrgb, wgc:
        result = _enrich_todo(text, stack_repo)

    # Each original pick line must now contain enriched subject markers.
    for _, short, _subject, _ in rows:
        assert f"pick {short} # " in result

    # Default mock data: verified=+1, cr=+2, submittable=True.
    assert "v+1" in result
    assert "cr+2" in result
    assert "submittable" in result

    # Original comment / blank lines must survive.
    assert "# Rebase a1b2c3" in result


def test_enrich_todo_preserves_action_and_sha(stack_repo: Path):
    """The action token and SHA must not be modified."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    text = _make_todo(rows).replace("pick ", "drop ", 1)  # first line: drop

    wg, wrgb, wgc = _patch_gerrit(details)
    with wg, wrgb, wgc:
        result = _enrich_todo(text, stack_repo)

    first_short = rows[0][1]
    assert result.startswith(f"drop {first_short} # ")


def test_enrich_todo_unresolved_comments_shown(stack_repo: Path):
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows, per_index_overrides=[{"unresolved_comment_count": 3}])
    text = _make_todo(rows)

    wg, wrgb, wgc = _patch_gerrit(details)
    with wg, wrgb, wgc:
        result = _enrich_todo(text, stack_repo)

    assert "com" in result
    assert "unresolved comment" in result


def test_enrich_todo_build_failed_shown(stack_repo: Path):
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows, per_index_overrides=[{"verified": -1}])
    text = _make_todo(rows)

    wg, wrgb, wgc = _patch_gerrit(details)
    with wg, wrgb, wgc:
        result = _enrich_todo(text, stack_repo)

    assert "v-1" in result
    assert "build failed" in result


def test_enrich_todo_on_gerrit_api_error_degrades_gracefully(stack_repo: Path):
    """When the Gerrit API is unreachable, fetch_gerrit_data catches the error internally
    and returns commits as absent/not-pushed rather than raising.  The enricher should
    still succeed and annotate each line with the degraded status."""
    from gerrit_workflow_tools.rebase_enricher import _enrich_todo

    rows = stack_rows_mb_to_head(stack_repo)
    text = _make_todo(rows)

    broken_client = MagicMock()
    from gerrit_workflow_tools.gerrit_client import GerritApiError
    broken_client.query_changes.side_effect = GerritApiError("timeout")

    with (
        patch("gerrit_workflow_tools.rebase_enricher.gerrit_web_url", return_value="https://g.example"),
        patch("gerrit_workflow_tools.rebase_enricher.resolve_gerrit_web_base", return_value="https://g.example"),
        patch("gerrit_workflow_tools.rebase_enricher.GerritClient", return_value=broken_client),
    ):
        result = _enrich_todo(text, stack_repo)

    # Must not raise; each pick line must still be enriched (degraded to not-pushed).
    for _, short, _subject, _ in rows:
        assert f"pick {short} # " in result
    assert "not-pushed" in result


# ---------------------------------------------------------------------------
# main: end-to-end (editor mocked out)
# ---------------------------------------------------------------------------


def test_main_enriches_todo_and_launches_editor(tmp_path: Path, stack_repo: Path, monkeypatch):
    """main() writes the enriched text returned by _enrich_todo and opens the editor."""
    from gerrit_workflow_tools.rebase_enricher import main as enricher_main

    rows = stack_rows_mb_to_head(stack_repo)
    todo = tmp_path / "git-rebase-todo"
    todo.write_text(_make_todo(rows), encoding="utf-8")
    enriched_text = "pick abc1234 # enriched subject  p v+1 cr+2     # submittable\n"

    monkeypatch.chdir(stack_repo)
    # GIT_EDITOR set so _resolve_editor returns without a git-config call.
    monkeypatch.setenv("GIT_EDITOR", "vim")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))

    # Mock _enrich_todo to avoid real Gerrit/git calls; only subprocess.run
    # (the editor launch) remains, which is the thing we want to test here.
    with (
        patch("gerrit_workflow_tools.rebase_enricher._enrich_todo", return_value=enriched_text),
        patch("gerrit_workflow_tools.rebase_enricher.subprocess.run", mock_run),
    ):
        code = enricher_main(["_", str(todo)])

    assert code == 0
    assert todo.read_text(encoding="utf-8") == enriched_text

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "vim" in cmd
    assert str(todo) in cmd


def test_main_on_gerrit_error_prepends_comment_and_still_opens_editor(
    tmp_path: Path, stack_repo: Path, monkeypatch
):
    """When _enrich_todo raises, main() prepends an error comment and opens the editor
    with the original (unenriched) todo — the rebase can still proceed."""
    from gerrit_workflow_tools.gerrit_client import GerritApiError
    from gerrit_workflow_tools.rebase_enricher import main as enricher_main

    rows = stack_rows_mb_to_head(stack_repo)
    todo = tmp_path / "git-rebase-todo"
    original_text = _make_todo(rows)
    todo.write_text(original_text, encoding="utf-8")

    monkeypatch.chdir(stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "vim")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))

    with (
        patch(
            "gerrit_workflow_tools.rebase_enricher._enrich_todo",
            side_effect=GerritApiError("connection refused"),
        ),
        patch("gerrit_workflow_tools.rebase_enricher.subprocess.run", mock_run),
    ):
        code = enricher_main(["_", str(todo)])

    assert code == 0
    text = todo.read_text(encoding="utf-8")

    # Error comment must appear at the top.
    assert text.startswith("# ger rebase:")
    assert "enrichment failed" in text

    # Original pick lines must be intact (unenriched fallback).
    for _, short, subject, _ in rows:
        assert f"pick {short} {subject}" in text

    # Editor was still launched despite the error.
    mock_run.assert_called_once()


def test_main_returns_editor_exit_code(tmp_path: Path, stack_repo: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_enricher import main as enricher_main

    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    todo = tmp_path / "git-rebase-todo"
    todo.write_text(_make_todo(rows), encoding="utf-8")

    monkeypatch.chdir(stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "vim")
    mock_run = MagicMock(return_value=MagicMock(returncode=42))

    wg, wrgb, wgc = _patch_gerrit(details)
    with wg, wrgb, wgc, patch("gerrit_workflow_tools.rebase_enricher.subprocess.run", mock_run):
        code = enricher_main(["_", str(todo)])

    assert code == 42


def test_main_missing_args_returns_1():
    from gerrit_workflow_tools.rebase_enricher import main as enricher_main

    assert enricher_main(["_"]) == 1


# ---------------------------------------------------------------------------
# Editor resolution
# ---------------------------------------------------------------------------


def test_resolve_editor_prefers_grebase_editor(tmp_path: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_enricher import _resolve_editor

    monkeypatch.setenv("GREBASE_EDITOR", "myeditor")
    monkeypatch.setenv("GIT_EDITOR", "other")
    assert _resolve_editor(tmp_path) == "myeditor"


def test_resolve_editor_falls_back_to_git_editor(tmp_path: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_enricher import _resolve_editor

    monkeypatch.delenv("GREBASE_EDITOR", raising=False)
    monkeypatch.setenv("GIT_EDITOR", "nano")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    with patch("gerrit_workflow_tools.rebase_enricher.git") as mock_git:
        mock_git.return_value = MagicMock(returncode=1, stdout="")
        assert _resolve_editor(tmp_path) == "nano"


def test_resolve_editor_falls_back_to_editor_env(tmp_path: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_enricher import _resolve_editor

    monkeypatch.delenv("GREBASE_EDITOR", raising=False)
    monkeypatch.delenv("GIT_EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "emacs")
    with patch("gerrit_workflow_tools.rebase_enricher.git") as mock_git:
        mock_git.return_value = MagicMock(returncode=1, stdout="")
        assert _resolve_editor(tmp_path) == "emacs"


def test_resolve_editor_vi_fallback(tmp_path: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_enricher import _resolve_editor

    for var in ("GREBASE_EDITOR", "GIT_EDITOR", "VISUAL", "EDITOR"):
        monkeypatch.delenv(var, raising=False)
    with patch("gerrit_workflow_tools.rebase_enricher.git") as mock_git:
        mock_git.return_value = MagicMock(returncode=1, stdout="")
        assert _resolve_editor(tmp_path) == "vi"


# ---------------------------------------------------------------------------
# cli_rebase
# ---------------------------------------------------------------------------

# subprocess.run is a global — patching it via the module affects git_run.py
# too. Use a smart fake that only intercepts the "git rebase -i" launch and
# forwards all other calls (rev-parse, config reads, …) to the real subprocess.
_real_subprocess_run = subprocess.run


def _make_rebase_interceptor(captured: dict):
    """Return a fake subprocess.run that intercepts only ``git rebase -i …``."""

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["git", "rebase", "-i"]:
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env", {})
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return _real_subprocess_run(cmd, **kwargs)

    return fake_run


def test_cli_rebase_sets_sequence_editor_env(stack_repo: Path, monkeypatch):
    from gerrit_workflow_tools.cli_rebase import main as rebase_main

    captured: dict = {}
    monkeypatch.chdir(stack_repo)
    with patch("subprocess.run", side_effect=_make_rebase_interceptor(captured)):
        code = rebase_main([])

    assert code == 0
    assert "GIT_SEQUENCE_EDITOR" in captured["env"]
    assert "rebase_enricher" in captured["env"]["GIT_SEQUENCE_EDITOR"]


def test_cli_rebase_passes_merge_base_to_git(stack_repo: Path, monkeypatch):
    from gerrit_workflow_tools.cli_rebase import main as rebase_main
    from gerrit_workflow_tools.stack import merge_base_with_target

    monkeypatch.chdir(stack_repo)
    expected_base, _, _ = merge_base_with_target(stack_repo)

    captured: dict = {}
    with patch("subprocess.run", side_effect=_make_rebase_interceptor(captured)):
        code = rebase_main([])

    assert code == 0
    assert captured["cmd"] == ["git", "rebase", "-i", expected_base]


def test_cli_rebase_uses_given_rev(stack_repo: Path, monkeypatch):
    """When REV is given, it is resolved and passed as the rebase base."""
    from gerrit_workflow_tools.cli_rebase import main as rebase_main

    rows = stack_rows_mb_to_head(stack_repo)
    target_sha = rows[0][0]  # Full SHA of the first (oldest) stack commit.

    captured: dict = {}
    monkeypatch.chdir(stack_repo)
    with patch("subprocess.run", side_effect=_make_rebase_interceptor(captured)):
        code = rebase_main([rows[0][1]])  # pass short SHA; resolve_stack_commit expands it

    assert code == 0
    assert captured["cmd"][-1] == target_sha


def test_cli_rebase_debug_log_sets_env_flag(stack_repo: Path, monkeypatch):
    from gerrit_workflow_tools.cli_rebase import main as rebase_main

    captured: dict = {}
    monkeypatch.chdir(stack_repo)
    with patch("subprocess.run", side_effect=_make_rebase_interceptor(captured)):
        rebase_main(["--debug-log"])

    assert captured.get("env", {}).get("GREBASE_DEBUG_LOG") == "1"
