"""Low-level Gerrit REST API access."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar
from urllib.parse import quote, unquote

from gerrit_workflow_tools.core.changeish import is_change_id
from gerrit_workflow_tools.core.config import ConfigError, Settings


logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class GerritApiError(RuntimeError):
    """Gerrit HTTP or JSON error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def norm_change_id(change_id: str) -> str:
    """Normalize Change-Id values for case-insensitive lookups (lowercase)."""
    return change_id.lower()


def change_id_for_gerrit_rest_path(change_id: str) -> str:
    """
    Return *change_id* for Gerrit ``changes/<id>/...`` URL segments.

    Gerrit expects the canonical Change-Id with an uppercase ``I`` prefix; values
    taken from :func:`norm_change_id` use a lowercase ``i`` and yield HTTP 404 unless corrected.
    """

    s = change_id.strip()
    if is_change_id(s):
        return "I" + s[1:].lower()
    return s


def encode_gerrit_path_segment(segment: str | int) -> str:
    """URL-encode one Gerrit REST path segment (project, change id, account, …).

    ``ChangeInfo.id`` values are already percent-encoded (``tools%2Frepo~123``).
    Unquote first so raw locally-built triplets and Gerrit-returned ids both
    encode once — never to ``%252F``.
    """

    return quote(unquote(str(segment)), safe="")


def parallel_map(
    callables: Iterable[Callable[[], _T]],
    *,
    max_workers: int = 8,
) -> list[_T]:
    """Run blocking REST callables concurrently and preserve input order."""

    jobs = list(callables)
    if not jobs:
        return []
    workers = min(max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda fn: fn(), jobs))


class GerritRest(Protocol):
    """Single-round-trip Gerrit operations — one call in, one Gerrit payload out.

    This is the seam. Everything that composes round trips — chunking, ``OR`` batching,
    triplet aliasing, the SQLite cache, parallelism — sits *above* it in
    :mod:`~gerrit_workflow_tools.core.gerrit.service` and the module-level helpers below,
    so an implementation only has to answer individual Gerrit questions.

    Implementations return Gerrit payloads verbatim and raise :class:`GerritApiError` on
    failure; interpreting or filtering those payloads is the caller's job. There is
    deliberately no raw-path escape hatch (``get_json``) — that would make the set of
    things crossing this seam unbounded. ``cli_fetch_api`` uses a concrete
    :class:`HttpGerritRest` for exactly that reason.
    """

    web_base: str

    def query_changes(self, query: str, *, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        """Return ChangeInfo rows; optional fields (labels, revisions, …) only as *options* ask."""

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """Return AccountInfo rows matching an account search query."""

    def get_change(self, change_id: str) -> dict[str, Any]:
        """Return one ChangeInfo, carrying the fields of a :data:`LOG_QUERY_OPTIONS` query row."""

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """Return AccountInfo detail for one account."""

    def get_comments(
        self,
        change_id: str,
        *,
        enable_context: bool = False,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return inline comments grouped by file path."""

    def list_drafts(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return the caller's draft comments on every patchset, grouped by file path."""

    def put_draft(
        self, change_id: str, revision: int | str, comment: dict[str, Any], *, draft_id: str | None = None
    ) -> dict[str, Any]:
        """Create a draft (CommentInput) on *revision*, or update *draft_id*; return its CommentInfo."""

    def delete_draft(self, change_id: str, revision: int | str, draft_id: str) -> None:
        """Delete one of the caller's drafts."""

    def publish_drafts(
        self, change_id: str, *, message: str | None = None, labels: Mapping[str, int] | None = None
    ) -> dict[str, Any]:
        """Publish all the caller's drafts on every patchset, with an optional message and votes."""

    def get_related(self, change_id: str, revision: int | str = "current") -> dict[str, Any]:
        """Return RelatedChangesInfo: the relation chain of *revision*, newest first."""

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """Return Checks-plugin rows for the current revision."""

    def get_messages(self, change_id: str) -> list[dict[str, Any]]:
        """Return change message rows (review/robot messages) for one change."""

    def list_change_reviewers(self, change_id: str) -> list[dict[str, Any]]:
        """Return reviewer rows for one change."""

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """Return suggested reviewer rows for one change."""

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """Return project-level reviewer defaults, or ``None`` when the plugin is absent."""

    def set_reviewers_batch(
        self,
        change_id: str,
        *,
        reviewers: list[str] | None = None,
        ccs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add reviewers and CCs to one change in a single request."""

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove one reviewer or CC from a change."""

    def set_topic(self, change_id: str, topic: str | None) -> None:
        """Set or clear the change topic."""

    def set_wip(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark a change work-in-progress or ready for review."""

    def set_private(self, change_id: str, on: bool) -> dict[str, Any]:
        """Set or clear the private flag on a change."""


def pick_change_from_query_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the single change from *rows* or raise :class:`GerritApiError` if none or ambiguous."""
    if not rows:
        raise GerritApiError("no matching change")
    if len(rows) > 1:
        shown = ", ".join(str(r.get("_number", "?")) for r in rows[:5]) + (", ..." if len(rows) > 5 else "")
        more = "+" if rows[-1].get("_more_changes") else ""
        raise GerritApiError(f"ambiguous change query ({len(rows)}{more} matches): {shown}")
    return rows[0]


def resolve_gerrit_web_base(settings: Settings) -> str:
    """
    Gerrit HTTPS base for the REST API and web links.

    Requires ``gerrit.webUrl`` in git config (no inference from remotes).
    """
    override = settings.gerrit_web_url
    if override:
        base = override.rstrip("/")
        logger.debug("resolve_gerrit_web_base: gerrit.webUrl -> %s", base)
        return base
    raise ConfigError(
        "gerrit.webUrl is not set; configure the Gerrit HTTPS base, e.g. "
        "`git config gerrit.webUrl https://gerrit.example.com`"
    )


# Options for batch stack queries and single-change resolution:
# labels + submittable + revisions in one round trip (no separate /detail call).
LOG_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "DETAILED_ACCOUNTS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)
