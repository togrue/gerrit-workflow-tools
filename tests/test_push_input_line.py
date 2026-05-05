from __future__ import annotations

import pytest

from gerrit_workflow_tools.push_input_line import (
    Diagnostic,
    PushLineState,
    Span,
    format_canonical,
    parse,
    refspec_options,
    tokenize,
)


def _state(buf: str) -> PushLineState:
    return parse(buf).state


def _diag_severities(buf: str) -> list[str]:
    return [d.severity for d in parse(buf).diagnostics]


# ---------- examples A: r alice,bob wip variants ----------


@pytest.mark.parametrize(
    "buf",
    [
        "r alice,bob wip",
        "r=alice,bob wip",
        "alice bob wip",
        "wip alice bob",
        "alice wip bob",
    ],
)
def test_example_a_variants_all_yield_alice_bob_wip(buf: str) -> None:
    s = _state(buf)
    assert s.reviewers == ["alice", "bob"]
    assert s.wip is True
    assert s.private is False
    assert s.topic is None
    assert parse(buf).valid_for_apply


# ---------- examples B: bob topic=abc alice variants ----------


@pytest.mark.parametrize(
    "buf",
    [
        "bob topic=abc alice",
        "bob topic abc alice",
        'bob topic "abc" alice',
        "topic=abc alice bob",
    ],
)
def test_example_b_variants_all_yield_alice_bob_topic_abc(buf: str) -> None:
    s = _state(buf)
    assert s.reviewers == ["bob", "alice"] or s.reviewers == ["alice", "bob"]
    assert s.topic == "abc"


def test_topic_quoted_with_spaces_kept_as_value() -> None:
    s = _state('alice topic "my topic" wip')
    assert s.topic == "my topic"
    assert s.reviewers == ["alice"]
    assert s.wip is True


# ---------- removal ----------


def test_minus_reviewer_removes_from_list() -> None:
    s = _state("alice bob -alice")
    assert s.reviewers == ["bob"]


def test_minus_wip_clears_flag() -> None:
    s = _state("wip -wip")
    assert s.wip is False


def test_minus_topic_clears_topic() -> None:
    s = _state("topic=abc -topic")
    assert s.topic is None


def test_minus_r_clears_reviewers() -> None:
    s = _state("alice bob -r")
    assert s.reviewers == []


def test_minus_then_re_add() -> None:
    s = _state("alice -alice alice")
    assert s.reviewers == ["alice"]


# ---------- collisions / keyword precedence ----------


def test_keyword_wip_not_treated_as_reviewer_name() -> None:
    s = _state("wip")
    assert s.wip is True
    assert s.reviewers == []


def test_case_insensitive_keywords() -> None:
    s = _state("WIP Private TOPIC=x")
    assert s.wip is True
    assert s.private is True
    assert s.topic == "x"


def test_dedup_reviewers_preserves_order() -> None:
    s = _state("alice bob alice carol bob")
    assert s.reviewers == ["alice", "bob", "carol"]


# ---------- diagnostics ----------


def test_unclosed_quote_errors() -> None:
    res = parse('topic "abc')
    assert any(d.severity == "error" and "Unclosed" in d.message for d in res.diagnostics)
    assert not res.valid_for_apply


def test_empty_r_equals_errors() -> None:
    sevs = _diag_severities("r=")
    assert "error" in sevs


def test_topic_without_value_errors() -> None:
    sevs = _diag_severities("topic")
    assert "error" in sevs


def test_topic_followed_by_keyword_errors() -> None:
    sevs = _diag_severities("topic wip")
    assert "error" in sevs


def test_unknown_key_errors() -> None:
    sevs = _diag_severities("foo=bar")
    assert "error" in sevs


def test_topic_redefined_warns() -> None:
    res = parse("topic=a topic=b")
    assert res.state.topic == "b"
    assert any(d.severity == "warning" for d in res.diagnostics)


# ---------- canonical format ----------


@pytest.mark.parametrize(
    "buf,expected",
    [
        ("r alice,bob wip", "r=alice,bob wip"),
        ("r=alice,bob wip", "r=alice,bob wip"),
        ("alice bob wip", "r=alice,bob wip"),
        ("wip alice bob", "r=alice,bob wip"),
        ("bob topic=abc alice", "r=bob,alice topic=abc"),
        ('bob topic "abc" alice', "r=bob,alice topic=abc"),
        ("alice private wip", "r=alice wip private"),
    ],
)
def test_canonical_examples(buf: str, expected: str) -> None:
    assert format_canonical(_state(buf)) == expected


def test_canonical_quotes_topic_with_space() -> None:
    s = PushLineState(topic="a b")
    assert format_canonical(s) == "topic='a b'"


def test_canonical_empty_state_is_empty_string() -> None:
    assert format_canonical(PushLineState()) == ""


def test_canonical_round_trip_through_parser() -> None:
    s1 = _state("alice bob topic=t1 wip private")
    can = format_canonical(s1)
    s2 = _state(can)
    assert s2 == s1


# ---------- refspec options ----------


def test_refspec_options_push_strategy_emits_reviewers_and_flags() -> None:
    s = _state("alice bob wip private topic=t1")
    assert refspec_options(s, "push") == ["r=alice", "r=bob", "wip", "private", "topic=t1"]


def test_refspec_options_lazy_strategy_omits_reviewers() -> None:
    s = _state("alice bob wip topic=t1")
    assert refspec_options(s, "lazy") == ["wip", "topic=t1"]


def test_refspec_options_overwrite_strategy_omits_reviewers() -> None:
    s = _state("alice private")
    assert refspec_options(s, "overwrite") == ["private"]


def test_refspec_options_empty_state_returns_empty_list() -> None:
    assert refspec_options(PushLineState(), "push") == []


# ---------- tokenizer offsets and spans ----------


def test_tokenizer_offsets_match_substring() -> None:
    buf = "  alice bob  wip"
    tokens, _ = tokenize(buf)
    for tok in tokens:
        assert buf[tok.start : tok.end] == tok.text or tok.quoted


def test_spans_have_increasing_offsets() -> None:
    res = parse("alice bob topic=abc wip")
    for a, b in zip(res.spans, res.spans[1:], strict=False):
        assert a.start <= b.start


def test_minus_span_records_minus_and_target_separately() -> None:
    res = parse("-alice")
    kinds = [s.kind for s in res.spans]
    assert "minus" in kinds
    assert "reviewer" in kinds


def test_keyword_spans_are_classified() -> None:
    res = parse("wip private topic=abc r=alice")
    kinds = {s.kind for s in res.spans}
    expected = {
        "keyword_wip",
        "keyword_private",
        "keyword_topic",
        "keyword_r",
        "topic_value",
        "reviewer",
        "equals",
    }
    assert expected.issubset(kinds)


def test_diagnostic_dataclass_is_hashable_via_fields() -> None:
    d = Diagnostic("error", "x", 0, 1)
    assert d.severity == "error"
    assert (d.start, d.end) == (0, 1)


def test_span_dataclass_is_hashable_via_fields() -> None:
    s = Span("reviewer", 0, 5)
    assert (s.start, s.end, s.kind) == (0, 5, "reviewer")
