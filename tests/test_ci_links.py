"""Unit tests for CI links, Checks-first filtering, and ``.ger/ci`` strategies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_style import init_hyperlink_mode, strip_ansi
from gerrit_workflow_tools.core.ci_links import (
    CiLink,
    CiPipeline,
    apply_ci_strategy,
    ci_pipelines_from_checks,
    failed_check_names,
    prefer_checks_links,
)
from gerrit_workflow_tools.core.ci_strategy import clear_ci_strategy_cache, resolve_ci_strategy
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import CommitStatusInput, LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.render.commit_row import extra_detail_lines, format_ci_lines
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


def test_failed_check_names_filters_failed_only() -> None:
    rows = [
        {"state": "FAILED", "checker_name": "build"},
        {"state": "SUCCESSFUL", "checker_name": "lint"},
        {"state": "FAILED", "name": "unit"},
    ]
    assert failed_check_names(rows) == ["build", "unit"]


def test_prefer_checks_links_drops_messages_when_checks_present() -> None:
    links = [
        CiLink(label="jenkins", url="https://ci/1/console", source="checks"),
        CiLink(label="bot", url="https://ci/from-msg", source="message"),
    ]
    assert prefer_checks_links(links) == [links[0]]


def test_prefer_checks_links_keeps_messages_when_no_checks() -> None:
    links = [CiLink(label="bot", url="https://ci/from-msg", source="message")]
    assert prefer_checks_links(links) == links


def test_apply_ci_strategy_none_is_noop() -> None:
    assert apply_ci_strategy(None, project="p", checks=[], messages=[]) == []


def test_apply_ci_strategy_filters_checks_first() -> None:
    def strategy(*, project: str, checks: list, messages: list) -> list[CiLink]:
        del project, checks, messages
        return [
            CiLink(label="c", url="https://c", source="checks"),
            CiLink(label="m", url="https://m", source="message"),
        ]

    out = apply_ci_strategy(strategy, project="p", checks=[], messages=[])
    assert out == [CiLink(label="c", url="https://c", source="checks")]


def _write_registry(repo: Path, body: str) -> None:
    ci_dir = repo / ".ger" / "ci"
    ci_dir.mkdir(parents=True)
    (ci_dir / "registry.py").write_text(body, encoding="utf-8")
    clear_ci_strategy_cache()


def test_resolve_ci_strategy_no_registry(stack_repo: Path) -> None:
    clear_ci_strategy_cache()
    assert resolve_ci_strategy(stack_repo, "testproj") is None


def test_resolve_ci_strategy_from_strategies_dict(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        """
from gerrit_workflow_tools.core.ci_links import CiLink

def _extract(*, project, checks, messages):
    return [CiLink(label="j", url="https://ci/console", source="checks")]

STRATEGIES = {"testproj": _extract}
""",
    )
    strat = resolve_ci_strategy(stack_repo, "testproj")
    assert strat is not None
    links = strat(project="testproj", checks=[], messages=[])
    assert links[0].url == "https://ci/console"
    assert resolve_ci_strategy(stack_repo, "other") is None


def test_resolve_ci_strategy_get_strategy(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        """
from gerrit_workflow_tools.core.ci_links import CiLink

def get_strategy(project):
    if project != "testproj":
        return None
    def extract(*, project, checks, messages):
        return [CiLink(label="x", url="https://x", source="message")]
    return extract
""",
    )
    strat = resolve_ci_strategy(stack_repo, "testproj")
    assert strat is not None
    assert strat(project="testproj", checks=[], messages=[])[0].source == "message"


def test_service_fetch_populates_ci_links(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        """
import re
from gerrit_workflow_tools.core.ci_links import CiLink

def _jenkins(*, project, checks, messages):
    out = []
    for row in checks:
        if row.get("state") != "FAILED":
            continue
        url = row.get("url") or ""
        if url and not url.endswith("/console"):
            url = url.rstrip("/") + "/console"
        if url:
            name = row.get("checker_name") or "jenkins"
            out.append(CiLink(label=str(name), url=url, source="checks"))
    if out:
        return out
    for msg in messages:
        m = re.search(r"(https://jenkins[^\\s]+/\\d+/)", str(msg.get("message") or ""))
        if m:
            out.append(CiLink(label="jenkins", url=m.group(1) + "console", source="message"))
    return out

STRATEGIES = {"testproj": _jenkins}
""",
    )
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows, per_index_overrides=[{"verified": -1}] * len(rows))
    first_cid = rows[0].change_id
    assert first_cid
    triplet = f"testproj~main~{first_cid}"
    store = ChangeStore(
        details,
        web_base="https://g.example",
        checks={
            triplet: [
                {
                    "state": "FAILED",
                    "checker_name": "verify",
                    "url": "https://jenkins.example/job/p/42/",
                }
            ]
        },
        messages={triplet: [{"message": "Build failed: https://jenkins.example/job/p/99/"}]},
    )
    service = GerritService.from_cwd(stack_repo, rest=store)
    inputs = [
        CommitStatusInput(
            sha=row.sha,
            short_sha=row.short_sha,
            summary=row.subject,
            change_id=row.change_id,
        )
        for row in rows
    ]
    result = service.fetch_gerrit_data(inputs, cwd=stack_repo)
    matched = next(c for c in result if c.change_id == first_cid)
    assert matched.ci_failures == ["verify"]
    assert matched.ci_links == [
        CiLink(label="verify", url="https://jenkins.example/job/p/42/console", source="checks")
    ]
    assert store.calls_to("get_messages")


def test_service_falls_back_to_messages(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        """
import re
from gerrit_workflow_tools.core.ci_links import CiLink

def _msg_only(*, project, checks, messages):
    out = []
    for msg in messages:
        m = re.search(r"(https://jenkins[^\\s]+/\\d+/)", str(msg.get("message") or ""))
        if m:
            out.append(CiLink(label="jenkins", url=m.group(1) + "console", source="message"))
    return out

STRATEGIES = {"testproj": _msg_only}
""",
    )
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows, per_index_overrides=[{"verified": -1}] * len(rows))
    first_cid = rows[0].change_id
    assert first_cid
    triplet = f"testproj~main~{first_cid}"
    store = ChangeStore(
        details,
        web_base="https://g.example",
        checks={triplet: []},
        messages={triplet: [{"message": "failed https://jenkins.example/job/p/7/"}]},
    )
    service = GerritService.from_cwd(stack_repo, rest=store)
    inputs = [
        CommitStatusInput(
            sha=r.sha,
            short_sha=r.short_sha,
            summary=r.subject,
            change_id=r.change_id,
        )
        for r in rows
    ]
    result = service.fetch_gerrit_data(inputs, cwd=stack_repo)
    matched = next(c for c in result if c.change_id == first_cid)
    assert matched.ci_links[0].url == "https://jenkins.example/job/p/7/console"
    assert matched.ci_links[0].source == "message"


def test_ci_pipelines_from_checks_overlays_strategy_urls() -> None:
    checks = [
        {"state": "FAILED", "checker_name": "build", "url": "https://ci/build"},
        {"state": "SUCCESSFUL", "checker_name": "lint"},
    ]
    links = [CiLink(label="build", url="https://ci/build/console", source="checks")]
    pipelines = ci_pipelines_from_checks(checks, links)
    assert pipelines == [
        CiPipeline(label="build", state="FAILED", url="https://ci/build/console"),
        CiPipeline(label="lint", state="SUCCESSFUL", url=None),
    ]


def test_format_ci_lines_hyperlink_single_line() -> None:
    init_hyperlink_mode(hyperlinks="always")
    commit = LogCommit(
        sha="a" * 40,
        short_sha="aaaaaaaa",
        summary="x",
        change_id=None,
        pushed=True,
        abandoned=False,
        patchset_status=PatchsetStatus.ACTIVE,
        verified=-1,
        code_review=None,
        comments_unresolved=0,
        ci_pipelines=[
            CiPipeline(label="build", state="FAILED", url="https://ci/build"),
            CiPipeline(label="lint", state="SUCCESSFUL", url="https://ci/lint"),
        ],
    )
    lines = format_ci_lines(commit)
    assert len(lines) == 1
    assert strip_ansi(lines[0]).startswith("CI:")
    assert "build" in strip_ansi(lines[0])
    assert "lint" in strip_ansi(lines[0])
    init_hyperlink_mode(hyperlinks="never")


def test_format_ci_lines_plain_multi_line() -> None:
    init_hyperlink_mode(hyperlinks="never")
    commit = LogCommit(
        sha="a" * 40,
        short_sha="aaaaaaaa",
        summary="x",
        change_id=None,
        pushed=True,
        abandoned=False,
        patchset_status=PatchsetStatus.ACTIVE,
        verified=-1,
        code_review=None,
        comments_unresolved=0,
        ci_pipelines=[
            CiPipeline(label="build", state="FAILED", url="https://ci/build"),
            CiPipeline(label="lint", state="SUCCESSFUL", url="https://ci/lint"),
        ],
    )
    lines = format_ci_lines(commit)
    assert len(lines) == 2
    assert "CI:" in strip_ansi(lines[0])
    assert "build https://ci/build" in strip_ansi(lines[0])
    assert "lint https://ci/lint" in strip_ansi(lines[1])


def test_extra_detail_lines_with_hyperlinks() -> None:
    init_hyperlink_mode(hyperlinks="always")
    commit = LogCommit(
        sha="a" * 40,
        short_sha="aaaaaaaa",
        summary="x",
        change_id=None,
        pushed=True,
        abandoned=False,
        patchset_status=PatchsetStatus.ACTIVE,
        verified=-1,
        code_review=None,
        comments_unresolved=0,
        ci_failures=["verify"],
        ci_links=[CiLink(label="verify", url="https://ci/console", source="checks")],
    )
    lines = extra_detail_lines(commit)
    assert len(lines) == 1
    plain = strip_ansi(lines[0])
    assert plain.startswith("CI:")
    assert "verify" in plain
    assert "\x1b]8;;https://ci/console" in lines[0]
    init_hyperlink_mode(hyperlinks="never")
    lines_off = extra_detail_lines(commit)
    assert "https://ci/console" in strip_ansi(lines_off[0])


def test_log_json_includes_ci_links(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    _write_registry(
        stack_repo,
        """
from gerrit_workflow_tools.core.ci_links import CiLink

def _extract(*, project, checks, messages):
    return [CiLink(label="build", url="https://ci/1/console", source="checks")]

STRATEGIES = {"testproj": _extract}
""",
    )
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows, per_index_overrides=[{"verified": -1}] * len(rows))
    first_cid = rows[0].change_id
    assert first_cid
    store = ChangeStore(
        details,
        checks={f"testproj~main~{first_cid}": [{"state": "FAILED", "checker_name": "build"}]},
    )
    code, out, err = run_cli(
        stack_repo, log_main, ["--json", "--color=never"], monkeypatch, gerrit=store
    )
    assert code in (0, 1), err
    data = json.loads(out)
    linked = [c for c in data["commits"] if c.get("ci_links")]
    assert linked
    assert linked[0]["ci_links"][0]["url"] == "https://ci/1/console"
