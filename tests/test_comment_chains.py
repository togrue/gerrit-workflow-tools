# Spec: comment chain grouping in gerrit_change_status

from __future__ import annotations

from gerrit_workflow_tools.core.comment_chains import (
    build_comment_chains,
    collect_comment_chains,
    collect_unresolved_comment_chains,
    count_unresolved_in_file_map,
)


def _comment(
    cid: str,
    *,
    message: str = "msg",
    unresolved: bool = True,
    in_reply_to: str | None = None,
    updated: str = "",
) -> dict:
    out: dict = {
        "id": cid,
        "message": message,
        "unresolved": unresolved,
        "updated": updated,
    }
    if in_reply_to is not None:
        out["in_reply_to"] = in_reply_to
    return out


def test_build_comment_chains_groups_replies() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=True),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=False,
                message="fixed",
            ),
        ]
    }
    chains = build_comment_chains(file_map)
    assert len(chains) == 1
    chain = chains[0]
    assert chain.root_id == "root"
    assert chain.resolved is True
    assert [c.comment_id for c in chain.comments] == ["root", "reply"]
    assert chain.comments[1].message == "fixed"


def test_collect_unresolved_skips_resolved_chain_tail() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=True),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=False,
            ),
        ]
    }
    assert collect_unresolved_comment_chains(file_map) == []
    assert count_unresolved_in_file_map(file_map) == 0


def test_collect_unresolved_includes_open_chain() -> None:
    file_map = {
        "a.py": [
            _comment("root", updated="2024-01-01 10:00:00", unresolved=False),
            _comment(
                "reply",
                in_reply_to="root",
                updated="2024-01-01 11:00:00",
                unresolved=True,
                message="still open",
            ),
        ]
    }
    chains = collect_unresolved_comment_chains(file_map)
    assert len(chains) == 1
    assert chains[0].resolved is False
    assert count_unresolved_in_file_map(file_map) == 1


def test_multiple_independent_chains() -> None:
    file_map = {
        "a.py": [
            _comment("c1", updated="1", unresolved=True),
            _comment("c2", updated="2", unresolved=False),
        ]
    }
    file_map["a.py"][0]["line"] = 1
    file_map["a.py"][1]["line"] = 5
    chains = build_comment_chains(file_map)
    assert len(chains) == 2
    assert count_unresolved_in_file_map(file_map) == 1


def test_orphan_reply_becomes_own_chain() -> None:
    file_map = {
        "a.py": [
            _comment(
                "orphan",
                in_reply_to="missing-parent",
                updated="1",
                unresolved=True,
            ),
        ]
    }
    chains = build_comment_chains(file_map)
    assert len(chains) == 1
    assert chains[0].root_id == "orphan"
    assert chains[0].resolved is False


def test_collect_comment_chains_all_puts_unresolved_first() -> None:
    file_map = {
        "a.py": [
            _comment("open", updated="1", unresolved=True),
            _comment("closed", updated="2", unresolved=False),
        ]
    }
    file_map["a.py"][0]["line"] = 10
    file_map["a.py"][1]["line"] = 1
    all_chains = collect_comment_chains(file_map, comments="all")
    assert [chain.root_id for chain in all_chains] == ["open", "closed"]
    assert [chain.resolved for chain in all_chains] == [False, True]
    resolved = collect_comment_chains(file_map, comments="resolved")
    assert [chain.root_id for chain in resolved] == ["closed"]
    assert collect_comment_chains(file_map, comments="unresolved") == collect_unresolved_comment_chains(file_map)


def test_build_comment_chains_keeps_context_lines() -> None:
    file_map = {
        "a.py": [
            {
                "id": "root",
                "message": "nit",
                "unresolved": True,
                "updated": "1",
                "line": 2,
                "context_lines": [
                    {"line_number": 1, "context_line": "alpha()"},
                    {"line_number": 2, "context_line": "beta()"},
                ],
            }
        ]
    }
    chain = build_comment_chains(file_map)[0]
    assert [(line.line_number, line.text) for line in chain.comments[0].context_lines] == [
        (1, "alpha()"),
        (2, "beta()"),
    ]
