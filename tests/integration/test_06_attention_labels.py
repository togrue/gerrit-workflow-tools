"""Integration: ``ger log`` trailing attention labels against a live Gerrit chain."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.gerrit_seed import post_review_labels
from tests.integration.integration_helpers import (
    ChainCommit,
    abandon_change,
    add_change_reviewer,
    chain_commits_oldest_first,
    clear_change_reviewers,
    open_changes_on_branch,
    parse_trailing_attention_labels,
    post_unresolved_inline_comment,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain


def _commit_msg(subject: str, gerrit_state: str, expected_label: str) -> str:
    return f"{subject}\n\nGerrit state: {gerrit_state}\nExpected label: {expected_label}\n"


def _scenario_commit_msg(scenario: AttentionScenario) -> str:
    state_docs = {
        "attn: build-failed+no-reviewers": (
            "owner Verified=-1 (no REST reviewers); owner vote counts as reviewer in Gerrit"
        ),
    }
    gerrit_state = state_docs.get(
        scenario.subject_tag,
        f"see setup for {scenario.subject_tag}",
    )
    expected = ", ".join(scenario.expected_labels) if scenario.expected_labels else "(none)"
    return _commit_msg(scenario.subject_tag, gerrit_state, expected)


@dataclass(frozen=True)
class AttentionScenario:
    subject_tag: str
    expected_labels: tuple[str, ...]
    expected_reasons: frozenset[str]
    forbidden_reasons: frozenset[str]
    setup: Callable[[GerritHttpSession, ChainCommit, str], None]


def _setup_submittable(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_review_labels(session, commit.change_id, code_review=2, verified=1)


def _setup_no_label(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_review_labels(session, commit.change_id, code_review=1, verified=0)


def _setup_no_reviewers(session: GerritHttpSession, commit: ChainCommit, _reviewer: str) -> None:
    return


def _setup_build_failed(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_review_labels(session, commit.change_id, verified=-1)


def _setup_unresolved(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_unresolved_inline_comment(
        session,
        commit.change_id,
        f"chain_{commit.chain_index}.txt",
        1,
        "please address",
    )


def _setup_abandoned(session: GerritHttpSession, commit: ChainCommit, _reviewer: str) -> None:
    abandon_change(session, commit.change_id)


def _setup_build_failed_unresolved(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_review_labels(session, commit.change_id, verified=-1)
    post_unresolved_inline_comment(
        session,
        commit.change_id,
        f"chain_{commit.chain_index}.txt",
        1,
        "please address",
    )


def _setup_build_failed_no_reviewers(dev_session: GerritHttpSession, commit: ChainCommit, _reviewer: str) -> None:
    # Owner vote for Verified-1 without REST reviewer assignment (owner is not a reviewer).
    post_review_labels(dev_session, commit.change_id, verified=-1)


def _setup_unresolved_no_reviewers(session: GerritHttpSession, commit: ChainCommit, _reviewer: str) -> None:
    post_unresolved_inline_comment(
        session,
        commit.change_id,
        f"chain_{commit.chain_index}.txt",
        1,
        "please address",
    )
    clear_change_reviewers(session, commit.change_id)


def _setup_rest_reviewers(session: GerritHttpSession, commit: ChainCommit, reviewer: str) -> None:
    add_change_reviewer(session, commit.change_id, reviewer)
    post_review_labels(session, commit.change_id, code_review=2, verified=1)


SCENARIOS: tuple[AttentionScenario, ...] = (
    AttentionScenario(
        subject_tag="attn: submittable",
        expected_labels=("submittable",),
        expected_reasons=frozenset(),
        forbidden_reasons=frozenset({"no-reviewers", "ci-failed", "unresolved-comments", "abandoned"}),
        setup=_setup_submittable,
    ),
    AttentionScenario(
        subject_tag="attn: rest-reviewers",
        expected_labels=("submittable",),
        expected_reasons=frozenset(),
        forbidden_reasons=frozenset({"no-reviewers", "ci-failed", "unresolved-comments", "abandoned"}),
        setup=_setup_rest_reviewers,
    ),
    AttentionScenario(
        subject_tag="attn: no-reviewers",
        expected_labels=("no reviewers",),
        expected_reasons=frozenset({"no-reviewers"}),
        forbidden_reasons=frozenset({"ci-failed", "unresolved-comments", "abandoned"}),
        setup=_setup_no_reviewers,
    ),
    AttentionScenario(
        subject_tag="attn: build-failed",
        expected_labels=("build failed",),
        expected_reasons=frozenset({"ci-failed"}),
        forbidden_reasons=frozenset({"no-reviewers", "unresolved-comments", "abandoned"}),
        setup=_setup_build_failed,
    ),
    AttentionScenario(
        subject_tag="attn: unresolved",
        expected_labels=("1 unresolved comment",),
        expected_reasons=frozenset({"unresolved-comments"}),
        forbidden_reasons=frozenset({"no-reviewers", "ci-failed", "abandoned"}),
        setup=_setup_unresolved,
    ),
    AttentionScenario(
        subject_tag="attn: build-failed+unresolved",
        expected_labels=("build failed", "1 unresolved comment"),
        expected_reasons=frozenset({"ci-failed", "unresolved-comments"}),
        forbidden_reasons=frozenset({"no-reviewers", "abandoned"}),
        setup=_setup_build_failed_unresolved,
    ),
    AttentionScenario(
        subject_tag="attn: build-failed+no-reviewers",
        expected_labels=("build failed",),
        expected_reasons=frozenset({"ci-failed"}),
        forbidden_reasons=frozenset({"no-reviewers", "unresolved-comments", "abandoned"}),
        setup=_setup_build_failed_no_reviewers,
    ),
    AttentionScenario(
        subject_tag="attn: unresolved+no-reviewers",
        expected_labels=("1 unresolved comment", "no reviewers"),
        expected_reasons=frozenset({"unresolved-comments", "no-reviewers"}),
        forbidden_reasons=frozenset({"ci-failed", "abandoned"}),
        setup=_setup_unresolved_no_reviewers,
    ),
    AttentionScenario(
        subject_tag="attn: abandoned",
        expected_labels=("abandoned",),
        expected_reasons=frozenset({"abandoned"}),
        forbidden_reasons=frozenset({"no-reviewers", "ci-failed", "unresolved-comments"}),
        setup=_setup_abandoned,
    ),
    AttentionScenario(
        subject_tag="attn: no-label",
        expected_labels=(),
        expected_reasons=frozenset({"awaiting-review"}),
        forbidden_reasons=frozenset({"no-reviewers", "ci-failed", "unresolved-comments", "abandoned"}),
        setup=_setup_no_label,
    ),
)


def _json_by_subject_tag(out: str) -> dict[str, dict]:
    data = json.loads(out)
    assert isinstance(data, list)
    out_map: dict[str, dict] = {}
    for item in data:
        assert isinstance(item, dict)
        summary = str(item.get("summary", "")).strip()
        for scenario in sorted(SCENARIOS, key=lambda s: len(s.subject_tag), reverse=True):
            if summary == scenario.subject_tag:
                out_map[scenario.subject_tag] = item
                break
    return out_map


def _chain_blocked_from_index(first_blocking_index: int | None, chain_index: int) -> bool:
    return first_blocking_index is not None and chain_index > first_blocking_index


def _first_blocking_index(rows: list[dict]) -> int | None:
    """Index of the first commit that blocks followers (not submittable while still open)."""
    for i, row in enumerate(rows):
        if row.get("abandoned"):
            continue
        if row.get("patchset_status") in ("absent", "merged-same"):
            continue
        if row.get("submittable") is True:
            continue
        return i
    return None


def test_attention_labels_on_pushed_chain(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    gerrit_dev_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    reviewer = ctx.admin_user
    topic = f"attn_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)

    messages = [_scenario_commit_msg(scenario) for scenario in SCENARIOS]
    build_linear_chain(repo, messages)

    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--all", "--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    proj = ctx.project_verified
    open_rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(open_rows) == len(SCENARIOS)

    chain = chain_commits_oldest_first(repo)
    assert len(chain) == len(SCENARIOS)
    for commit, scenario in zip(chain, SCENARIOS, strict=True):
        assert scenario.subject_tag in commit.subject
        actor = gerrit_dev_session if scenario.setup is _setup_build_failed_no_reviewers else gerrit_admin_session
        scenario.setup(actor, commit, reviewer)

    code_log, out_log, elog = run_cli(
        repo,
        ger_log_main,
        ["--color", "never"],
        monkeypatch,
    )
    assert code_log == 1, elog

    code_json, out_json, ejson = run_cli(
        repo,
        ger_log_main,
        ["--json"],
        monkeypatch,
    )
    assert code_json == 1, ejson
    json_rows = _json_by_subject_tag(out_json)
    json_list = json.loads(out_json)
    first_block = _first_blocking_index(json_list)

    for scenario in SCENARIOS:
        labels = parse_trailing_attention_labels(out_log, scenario.subject_tag)
        assert labels is not None, f"missing log line for {scenario.subject_tag!r}:\n{out_log}"
        assert tuple(labels) == scenario.expected_labels, (
            f"{scenario.subject_tag}: expected labels {scenario.expected_labels!r}, got {labels!r}\n{out_log}"
        )

        row = json_rows.get(scenario.subject_tag)
        assert row is not None, f"missing JSON row for {scenario.subject_tag!r}"
        reasons = set(row.get("attention_reasons") or [])
        assert scenario.expected_reasons <= reasons, (
            f"{scenario.subject_tag}: expected reasons {scenario.expected_reasons!r}, got {reasons!r}"
        )
        overlap = reasons & scenario.forbidden_reasons
        assert not overlap, f"{scenario.subject_tag}: unexpected reasons {overlap!r}"

        chain_index = next(i for i, c in enumerate(chain) if scenario.subject_tag in c.subject)
        if _chain_blocked_from_index(first_block, chain_index) and "abandoned" not in reasons:
            assert "chain-blocked" in reasons, f"{scenario.subject_tag}: expected chain-blocked in {reasons!r}"
