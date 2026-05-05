"""prompt_toolkit glue for the Gerrit push options input line.

The pure parsing/formatting logic lives in :mod:`push_input_line`. This module
wires it into a prompt_toolkit ``PromptSession`` with a syntax-highlighting
lexer, live validation, keyword/reviewer completion, and a persisted default
buffer reflecting the last accepted line.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.validation import ValidationError, Validator

from gerrit_workflow_tools.cli_style import is_color_enabled
from gerrit_workflow_tools.push_input_line import (
    KW_LAZY,
    KW_OVERWRITE,
    KW_PRIVATE,
    KW_PUSH,
    KW_R,
    KW_TOPIC,
    KW_WIP,
    ParseResult,
    SpanKind,
    format_canonical,
    parse,
)
from gerrit_workflow_tools.reviewer_catalog import ReviewerCatalog

_STYLE_BY_KIND: dict[SpanKind, str] = {
    "minus": "fg:ansiyellow",
    "keyword_r": "fg:ansigreen bold",
    "keyword_topic": "fg:ansicyan",
    "keyword_wip": "fg:#a05a2c bold",
    "keyword_private": "fg:#9b30ff bold",
    "keyword_push": "fg:#4a7ebb bold",
    "keyword_lazy": "fg:#4a7ebb bold",
    "keyword_overwrite": "fg:#4a7ebb bold",
    "equals": "fg:ansiwhite",
    "comma": "fg:ansiwhite",
    "reviewer": "fg:ansibrightgreen",
    "topic_value": "fg:ansicyan",
    "quoted": "fg:ansicyan",
    "error": "fg:ansired bold",
    "unknown": "fg:ansired",
    "whitespace": "",
}


def _style_for(kind: SpanKind) -> str:
    if not is_color_enabled():
        return ""
    return _STYLE_BY_KIND.get(kind, "")


class PushOptionsLexer(Lexer):
    """Color the buffer using spans from :func:`push_input_line.parse`."""

    def lex_document(self, document: Document):  # type: ignore[override]
        text = document.text

        def get_line(line_no: int) -> FormattedText:
            if line_no != 0:
                return FormattedText([("", "")])
            res = parse(text)
            return FormattedText(_fragments_from_spans(text, res))

        return get_line


def _fragments_from_spans(text: str, res: ParseResult) -> list[tuple[str, str]]:
    """Turn classified spans + remaining gaps into ``(style, text)`` fragments."""
    fragments: list[tuple[str, str]] = []
    cursor = 0
    for span in res.spans:
        if span.start > cursor:
            fragments.append(("", text[cursor : span.start]))
        fragments.append((_style_for(span.kind), text[span.start : span.end]))
        cursor = span.end
    if cursor < len(text):
        fragments.append(("", text[cursor:]))
    return fragments


class PushOptionsValidator(Validator):
    """Block accept on hard parse errors; warnings pass through."""

    def validate(self, document: Document) -> None:  # type: ignore[override]
        res = parse(document.text)
        for diag in res.diagnostics:
            if diag.severity == "error":
                raise ValidationError(message=diag.message, cursor_position=diag.start)


class PushOptionsCompleter(Completer):
    """Complete reserved keywords and known reviewer names at the cursor word."""

    def __init__(self, reviewer_seeds: Iterable[str] = ()):
        seen: set[str] = set()
        ordered: list[str] = []
        for name in reviewer_seeds:
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        self._reviewer_seeds = ordered

    def get_completions(self, document: Document, complete_event: CompleteEvent):  # type: ignore[override]
        word = document.get_word_before_cursor(WORD=True)
        candidates: list[tuple[str, str]] = [
            (f"{KW_R}=", "reviewer list"),
            (KW_TOPIC + "=", "change topic"),
            (KW_WIP, "mark as WIP"),
            (KW_PRIVATE, "mark as private"),
            (KW_PUSH, "reviewers via %r= on push"),
            (KW_LAZY, "reviewers via REST only when missing"),
            (KW_OVERWRITE, "reviewers via REST on all changes"),
        ]
        candidates.extend((name, "reviewer") for name in self._reviewer_seeds)
        prefix = word.lstrip("-").lower()
        for value, meta in candidates:
            if not prefix or value.lower().startswith(prefix):
                yield Completion(value, start_position=-len(word.lstrip("-")), display_meta=meta)


def _last_line_path() -> Path:
    d = Path.home() / ".cache" / "ger"
    d.mkdir(parents=True, exist_ok=True)
    return d / "push_options_line_last"


def load_last_canonical() -> str:
    """Return the last canonical line stored on disk, or empty string."""
    p = _last_line_path()
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_last_canonical(line: str) -> None:
    """Persist ``line`` (already canonical) for the next prompt's default buffer."""
    p = _last_line_path()
    with contextlib.suppress(OSError):
        p.write_text(line, encoding="utf-8")


def _bottom_toolbar(text: str, catalog: ReviewerCatalog | None = None) -> FormattedText | None:
    res = parse(text)
    errors = [d for d in res.diagnostics if d.severity == "error"]
    warnings = [d for d in res.diagnostics if d.severity == "warning"]
    if errors:
        msg = errors[0].message
        extra = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return FormattedText([("fg:ansired", f"error: {msg}{extra}")])
    if catalog is not None:
        validation = catalog.validate_state(res.state)
        if validation.issues:
            msg = validation.issues[0].message
            extra = f" (+{len(validation.issues) - 1} more)" if len(validation.issues) > 1 else ""
            return FormattedText([("fg:ansired", f"reviewer: {msg}{extra}")])
        if validation.pending_checks and res.state.reviewers:
            return FormattedText([("fg:#808080", "reviewer validation: checking Gerrit...")])
    if warnings:
        msg = warnings[0].message
        extra = f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""
        return FormattedText([("fg:ansiyellow", f"warning: {msg}{extra}")])
    if catalog is not None:
        hint = catalog.default_toolbar_hint()
    else:
        hint = "keywords: r= topic= wip private push lazy overwrite"
    return FormattedText([("fg:#808080", hint)])


def prompt_push_options_line(
    *,
    default: str | None = None,
    reviewer_seeds: Iterable[str] = (),
    message: str = "Push options: ",
    cwd: Path | None = None,
    change_id_hint: str | None = None,
) -> ParseResult:
    """Show the prompt and return the parsed result for the accepted line.

    ``default`` pre-fills the buffer; when omitted, the last persisted canonical
    line is used. On accept, the canonical form of the parsed state is saved
    back to disk so the next prompt opens with the same state.
    """
    initial = default if default is not None else load_last_canonical()
    seed_list = [s for s in reviewer_seeds if s]
    catalog = ReviewerCatalog.from_runtime(cwd=cwd, reviewer_seeds=seed_list, change_id_hint=change_id_hint)
    completion_candidates = catalog.completion_candidates()
    session: PromptSession[str] = PromptSession(
        message=message,
        lexer=PushOptionsLexer(),
        validator=PushOptionsValidator(),
        validate_while_typing=False,
        completer=PushOptionsCompleter(completion_candidates),
        complete_while_typing=True,
        bottom_toolbar=lambda: _bottom_toolbar(session.default_buffer.text, catalog),
    )
    raw = session.prompt(default=initial)
    res = parse(raw)
    if res.valid_for_apply:
        save_last_canonical(format_canonical(res.state))
    return res
