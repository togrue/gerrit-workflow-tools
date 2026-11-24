"""Pure parsing/formatting for the interactive Gerrit push options line.

The interactive ``ger push`` reviewer prompt accepts a single line that mixes
reviewers, a topic, flags, and reviewer-assignment strategy keywords. This module turns the
raw buffer into a structured :class:`PushLineState` with diagnostics and
classified spans for highlighting, and renders the canonical form back out.

Grammar (best effort, case-insensitive keywords)::

    line     := element (WS+ element)*
    element  := removal | rclause | topic | flag | strategy | reviewer
    removal  := '-' (keyword | reviewer)
    rclause  := ('r' '=' list) | ('r' WS+ list)        # list = name (',' name)*
    topic    := ('topic' '=' value) | ('topic' WS+ value)
    flag     := 'wip' | 'private' | 'publish-drafts'
    strategy := 'push' | 'lazy' | 'overwrite'
    reviewer := bare-word that is not a reserved keyword

Quoted strings (``"..."`` / ``'...'``) are accepted as topic values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


ReviewerStrategy = Literal["push", "lazy", "overwrite"]

KW_R = "r"
KW_TOPIC = "topic"
KW_WIP = "wip"
KW_PRIVATE = "private"
KW_PUSH = "push"
KW_LAZY = "lazy"
KW_OVERWRITE = "overwrite"
KW_PUBLISH_DRAFTS = "publish-drafts"

SpanKind = Literal[
    "whitespace",
    "minus",
    "keyword_r",
    "keyword_topic",
    "keyword_wip",
    "keyword_private",
    "keyword_push",
    "keyword_lazy",
    "keyword_overwrite",
    "keyword_publish_drafts",
    "equals",
    "comma",
    "reviewer",
    "topic_value",
    "quoted",
    "error",
    "unknown",
]

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Token:
    """One whitespace-separated segment of the input line."""

    text: str
    start: int
    end: int
    quoted: bool = False


@dataclass(frozen=True)
class Span:
    """Classified character range used by the lexer for highlighting."""

    kind: SpanKind
    start: int
    end: int


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    start: int
    end: int


@dataclass
class PushLineState:
    """Effective push-options state derived from a parsed line."""

    reviewers: list[str] = field(default_factory=list)
    topic: str | None = None
    wip: bool = False
    private: bool = False
    strategy: ReviewerStrategy = "push"
    publish_drafts: bool = False
    """Publish your drafts on the updated changes with the push (``publish-drafts``)."""

    def add_reviewer(self, name: str) -> None:
        """Append ``name`` if non-empty and not already present."""
        if name and name not in self.reviewers:
            self.reviewers.append(name)

    def remove_reviewer(self, name: str) -> None:
        """Remove ``name`` if present (no-op otherwise)."""
        if name in self.reviewers:
            self.reviewers.remove(name)


@dataclass
class ParseResult:
    """Output of :func:`parse`: state plus diagnostics and classified spans."""

    state: PushLineState
    diagnostics: list[Diagnostic]
    spans: list[Span]

    @property
    def valid_for_apply(self) -> bool:
        """``True`` when no diagnostic has severity ``error``."""
        return not any(d.severity == "error" for d in self.diagnostics)


def tokenize(buf: str) -> tuple[list[Token], list[Diagnostic]]:
    """Split ``buf`` into whitespace-separated segments, honoring ``"`` / ``'`` quotes.

    Inside quotes, whitespace is preserved as part of the segment text and the
    surrounding quote characters are stripped. An unclosed quote yields an
    error diagnostic; the partial segment is still emitted so the rest of the
    line can be parsed.
    """
    tokens: list[Token] = []
    diags: list[Diagnostic] = []
    for match in _SEGMENT.finditer(buf):
        text, start, end = match.group(), match.start(), match.end()
        if text[0] not in ('"', "'"):
            tokens.append(Token(text, start, end))
            continue
        closed = len(text) > 1 and text[-1] == text[0]
        tokens.append(Token(text[1 : -1 if closed else None], start, end, quoted=True))
        if not closed:
            diags.append(Diagnostic("error", f"Unclosed {text[0]} quote", start, end))
    return tokens, diags


_SEGMENT = re.compile(r""""[^"]*"?|'[^']*'?|[^\s"']+""")
"""A quoted run up to its closing quote (or the end of the line), or a run of anything but space and quotes."""


def _split_reviewers(raw: str, base_offset: int, spans: list[Span]) -> list[str]:
    """Split a comma-separated reviewer RHS, recording comma + reviewer spans."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        j = i
        while j < n and raw[j] != ",":
            j += 1
        name = raw[i:j].strip()
        if name:
            name_start = base_offset + i + (len(raw[i:j]) - len(raw[i:j].lstrip()))
            spans.append(Span("reviewer", name_start, name_start + len(name)))
            out.append(name)
        if j < n:
            spans.append(Span("comma", base_offset + j, base_offset + j + 1))
        i = j + 1
    return out


_KEYWORD_KINDS: dict[str, SpanKind] = {
    KW_R: "keyword_r",
    KW_TOPIC: "keyword_topic",
    KW_WIP: "keyword_wip",
    KW_PRIVATE: "keyword_private",
    KW_PUSH: "keyword_push",
    KW_LAZY: "keyword_lazy",
    KW_OVERWRITE: "keyword_overwrite",
    KW_PUBLISH_DRAFTS: "keyword_publish_drafts",
}
_FLAG_FIELDS: dict[SpanKind, str] = {
    "keyword_wip": "wip",
    "keyword_private": "private",
    "keyword_publish_drafts": "publish_drafts",
}
"""The keywords that switch a :class:`PushLineState` flag on (and, after ``-``, off), by the field they set."""
_STRATEGIES: dict[SpanKind, ReviewerStrategy] = {
    "keyword_push": "push",
    "keyword_lazy": "lazy",
    "keyword_overwrite": "overwrite",
}


def _classify_keyword(word: str) -> SpanKind | None:
    return _KEYWORD_KINDS.get(word.lower())


def _add_reviewers(state: PushLineState, raw: str, offset: int, spans: list[Span]) -> None:
    for name in _split_reviewers(raw, offset, spans):
        state.add_reviewer(name)


def _set_topic(state: PushLineState, value: str, start: int, end: int, diags: list[Diagnostic]) -> None:
    if state.topic not in (None, value):
        diags.append(Diagnostic("warning", "Topic redefined; using last value", start, end))
    state.topic = value


def _removal(state: PushLineState, tok: Token, spans: list[Span]) -> None:
    """``-word``: switch a keyword off, or drop a reviewer."""
    inner, inner_start = tok.text[1:], tok.start + 1
    kind = _classify_keyword(inner)
    spans += [Span("minus", tok.start, inner_start), Span(kind or "reviewer", inner_start, tok.end)]
    if kind == "keyword_r":
        state.reviewers.clear()
    elif kind in _FLAG_FIELDS:
        setattr(state, _FLAG_FIELDS[kind], False)
    elif kind == "keyword_topic":
        state.topic = None
    elif kind in _STRATEGIES:
        state.strategy = "push"
    else:
        state.remove_reviewer(inner)


def _assignment(state: PushLineState, tok: Token, spans: list[Span], diags: list[Diagnostic]) -> None:
    """``r=alice,bob`` / ``topic=x``."""
    key, _eq, value = tok.text.partition("=")
    kind = _classify_keyword(key)
    if kind not in ("keyword_r", "keyword_topic"):
        spans.append(Span("error", tok.start, tok.end))
        diags.append(Diagnostic("error", f"Unknown key `{key}=`", tok.start, tok.end))
        return
    eq = tok.start + len(key)
    spans += [Span(kind, tok.start, eq), Span("equals", eq, eq + 1)]
    if not value:
        what = "`r=` reviewer list" if kind == "keyword_r" else "`topic=` value"
        diags.append(Diagnostic("error", f"Empty {what}", tok.start, tok.end))
    elif kind == "keyword_r":
        _add_reviewers(state, value, eq + 1, spans)
    else:
        spans.append(Span("topic_value", eq + 1, tok.end))
        _set_topic(state, value, tok.start, tok.end, diags)


def _keyword_value(
    state: PushLineState, kind: SpanKind, tok: Token, nxt: Token | None, spans: list[Span], diags: list[Diagnostic]
) -> int:
    """``r alice,bob`` / ``topic x``: the keyword takes the next token. How many tokens it used."""
    spans.append(Span(kind, tok.start, tok.end))
    is_word = nxt is not None and (nxt.text.startswith("-") or _classify_keyword(nxt.text) is not None)
    if nxt is None or (is_word and not (kind == "keyword_topic" and nxt.quoted)):
        what = "`r` requires a comma-separated reviewer list" if kind == "keyword_r" else "`topic` requires a value"
        diags.append(Diagnostic("error", what, tok.start, tok.end))
        return 1
    if kind == "keyword_r":
        _add_reviewers(state, nxt.text, nxt.start, spans)
    else:
        spans.append(Span("quoted" if nxt.quoted else "topic_value", nxt.start, nxt.end))
        _set_topic(state, nxt.text, tok.start, nxt.end, diags)
    return 2


def parse(buf: str) -> ParseResult:
    """Parse ``buf`` into a :class:`PushLineState` plus diagnostics and spans."""
    state = PushLineState()
    tokens, diags = tokenize(buf)
    spans: list[Span] = []
    i = 0
    while i < len(tokens):
        tok, nxt = tokens[i], tokens[i + 1] if i + 1 < len(tokens) else None
        i += 1
        kind = None if tok.quoted else _classify_keyword(tok.text)
        if tok.quoted:
            spans.append(Span("error", tok.start, tok.end))
            message = "Unexpected quoted value (only `topic` accepts a quoted value)"
            diags.append(Diagnostic("error", message, tok.start, tok.end))
        elif not tok.text:
            continue
        elif tok.text.startswith("-") and len(tok.text) > 1:
            _removal(state, tok, spans)
        elif "=" in tok.text:
            _assignment(state, tok, spans, diags)
        elif kind in ("keyword_r", "keyword_topic"):
            i += _keyword_value(state, kind, tok, nxt, spans, diags) - 1
        elif kind in _FLAG_FIELDS:
            spans.append(Span(kind, tok.start, tok.end))
            setattr(state, _FLAG_FIELDS[kind], True)
        elif kind in _STRATEGIES:
            spans.append(Span(kind, tok.start, tok.end))
            state.strategy = _STRATEGIES[kind]
        else:
            spans.append(Span("reviewer", tok.start, tok.end))
            state.add_reviewer(tok.text)
    diags.extend(_validate(state))
    spans.sort(key=lambda s: (s.start, s.end))
    return ParseResult(state=state, diagnostics=diags, spans=spans)


_REVIEWER_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+@")


def _validate(state: PushLineState) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for name in state.reviewers:
        if any(c not in _REVIEWER_NAME_OK for c in name):
            out.append(Diagnostic("warning", f"Reviewer `{name}` has unusual characters", 0, 0))
    return out


def _needs_quoting(value: str) -> bool:
    return any(c.isspace() or c in ('"', "'", "=") for c in value) or value == ""


def _quote(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def format_canonical(state: PushLineState, *, include_strategy: bool = True) -> str:
    """Render ``state`` as a stable, ergonomic line.

    Reviewers come first as ``r=alice,bob``, then optional ``topic=…``, then
    flags (``wip private``), then a non-default strategy keyword
    (``lazy``/``overwrite``) when ``include_strategy`` is true, and ``publish-drafts`` last.

    Persisted push-options history omits strategy (``include_strategy=False``).
    """
    parts: list[str] = []
    if state.reviewers:
        parts.append(f"r={','.join(state.reviewers)}")
    if state.topic is not None:
        value = _quote(state.topic) if _needs_quoting(state.topic) else state.topic
        parts.append(f"topic={value}")
    if state.wip:
        parts.append("wip")
    if state.private:
        parts.append("private")
    if include_strategy and state.strategy != "push":
        parts.append(state.strategy)
    if state.publish_drafts:
        parts.append(KW_PUBLISH_DRAFTS)
    return " ".join(parts)


def apply_session_strategy(line: str, strategy: str | None) -> str:
    """Return ``line`` with *strategy* merged for display when it is non-default.

    History lines never store strategy. When the CLI passes ``lazy`` /
    ``overwrite``, merge that keyword into the visible buffer (and Up/Down
    entries) for this session only.
    """
    if strategy == "lazy":
        merged: ReviewerStrategy = "lazy"
    elif strategy == "overwrite":
        merged = "overwrite"
    else:
        return line
    state = parse(line).state
    state.strategy = merged
    return format_canonical(state)
