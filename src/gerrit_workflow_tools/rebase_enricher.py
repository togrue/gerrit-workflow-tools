"""GIT_SEQUENCE_EDITOR wrapper: enriches the rebase todo with Gerrit status, then opens the real editor."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from gerrit_workflow_tools.cli_common import configure_logging
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import (
    LogCommit,
)
from gerrit_workflow_tools.core.git_editor import resolve_git_editor, run_git_editor
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.stack import Commit, read_commit_rows
from gerrit_workflow_tools.render.status_fmt import (
    attention_text as _attention_text,
    code_review_token as _fmt_cr,
    comments_token as _fmt_comments,
    patchset_token as _fmt_patchset,
    verified_token as _fmt_verified,
)


logger = logging.getLogger(__name__)


# Actions that take a commit SHA as their second token.
_COMMIT_ACTIONS = frozenset({"pick", "p", "reword", "r", "edit", "e", "squash", "s", "fixup", "f", "drop", "d"})

# Width reserved for the commit subject in enriched lines.
_SUBJECT_WIDTH = 50


def _enriched_subject(commit: LogCommit) -> str:
    """
    Build the subject field for one enriched todo line.

    Format (plain text, column-aligned)::

        # perf: tweak hot path                    p v+1 cr+2     # submittable
        # docs: note change                        n v-1 cr+1 com # 3 unresolved comments
        # local commit not yet pushed              -              # not-pushed

    The leading ``# `` visually separates the sha from the content (mirrors ``ger log``).
    Git ignores the subject field when processing the rebase todo.
    """
    subj = commit.summary
    subj = subj[: _SUBJECT_WIDTH - 1] + "\u2026" if len(subj) > _SUBJECT_WIDTH else subj.ljust(_SUBJECT_WIDTH)

    ps = _fmt_patchset(commit)
    if commit.pushed:
        verified = _fmt_verified(commit.verified)
        cr = _fmt_cr(commit.code_review)
        comments = _fmt_comments(commit.comments_unresolved)
    else:
        verified = "   "
        cr = "    "
        comments = "   "

    attention = _attention_text(commit)
    attention_suffix = f"  # {attention}" if attention else ""

    return f"# {subj}  {ps} {verified} {cr} {comments}{attention_suffix}"


# ---------------------------------------------------------------------------
# Commit metadata loading
# ---------------------------------------------------------------------------


def _load_commit_metadata(short_shas: list[str], cwd: Path) -> dict[str, Commit]:
    """Return ``{todo_short_sha: commit row}`` for each SHA in the list, in one ``git log``.

    Each row carries the todo's short SHA, so ``LogCommit.short_sha`` matches the keys the
    todo is rewritten by.
    """
    if not short_shas:
        return {}
    try:
        rows = read_commit_rows(cwd, "--no-walk", *short_shas)
    except GitError:
        return {}
    result: dict[str, Commit] = {}
    for row in rows:
        for short in short_shas:
            if row.sha.startswith(short):
                result[short] = replace(row, short_sha=short)
                break
    return result


# ---------------------------------------------------------------------------
# Editor resolution
# ---------------------------------------------------------------------------


def _resolve_editor(cwd: Path, *, settings: Settings | None = None) -> str:
    """Find the real editor; ``GREBASE_EDITOR`` wins, then the shared git editor lookup."""
    return resolve_git_editor(cwd, settings=settings, env_overrides=("GREBASE_EDITOR",))


# ---------------------------------------------------------------------------
# Rebase sequence enrichment
# ---------------------------------------------------------------------------


def _enrich_todo(  # pylint: disable=too-many-branches,too-many-locals
    text: str,
    cwd: Path,
    *,
    gerrit: GerritRest | None = None,
    settings: Settings | None = None,
) -> str:
    """Rewrite rebase todo lines with Gerrit status annotations.

    Returns the original *text* unchanged if Gerrit is not configured for this
    repository (no ``gerrit.webUrl`` in git config).  Raises on Gerrit API errors
    so the caller can attach a diagnostic comment and fall back gracefully.

    A supplied *gerrit* implementation makes the config check moot: it already knows its
    own web base, matching ``GerritService.from_cwd(rest=…)``.
    """
    settings = settings if settings is not None else Settings.from_cwd(cwd)
    if gerrit is None and not settings.gerrit_web_url:
        return text  # Not a Gerrit repository — pass through silently.

    lines = text.splitlines(keepends=True)

    # Collect short SHAs for commit action lines.
    short_shas: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped.strip():
            continue
        parts = stripped.split(None, 2)
        if len(parts) >= 2 and parts[0] in _COMMIT_ACTIONS:
            short_shas.append(parts[1])

    if not short_shas:
        return text

    # Expand short SHAs to full SHA + Change-Id in one git call.
    meta_map = _load_commit_metadata(short_shas, cwd)

    # Build ordered input for fetch_gerrit_data.
    # We preserve the todo order (oldest-first) so chain-blocked detection is correct.
    commit_inputs = [meta_map[short_sha] for short_sha in short_shas if short_sha in meta_map]

    if not commit_inputs:
        return text

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    commits = annotate(commit_inputs, service=service, cwd=cwd)

    # Build short_sha → LogCommit lookup.
    commit_by_sha: dict[str, LogCommit] = {c.short_sha: c for c in commits}

    drop_merged = os.environ.get("GREBASE_DROP_MERGED_EQUIVALENT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    # Rewrite lines.
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped.strip():
            out.append(line)
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] in _COMMIT_ACTIONS:
            short_sha = parts[1]
            log_commit = commit_by_sha.get(short_sha)
            if log_commit is not None:
                newline = "\n" if line.endswith("\n") else ""
                action = parts[0]
                if drop_merged and log_commit.patchset_status == "merged-same" and log_commit.merged_equivalent is True:
                    if action == "pick":
                        action = "drop"
                    elif action == "p":
                        action = "d"
                out.append(f"{action} {short_sha} {_enriched_subject(log_commit)}{newline}")
                continue
        out.append(line)

    return "".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Invoked as ``GIT_SEQUENCE_EDITOR``: enrich the rebase todo then open the real editor."""
    if os.environ.get("GREBASE_DEBUG_LOG"):
        configure_logging(True)

    args = argv if argv is not None else sys.argv
    if len(args) < 2:
        print("usage: GIT_SEQUENCE_EDITOR for ger rebase", file=sys.stderr)
        return 1

    todo = Path(args[1])
    cwd = Path.cwd()
    settings = Settings.from_cwd(cwd)

    try:
        original_text = todo.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"error: cannot read rebase todo: {e}", file=sys.stderr)
        return 1

    # Attempt enrichment; on failure fall back to original text with a diagnostic comment.
    error_header: str | None = None
    try:
        final_text = _enrich_todo(original_text, cwd, gerrit=gerrit, settings=settings)
        logger.debug("rebase_enricher: todo enriched successfully")
    except (GerritApiError, GitError, ValueError, OSError) as e:
        logger.debug("rebase_enricher: enrichment failed: %s", e)
        final_text = original_text
        error_header = (
            f"# ger rebase: Gerrit enrichment failed — {e}\n"
            f"# ger rebase: Showing original todo without status annotations.\n"
        )

    if error_header:
        # Prepend above the pick lines so the user sees it immediately on opening.
        final_text = error_header + "\n" + final_text

    try:
        todo.write_text(final_text, encoding="utf-8")
    except OSError as e:
        print(f"error: cannot write rebase todo: {e}", file=sys.stderr)
        return 1

    editor = _resolve_editor(cwd, settings=settings)
    logger.debug("rebase_enricher: launching editor %r on %s", editor, todo)
    try:
        returncode = run_git_editor(todo, cwd, settings=settings, env_overrides=("GREBASE_EDITOR",))
    except FileNotFoundError:
        print(f"error: editor not found: {editor!r}", file=sys.stderr)
        return 1
    logger.debug("rebase_enricher: editor finished rc=%s", returncode)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
