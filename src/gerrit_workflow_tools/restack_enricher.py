"""GIT_SEQUENCE_EDITOR wrapper: enriches the rebase todo with Gerrit status, then opens the real editor."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import configure_logging
from gerrit_workflow_tools.config import gerrit_web_url
from gerrit_workflow_tools.gerrit_change_status import (
    LogCommit,
    commit_blocks_chain_for_submittability,
    determine_attention,
    fetch_gerrit_data,
)
from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError, git
from gerrit_workflow_tools.stack import parse_change_id

logger = logging.getLogger(__name__)

# Actions that take a commit SHA as their second token.
_COMMIT_ACTIONS = frozenset({"pick", "p", "reword", "r", "edit", "e", "squash", "s", "fixup", "f", "drop", "d"})

# Width reserved for the commit subject in enriched lines.
_SUBJECT_WIDTH = 50

# Record separator used in git --format strings (ASCII RS, same as stack.py).
_RS = "\x1e"


# ---------------------------------------------------------------------------
# Plain-text formatting helpers (no ANSI — this is an editable file)
# ---------------------------------------------------------------------------


def _fmt_patchset(commit: LogCommit) -> str:
    if commit.abandoned:
        return "a"
    status = commit.patchset_status
    if status == "merged-same":
        return "m"
    if status == "merged-drift":
        return "!"
    if status == "merged-unknown":
        return "?"
    if status == "active":
        return "p"
    if status == "newer":
        return "n"
    if status == "outdated":
        return "o"
    return "-"


def _fmt_verified(v: int | None) -> str:
    """Three-char verified label: ``v+1``, ``v-1``, ``v0 ``, ``v? ``."""
    if v is None:
        return "v? "
    if v >= 1:
        return "v+1"
    if v <= -1:
        return "v-1"
    return "v0 "


def _fmt_cr(cr: int | None) -> str:
    """Four-char code-review label: ``cr+2``, ``cr+1``, ``cr0 ``, ``cr-1``, ``cr-2``, ``cr? ``."""
    if cr is None:
        return "cr? "
    if cr >= 2:
        return "cr+2"
    if cr == 1:
        return "cr+1"
    if cr == -1:
        return "cr-1"
    if cr <= -2:
        return "cr-2"
    return "cr0 "


def _fmt_comments(count: int) -> str:
    """Three-char comments indicator: ``com`` or blank."""
    return "com" if count > 0 else "   "


def _attention_text(commit: LogCommit) -> str:
    """Short plain-text annotation for the trailing ``# …`` column, or empty string."""
    if commit.abandoned:
        return "abandoned"
    if not commit.pushed:
        return "not-pushed"
    if commit.patchset_status == "merged-same":
        return ""
    if commit.patchset_status == "merged-drift":
        return "merged drift"
    if commit.patchset_status == "merged-unknown":
        return "merged (equiv. unknown)"
    parts: list[str] = []
    if commit.ci_failures:
        parts.append(f"CI failed: {commit.ci_failures[0]}")
    elif commit.verified is not None and commit.verified <= -1:
        parts.append("build failed")
    if commit.comments_unresolved > 0:
        n = commit.comments_unresolved
        noun = "comment" if n == 1 else "comments"
        parts.append(f"{n} unresolved {noun}")
    if not parts and commit.submittable:
        return "submittable"
    return ", ".join(parts)


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


def _load_commit_metadata(short_shas: list[str], cwd: Path) -> dict[str, tuple[str, str, str | None]]:
    """Return ``{todo_short_sha: (full_sha, subject, change_id)}`` for each SHA in the list.

    Uses a single ``git log --no-walk`` call with RS-separated fields, the same approach
    as :func:`~gerrit_workflow_tools.stack.commits_in_range`.
    """
    if not short_shas:
        return {}
    fmt = f"%H{_RS}%s{_RS}%B{_RS}"
    p = git("log", "--no-walk", f"--format={fmt}", *short_shas, cwd=cwd, check=False)
    if p.returncode != 0 or not p.stdout:
        return {}

    parts = p.stdout.split(_RS)
    while parts and not parts[-1].strip():
        parts.pop()

    result: dict[str, tuple[str, str, str | None]] = {}
    i = 0
    while i + 2 < len(parts):
        full_sha = parts[i].strip()
        subject = parts[i + 1].strip()
        body = parts[i + 2]
        i += 3
        if not full_sha or len(full_sha) < 7:
            continue
        change_id = parse_change_id(body)
        # Match full_sha back to the short SHA that was passed in.
        for s in short_shas:
            if full_sha.startswith(s):
                result[s] = (full_sha, subject, change_id)
                break

    return result


# ---------------------------------------------------------------------------
# Editor resolution
# ---------------------------------------------------------------------------


def _resolve_editor(cwd: Path) -> str:
    """Find the real editor, following git's lookup order (skipping ``sequence.editor``).

    Priority:
    1. ``GREBASE_EDITOR`` env var (explicit override; set by ``ger restack`` if needed)
    2. ``GIT_EDITOR`` env var
    3. ``core.editor`` git config
    4. ``VISUAL`` env var
    5. ``EDITOR`` env var
    6. ``vi`` fallback

    ``sequence.editor`` is deliberately skipped: we *are* the sequence editor, and
    reading it risks infinite recursion when users set it to this wrapper directly.
    """
    for env_var in ("GREBASE_EDITOR", "GIT_EDITOR"):
        val = os.environ.get(env_var)
        if val:
            return val

    p = git("config", "core.editor", cwd=cwd, check=False)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()

    for env_var in ("VISUAL", "EDITOR"):
        val = os.environ.get(env_var)
        if val:
            return val

    return "vi"


# ---------------------------------------------------------------------------
# Todo enrichment
# ---------------------------------------------------------------------------


def _enrich_todo(text: str, cwd: Path) -> str:
    """Rewrite rebase todo lines with Gerrit status annotations.

    Returns the original *text* unchanged if Gerrit is not configured for this
    repository (no ``gerrit.webUrl`` in git config).  Raises on Gerrit API errors
    so the caller can attach a diagnostic comment and fall back gracefully.
    """
    if not gerrit_web_url(cwd):
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
    commit_inputs: list[tuple[str, str, str, str | None]] = []
    for short_sha in short_shas:
        meta = meta_map.get(short_sha)
        if meta:
            full_sha, subject, change_id = meta
            # Pass the todo's short SHA as the short field so LogCommit.short_sha
            # matches the keys we use for lookup below.
            commit_inputs.append((full_sha, short_sha, subject, change_id))

    if not commit_inputs:
        return text

    web_base = resolve_gerrit_web_base(cwd)
    client = GerritClient(web_base, cwd=str(cwd))
    commits = fetch_gerrit_data(client, web_base, commit_inputs, cwd=cwd)

    # Annotate attention (chain-blocked aware, oldest-first).
    for idx, commit in enumerate(commits):
        chain_blocked = False
        if commit.pushed:
            for earlier in commits[:idx]:
                if earlier.pushed and commit_blocks_chain_for_submittability(earlier):
                    chain_blocked = True
                    break
        commit.attention_reasons = determine_attention(commit, chain_blocked=chain_blocked)

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


def main(argv: list[str] | None = None) -> int:
    """Invoked as ``GIT_SEQUENCE_EDITOR``: enrich the rebase todo then open the real editor."""
    if os.environ.get("GREBASE_DEBUG_LOG"):
        configure_logging(True)

    args = argv if argv is not None else sys.argv
    if len(args) < 2:
        print("usage: GIT_SEQUENCE_EDITOR for ger restack", file=sys.stderr)
        return 1

    todo = Path(args[1])
    cwd = Path.cwd()

    try:
        original_text = todo.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"error: cannot read rebase todo: {e}", file=sys.stderr)
        return 1

    # Attempt enrichment; on failure fall back to original text with a diagnostic comment.
    error_header: str | None = None
    try:
        final_text = _enrich_todo(original_text, cwd)
        logger.debug("restack_enricher: todo enriched successfully")
    except (GerritApiError, GitError, ValueError, OSError) as e:
        logger.debug("restack_enricher: enrichment failed: %s", e)
        final_text = original_text
        error_header = (
            f"# ger restack: Gerrit enrichment failed — {e}\n"
            f"# ger restack: Showing original todo without status annotations.\n"
        )
    except Exception as e:
        logger.debug("restack_enricher: unexpected enrichment error: %s", e)
        final_text = original_text
        error_header = (
            f"# ger restack: Unexpected error during enrichment — {e}\n"
            f"# ger restack: Showing original todo without status annotations.\n"
        )

    if error_header:
        # Prepend above the pick lines so the user sees it immediately on opening.
        final_text = error_header + "\n" + final_text

    try:
        todo.write_text(final_text, encoding="utf-8")
    except OSError as e:
        print(f"error: cannot write rebase todo: {e}", file=sys.stderr)
        return 1

    # Launch the real editor.
    editor = _resolve_editor(cwd)
    logger.debug("restack_enricher: launching editor %r on %s", editor, todo)
    try:
        ed_cmd = [*shlex.split(editor), str(todo)]
        logger.debug("restack_enricher: run: %s", " ".join(ed_cmd))
        result = subprocess.run(ed_cmd, check=False)
        logger.debug("restack_enricher: editor finished rc=%s", result.returncode)
        return result.returncode
    except FileNotFoundError:
        print(f"error: editor not found: {editor!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
