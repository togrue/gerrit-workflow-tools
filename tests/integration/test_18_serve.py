"""Integration: ``ger serve`` endpoints against the real Gerrit, for one pushed stack.

The API is called in-process through ``GerApi.handle`` (the HTTP transport has unit tests of
its own). ``/stack`` must be ``ger log --json`` plus the fields a UI needs, and ``/overview``
must find the stack as one *outgoing* chain from Gerrit's own relation data, with each member
pointing at its local commit.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.server.api import GerApi
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.integration_helpers import chain_commits_oldest_first, ger_json, ger_push, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def test_stack_and_overview_agree_with_ger_log(
    tmp_path: Path, gerrit_integration_context: GerritIntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, f"serve_{secrets.token_hex(4)}")
    build_linear_chain(repo, ["serve base", "serve middle", "serve top"])
    ger_push(repo, monkeypatch)
    shas = [c.sha for c in chain_commits_oldest_first(repo)]

    log = ger_json(repo, ger_log_main, [], monkeypatch)
    api = GerApi(repo)
    stack = api.handle("GET", "/api/v1/stack")
    assert stack["stack"] == log["stack"]
    assert [c["sha"] for c in stack["commits"]] == shas
    for logged, served in zip(log["commits"], stack["commits"], strict=True):
        assert {key: served[key] for key in logged} == logged
    assert stack["summary"]["commits"] == 3 and stack["summary"]["not_on_gerrit"] == 0

    overview = api.handle("GET", "/api/v1/overview")
    assert overview["local"] == stack
    outgoing = next(section for section in overview["sections"] if section["name"] == "outgoing")
    numbers = [c["number"] for c in log["commits"]]
    [chain] = [chain for chain in outgoing["chains"] if numbers[-1] in [m["number"] for m in chain["members"]]]
    assert [m["number"] for m in chain["members"]] == numbers
    assert [m["local_sha"] for m in chain["members"]] == shas and chain["local"] is True
