# Spec: docu/spec/commands/ci.md, docu/spec/ci-content.md, docu/spec/commands/show.md § CI content
"""``ger ci`` and opt-in ``ger show --ci``: findings behind failed CI links, through the real commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ci import main as ci_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.ci_content_service import TAIL_LINES
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.ci_fakes import FAILING_LOG, JENKINS, FakeCiHttp, jenkins_build
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


CONSOLE = f"{JENKINS}/console"


def _store(repo: Path, *, failed: bool = True) -> ChangeStore:
    """Every stack change on Gerrit; HEAD's Verified -1 with a Jenkins ``Build Failed`` message."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    rows = stack_rows_mb_to_head(repo)
    head = git_out("rev-parse", "HEAD", cwd=repo)
    overrides = [{"verified": -1 if failed and r.sha == head else 1} for r in rows]
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    head_cid = next(r.change_id for r in rows if r.sha == head)
    triplet = f"testproj~main~{head_cid}"
    message = {
        "message": f"Patch Set 1: Verified-1\n\nBuild Failed \n\n{JENKINS}/ : FAILURE",
        "_revision_number": 1,
        "date": "2026-11-09 10:00:00",
    }
    return ChangeStore(details, checks={triplet: []}, messages={triplet: [message] if failed else []})


def _json(out: str) -> dict:
    return json.loads(out)


def test_ci_lists_findings_grouped_by_link_label(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp(jenkins_build())
    code, out, err = run_cli(
        stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=_store(stack_repo), ci_http=http
    )
    assert code == ExitCode.ATTENTION, err
    assert f"jenkins  {CONSOLE}  (Widget #55)" in out
    assert "pytest  demo/calc.py:6  tests/test_calc.py::test_divide: ZeroDivisionError" in out
    assert "Started by Gerrit" not in out, "no body without -v / --full"


def test_ci_json_is_the_contract(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo, ci_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=FakeCiHttp(jenkins_build())
    )
    assert code == ExitCode.ATTENTION, err
    [commit] = _json(out)["commits"]
    [content] = commit["ci_content"]
    assert (content["label"], content["url"], content["source"]) == ("jenkins", CONSOLE, "message")
    prov = content["provenance"]
    assert prov["provider"] == "jenkins"
    assert prov["cache_verdict"] == "body"
    assert prov["error"] is None and prov["cached"] is False
    pytest_finding = content["findings"][0]
    assert pytest_finding["tool"] == "pytest"
    assert pytest_finding["path"] == "demo/calc.py" and pytest_finding["line"] == 6
    assert pytest_finding["fingerprint"]
    assert "body" not in content


def test_ci_verbose_tail_and_full_body(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    long_log = "\n".join(f"line {i}" for i in range(TAIL_LINES + 10)) + "\n" + FAILING_LOG
    store = _store(stack_repo)
    http = FakeCiHttp(jenkins_build(log=long_log))
    code, out, _err = run_cli(stack_repo, ci_main, ["-v", "--color=never"], monkeypatch, gerrit=store, ci_http=http)
    assert code == ExitCode.ATTENTION
    assert "earlier omitted, --full for all" in out
    assert "line 0\n" not in out
    code, out, _err = run_cli(stack_repo, ci_main, ["--full", "--json"], monkeypatch, gerrit=store, ci_http=http)
    [content] = _json(out)["commits"][0]["ci_content"]
    assert content["body"].startswith("line 0\n")
    assert content["body_omitted_lines"] == 0


def test_ci_second_run_is_served_from_the_store(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stack_repo)
    http = FakeCiHttp(jenkins_build())
    run_cli(stack_repo, ci_main, ["--json"], monkeypatch, gerrit=store, ci_http=http)
    fetched = len(http.calls)
    _code, out, _err = run_cli(stack_repo, ci_main, ["--json"], monkeypatch, gerrit=store, ci_http=http)
    assert len(http.calls) == fetched
    assert _json(out)["commits"][0]["ci_content"][0]["provenance"]["cached"] is True


def test_ci_fetch_failure_is_hard(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=_store(stack_repo), ci_http=FakeCiHttp({})
    )
    assert code == ExitCode.CI_CONTENT
    assert "error: HTTP 404" in out, "what could be shown is still shown"
    assert "ci error: could not fetch CI content for 1 link(s)" in err


def test_ci_without_failed_links_is_ok(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp({})
    code, out, err = run_cli(
        stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=_store(stack_repo, failed=False), ci_http=http
    )
    assert code == ExitCode.OK, err
    assert "(no failed CI links)" in out
    assert http.calls == []


def test_ci_markdown(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(
        stack_repo, ci_main, ["--ai"], monkeypatch, gerrit=_store(stack_repo), ci_http=FakeCiHttp(jenkins_build())
    )
    assert code == ExitCode.ATTENTION
    assert "### CI findings" in out
    assert "#### jenkins" in out
    assert "- **pytest** (failed) `demo/calc.py:6`" in out


# --- ger show ----------------------------------------------------------------------


def test_show_does_not_fetch_ci_content_by_default(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp(jenkins_build())
    code, out, _err = run_cli(stack_repo, show_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=http)
    assert code == ExitCode.ATTENTION
    assert http.calls == []
    assert "ci_content" not in _json(out)


def test_show_ci_flag_and_setting(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stack_repo)
    http = FakeCiHttp(jenkins_build())
    _code, out, _err = run_cli(
        stack_repo, show_main, ["--color=never", "--ci"], monkeypatch, gerrit=store, ci_http=http
    )
    assert "CI findings:" in out
    assert "pytest  demo/calc.py:6" in out
    assert "Started by Gerrit" not in out, "show attaches findings only"

    git("config", "ger.showCiContent", "true", cwd=stack_repo)
    _code, out, _err = run_cli(stack_repo, show_main, ["--json"], monkeypatch, gerrit=store, ci_http=http)
    [content] = _json(out)["ci_content"]
    assert "body" not in content
    _code, out, _err = run_cli(stack_repo, show_main, ["--json", "--no-ci"], monkeypatch, gerrit=store, ci_http=http)
    assert "ci_content" not in _json(out)


def test_show_ci_fetch_failure_is_soft(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo,
        show_main,
        ["--color=never", "--ci"],
        monkeypatch,
        gerrit=_store(stack_repo),
        ci_http=FakeCiHttp({}),
    )
    assert code == ExitCode.ATTENTION, err
    assert f"jenkins  {CONSOLE}" in out
    assert "error: HTTP 404" in out


def test_show_and_ci_report_the_same_findings(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One result shape, two projections: the JSON findings must agree."""
    store = _store(stack_repo)
    _c, ci_out, _e = run_cli(
        stack_repo, ci_main, ["--json"], monkeypatch, gerrit=store, ci_http=FakeCiHttp(jenkins_build())
    )
    _c, show_out, _e = run_cli(
        stack_repo, show_main, ["--json", "--ci"], monkeypatch, gerrit=store, ci_http=FakeCiHttp(jenkins_build())
    )
    from_ci = _json(ci_out)["commits"][0]["ci_content"][0]["findings"]
    from_show = _json(show_out)["ci_content"][0]["findings"]
    assert from_ci == from_show
