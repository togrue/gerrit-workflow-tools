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


def _store(repo: Path, *, failed: bool = True, head_pushed_as: str | None = None) -> ChangeStore:
    """Every stack change on Gerrit; HEAD's Verified -1 with a Jenkins ``Build Failed`` message.

    *head_pushed_as*: the commit Gerrit has as HEAD's patchset, when not HEAD itself.
    """
    git("config", "ger.webUrl", "https://g.example", cwd=repo)
    rows = stack_rows_mb_to_head(repo)
    head = git_out("rev-parse", "HEAD", cwd=repo)
    overrides = [{"verified": -1 if failed and r.sha == head else 1} for r in rows]
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    for detail in details.values():
        if head_pushed_as and detail.get("current_revision") == head:
            detail["revisions"] = {head_pushed_as: detail["revisions"][head]}
            detail["current_revision"] = head_pushed_as
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
    assert code == int(ExitCode.OK), err
    assert f"jenkins  {JENKINS}  (log: {CONSOLE})  (Widget #55)" in out
    assert "pytest  demo/calc.py:6  tests/test_calc.py::test_divide: ZeroDivisionError" in out
    assert "Started by Gerrit" not in out, "no body without -v / --full"


def test_ci_json_is_the_contract(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo, ci_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=FakeCiHttp(jenkins_build())
    )
    assert code == int(ExitCode.OK), err
    [commit] = _json(out)["commits"]
    [content] = commit["ci_content"]
    assert (content["label"], content["url"], content["source"]) == ("jenkins", CONSOLE, "message")
    assert content["build_url"] == JENKINS, "the URL as the CI message gave it, to quote"
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
    assert code == int(ExitCode.OK)
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
    assert err.strip() == "ci error: the CI log could not be fetched (see above)"


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
    assert code == int(ExitCode.OK)
    assert "### CI findings" in out
    assert "#### jenkins" in out
    assert "- **pytest** (failed) `demo/calc.py:6`" in out


# --- ger show ----------------------------------------------------------------------


def test_show_does_not_fetch_ci_content_by_default(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp(jenkins_build())
    code, out, _err = run_cli(stack_repo, show_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=http)
    assert code == int(ExitCode.OK)
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
    assert code == int(ExitCode.OK), err
    assert f"jenkins  {JENKINS}\n" in out  # the log it could not read is named once, on the error line
    assert "error: HTTP 404" in out and f"; open {CONSOLE}" in out


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


def test_ci_says_why_a_request_failed_in_words_once(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that cannot be found reads as such, not as a Python repr, and is reported once."""
    import socket
    from urllib.error import URLError

    from gerrit_workflow_tools.core import ci_http

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(ci_http, "urlopen", unreachable)
    code, out, err = run_cli(stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=_store(stack_repo))
    assert code == ExitCode.CI_CONTENT
    assert "error: could not reach ci.example.org: host not found; open " in out
    assert "gaierror" not in out + err
    assert err.strip() == "ci error: the CI log could not be fetched (see above)"


def test_ci_on_head_points_at_the_commit_below_whose_ci_failed(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD has no failed CI link, the commit below it has: say which, and how to look, with its subject."""
    store = _store(stack_repo)
    git("commit", "--allow-empty", "-m", f"Above the failure\n\nChange-Id: I{'9' * 40}", cwd=stack_repo)
    failed_sha = stack_rows_mb_to_head(stack_repo)[-2].short_sha
    code, out, err = run_cli(stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=store, ci_http=FakeCiHttp({}))
    assert code == ExitCode.OK, err
    lines = out.splitlines()
    assert lines[1] == "    Above the failure"
    assert lines[-1] == f"{failed_sha} below has failed CI: `ger ci {failed_sha}`, or `ger ci --stack`"


# --- While you fix it: the last pushed patchset's findings -------------------------


def _calc(repo: Path, lines: list[str]) -> None:
    """Amend HEAD so ``demo/calc.py`` holds *lines*; the Change-Id stays."""
    (repo / "demo").mkdir(exist_ok=True)
    (repo / "demo" / "calc.py").write_text("".join(f"{line}\n" for line in lines))
    git("add", "demo/calc.py", cwd=repo)
    git("commit", "-q", "--amend", "--no-edit", cwd=repo)


_CALC = [f"line {n}" for n in range(1, 11)]


def test_findings_stay_while_you_fix_them_at_their_lines_now(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Amended after CI failed: the findings are patchset 1's, labelled so, and their line is where it went."""
    _calc(stack_repo, _CALC)
    store = _store(stack_repo)  # HEAD as it is now is patchset 1, which CI failed
    _calc(stack_repo, ["import os", "import sys", *_CALC])
    http = FakeCiHttp(jenkins_build())
    code, out, err = run_cli(stack_repo, ci_main, ["--json"], monkeypatch, gerrit=store, ci_http=http)
    assert code == int(ExitCode.OK), err
    [content] = _json(out)["commits"][0]["ci_content"]
    assert (content["patch_set"], content["lines_on_patchset"]) == (1, False)
    finding = content["findings"][0]
    assert (finding["path"], finding["line"], finding["patchset_line"], finding["line_changed"]) == (
        "demo/calc.py",
        8,
        6,
        False,
    )
    code, out, _err = run_cli(stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=store, ci_http=http)
    assert "from patchset 1 (the last pushed), before your local changes" in out
    assert "pytest  demo/calc.py:8 (line 6 on patchset 1)  tests/test_calc.py::test_divide" in out
    _code, out, _err = run_cli(
        stack_repo, show_main, ["--color=never", "--ci"], monkeypatch, gerrit=store, ci_http=http
    )
    assert "demo/calc.py:8 (line 6 on patchset 1)" in out, "ger show --ci says the same"


def test_a_finding_whose_line_you_changed_says_so(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _calc(stack_repo, _CALC)
    store = _store(stack_repo)
    _calc(stack_repo, [*_CALC[:5], "fixed", *_CALC[6:]])
    code, out, err = run_cli(
        stack_repo, ci_main, ["--color=never"], monkeypatch, gerrit=store, ci_http=FakeCiHttp(jenkins_build())
    )
    assert code == int(ExitCode.OK), err
    assert "demo/calc.py:5 (line 6 on patchset 1, changed since)" in out


def test_the_last_pushed_builds_raise_no_attention_on_new_code(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict is not about the local code: no ci-failed, but ger log -v still lists the build, labelled."""
    from gerrit_workflow_tools.cli_log import main as log_main

    _calc(stack_repo, _CALC)
    store = _store(stack_repo)
    _calc(stack_repo, ["import os", *_CALC])
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    head = _json(out)["commits"][-1]
    assert head["ci_patch_set"] == 1 and head["ci_links"], "the links are kept"
    assert "ci-failed" not in head["attention_reasons"]
    _code, out, _err = run_cli(stack_repo, log_main, ["-v", "--color=never"], monkeypatch, gerrit=store)
    assert "(patchset 1, before your local changes)" in out


def test_findings_of_a_patchset_not_here_keep_its_lines(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gerrit's patchset commit is not in this repository: its lines are shown as its own."""
    _calc(stack_repo, _CALC)
    store = _store(stack_repo, head_pushed_as="f" * 40)
    code, out, err = run_cli(
        stack_repo, ci_main, ["--json"], monkeypatch, gerrit=store, ci_http=FakeCiHttp(jenkins_build())
    )
    assert code == int(ExitCode.OK), err
    [content] = _json(out)["commits"][0]["ci_content"]
    assert (content["patch_set"], content["lines_on_patchset"]) == (1, True)
    assert content["findings"][0]["path"] == "demo/calc.py", "the path is still resolved, against the local commit"
