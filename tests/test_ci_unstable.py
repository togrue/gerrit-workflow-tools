# Spec: docu/spec/ci-content.md, docu/gerrit-ci-strategies.md (issue #136: unstable builds)
"""An unstable Jenkins build is a failure like any other: ``ger log`` flags it, ``ger ci`` fetches its log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ci import main as ci_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.ci_links import CiVerdict
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from tests.change_store import ChangeStore
from tests.ci_fakes import FAILING_LOG, JENKINS, FakeCiHttp, jenkins_build
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


UNSTABLE_LOG = FAILING_LOG.replace("Finished: FAILURE", "Finished: UNSTABLE")


def _store(repo: Path) -> ChangeStore:
    """Every stack change on Gerrit; HEAD's Verified -1 with a Jenkins ``Build Unstable`` message."""
    git("config", "ger.webUrl", "https://g.example", cwd=repo)
    rows = stack_rows_mb_to_head(repo)
    head = git_out("rev-parse", "HEAD", cwd=repo)
    overrides = [{"verified": -1 if r.sha == head else 1} for r in rows]
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    triplet = f"testproj~main~{next(r.change_id for r in rows if r.sha == head)}"
    message = {
        "message": f"Patch Set 1: Verified-1\n\nBuild Unstable \n\n{JENKINS}/ : UNSTABLE",
        "_revision_number": 1,
        "date": "2026-11-09 10:00:00",
    }
    return ChangeStore(details, checks={triplet: []}, messages={triplet: [message]})


def test_the_overlay_carries_an_unstable_build_as_a_ci_failure(stack_repo: Path) -> None:
    run = RunContext.open(stack_repo, gerrit=_store(stack_repo))
    rows = list(get_stack_snapshot(stack_repo).commits)
    head = annotate(rows, service=run.gerrit, cwd=stack_repo, fetch_ci_pipelines=True).require_gerrit()[-1]
    [link] = head.ci_links
    assert (link.build_url, link.verdict) == (JENKINS, CiVerdict.UNSTABLE)
    [pipeline] = head.ci_pipelines
    assert (pipeline.state, pipeline.verdict) == ("FAILED", CiVerdict.UNSTABLE)
    assert Attention.CI_FAILED in head.attention_reasons


def test_ger_ci_fetches_and_parses_an_unstable_build_log(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp(jenkins_build(log=UNSTABLE_LOG))
    code, out, err = run_cli(stack_repo, ci_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=http)
    assert code == int(ExitCode.OK), err
    [content] = json.loads(out)["commits"][0]["ci_content"]
    assert content["build_url"] == JENKINS
    assert f"{JENKINS}/consoleText" in http.urls()
    assert [f["tool"] for f in content["findings"]][:1] == ["pytest"]


def test_every_shell_says_unstable(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The verdict reaches ``ger ci --json``, ``ger log --json`` and the text view, in words (not colour alone)."""
    from gerrit_workflow_tools.cli_log import main as log_main

    http = FakeCiHttp(jenkins_build(log=UNSTABLE_LOG))
    _code, out, _err = run_cli(stack_repo, ci_main, ["--json"], monkeypatch, gerrit=_store(stack_repo), ci_http=http)
    assert json.loads(out)["commits"][0]["ci_content"][0]["verdict"] == "unstable"

    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=_store(stack_repo))
    head = json.loads(out)["commits"][-1]
    assert [link["verdict"] for link in head["ci_links"]] == ["unstable"]

    _code, out, _err = run_cli(stack_repo, log_main, ["-v"], monkeypatch, gerrit=_store(stack_repo))
    assert "(unstable)" in out
