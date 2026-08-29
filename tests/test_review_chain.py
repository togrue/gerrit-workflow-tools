# Spec: docu/spec/commands/inbox.md — review-chain assembly and ages

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gerrit_workflow_tools.core.review_chain import (
    assemble_review_chains,
    format_age,
    member_unreviewed_since,
    missing_parent_shas,
    parse_gerrit_time,
)


SELF = 1000
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
WEB = "https://gerrit.example.com"


def _change(
    *,
    number: int,
    sha: str,
    parent: str | None,
    subject: str,
    owner: str = "alice",
    updated: str,
    created: str | None = None,
    cr: int = 0,
    verified: int = 1,
    comments: int = 0,
    attention_since: str | None = None,
    my_vote_date: str | None = None,
    status: str = "NEW",
) -> dict[str, Any]:
    change_id = "I" + f"{number:040x}"[-40:]
    stamp = created or updated
    labels: dict[str, Any] = {
        "Verified": {"value": verified, "all": [{"value": verified}]},
        "Code-Review": {"value": cr, "all": [{"value": cr, "_account_id": 1}]},
    }
    if my_vote_date is not None:
        labels["Code-Review"]["all"].append(
            {"_account_id": SELF, "value": 1, "date": my_vote_date},
        )
    payload: dict[str, Any] = {
        "id": f"myproject~main~{change_id}",
        "change_id": change_id,
        "project": "myproject",
        "branch": "main",
        "_number": number,
        "status": status,
        "subject": subject,
        "owner": {"name": owner, "email": f"{owner}@example.com", "_account_id": 2},
        "current_revision": sha,
        "updated": updated,
        "created": stamp,
        "unresolved_comment_count": comments,
        "revisions": {
            sha: {
                "_number": 1,
                "created": stamp,
                "commit": {"parents": [{"commit": parent}] if parent else [], "subject": subject},
            }
        },
        "labels": labels,
    }
    if attention_since is not None:
        payload["attention_set"] = {
            str(SELF): {
                "account": {"_account_id": SELF, "name": "me"},
                "last_update": attention_since,
            }
        }
    return payload


def test_parse_gerrit_time_strips_nanos_and_assumes_utc() -> None:
    stamp = parse_gerrit_time("2026-08-15 12:00:00.269000000")
    assert stamp == datetime(2026, 8, 15, 12, 0, 0, 269000, tzinfo=timezone.utc)


def test_format_age_uses_compact_units() -> None:
    assert format_age(45) == "0m"
    assert format_age(12 * 60) == "12m"
    assert format_age(4 * 3600) == "4h"
    assert format_age(3 * 86400) == "3d"


def test_linear_chain_links_on_first_parent_and_picks_the_tip() -> None:
    base = _change(
        number=4317,
        sha="aaa",
        parent="origin",
        subject="feat: config plumbing",
        updated="2026-08-17 12:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    mid = _change(
        number=4318,
        sha="bbb",
        parent="aaa",
        subject="feat: limiter core",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-16 12:00:00.000000000",
    )
    top = _change(
        number=4321,
        sha="ccc",
        parent="bbb",
        subject="feat: rate limiter",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    chains = assemble_review_chains([base, mid, top], [], web_base=WEB, now=NOW, self_account_id=SELF)
    assert len(chains) == 1
    chain = chains[0]
    assert chain.top.number == 4321
    assert chain.depth == 3
    assert [member.number for member in chain.members] == [4317, 4318, 4321]
    assert chain.url == "https://gerrit.example.com/c/myproject/+/4321"
    assert chain.unreviewed_age_seconds == 3 * 86400
    assert chain.wait_age_seconds == 4 * 3600
    assert chain.last_activity == datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def test_missing_parent_shas_are_the_ones_not_in_the_result() -> None:
    child = _change(
        number=2,
        sha="bbb",
        parent="aaa",
        subject="child",
        updated="2026-08-18 08:00:00.000000000",
    )
    assert missing_parent_shas([child]) == ["aaa"]
    parent = _change(
        number=1,
        sha="aaa",
        parent="origin",
        subject="parent",
        updated="2026-08-18 08:00:00.000000000",
    )
    assert missing_parent_shas([parent, child]) == ["origin"]


def test_follow_up_parent_joins_the_queried_members() -> None:
    parent = _change(
        number=1,
        sha="aaa",
        parent="origin",
        subject="base",
        owner="bob",
        updated="2026-08-10 12:00:00.000000000",
        attention_since="2026-08-12 12:00:00.000000000",
    )
    child = _change(
        number=2,
        sha="bbb",
        parent="aaa",
        subject="tip",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    chains = assemble_review_chains([child], [parent], web_base=WEB, now=NOW, self_account_id=SELF)
    assert len(chains) == 1
    assert chains[0].depth == 2
    assert chains[0].top.number == 2
    assert [member.number for member in chains[0].members] == [1, 2]


def test_merged_follow_up_parent_is_ground_not_a_member() -> None:
    merged = _change(
        number=1,
        sha="aaa",
        parent="origin",
        subject="landed",
        updated="2026-08-01 12:00:00.000000000",
        status="MERGED",
    )
    child = _change(
        number=2,
        sha="bbb",
        parent="aaa",
        subject="tip",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    chains = assemble_review_chains([child], [merged], web_base=WEB, now=NOW, self_account_id=SELF)
    assert len(chains) == 1
    assert chains[0].depth == 1
    assert chains[0].partial_chain is False


def test_unmatched_parent_sha_flags_partial_chain() -> None:
    child = _change(
        number=2,
        sha="bbb",
        parent="aaa",
        subject="tip",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    chains = assemble_review_chains(
        [child],
        [],
        web_base=WEB,
        now=NOW,
        self_account_id=SELF,
        follow_up_unmatched={"aaa"},
    )
    assert chains[0].partial_chain is True


def test_sorts_longest_unreviewed_first() -> None:
    stale = _change(
        number=100,
        sha="old",
        parent="origin",
        subject="stale",
        owner="carol",
        updated="2026-08-18 10:00:00.000000000",
        attention_since="2026-08-12 12:00:00.000000000",
    )
    fresh = _change(
        number=200,
        sha="new",
        parent="origin",
        subject="fresh",
        owner="bob",
        updated="2026-08-10 12:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    chains = assemble_review_chains([fresh, stale], [], web_base=WEB, now=NOW, self_account_id=SELF)
    assert [chain.top.number for chain in chains] == [100, 200]
    assert chains[0].unreviewed_age_seconds == 6 * 86400
    assert chains[1].unreviewed_age_seconds == 86400


def test_unreviewed_falls_back_to_current_patchset_after_old_vote() -> None:
    payload = _change(
        number=3,
        sha="ccc",
        parent="origin",
        subject="new ps",
        updated="2026-08-18 08:00:00.000000000",
        created="2026-08-16 12:00:00.000000000",
        my_vote_date="2026-08-10 12:00:00.000000000",
    )
    stamp = member_unreviewed_since(payload, SELF)
    assert stamp == datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def test_current_vote_means_not_waiting_on_me() -> None:
    payload = _change(
        number=3,
        sha="ccc",
        parent="origin",
        subject="reviewed",
        updated="2026-08-18 08:00:00.000000000",
        created="2026-08-10 12:00:00.000000000",
        my_vote_date="2026-08-18 07:00:00.000000000",
    )
    assert member_unreviewed_since(payload, SELF) is None
    chains = assemble_review_chains([payload], [], web_base=WEB, now=NOW, self_account_id=SELF)
    assert chains[0].unreviewed_age_seconds == 0
    assert chains[0].wait_age_seconds == 4 * 3600


def test_ci_failure_is_an_attention_reason() -> None:
    payload = _change(
        number=9,
        sha="fff",
        parent="origin",
        subject="red",
        updated="2026-08-18 08:00:00.000000000",
        verified=-1,
        comments=2,
        attention_since="2026-08-17 12:00:00.000000000",
    )
    chains = assemble_review_chains([payload], [], web_base=WEB, now=NOW, self_account_id=SELF)
    assert "ci-failed" in chains[0].attention_reasons
    assert "unresolved-comments" in chains[0].attention_reasons
    assert chains[0].comments_unresolved == 2
