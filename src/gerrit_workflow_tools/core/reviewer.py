"""Reviewer/account normalization helpers shared by CLI and core."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.http_rest import gerrit_auth_from_settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, parallel_map
from gerrit_workflow_tools.core.gerrit.stack_context import stack_project
from gerrit_workflow_tools.core.git_state import repo_toplevel
from gerrit_workflow_tools.core.reviewers_strategy import default_reviewers_via_registry
from gerrit_workflow_tools.core.stack import get_stack_snapshot


@dataclass(frozen=True)
class ReviewerAccount:
    """Normalized Gerrit reviewer account identity used by CLI and core."""

    slug: str
    account_id: int | None = None
    cc: bool = False
    """Gerrit lists the account as ``CC``, not as a ``REVIEWER``."""


class ReviewerStrategy(str, Enum):
    """How reviewers are applied during `ger push`."""

    PUSH = "push"
    LAZY = "lazy"
    OVERWRITE = "overwrite"


def gerrit_credentials_configured(settings: Settings) -> bool:
    """Whether Gerrit REST credentials are configured in git config."""

    return gerrit_auth_from_settings(settings) is not None


def account_slug_from_gerrit(account: Mapping[str, object]) -> str | None:
    """Best-effort Gerrit account slug normalization."""

    username = account.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    email = account.get("email")
    if isinstance(email, str) and "@" in email:
        return email.split("@", 1)[0].strip()
    name = account.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def format_gerrit_account_label(account: Mapping[str, object]) -> str | None:
    """Human label for Gerrit AccountInfo, e.g. ``grt (Tobias Grün)``."""

    slug = account_slug_from_gerrit(account)
    raw_name = account.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if slug and name and slug.lower() != name.lower():
        return f"{slug} ({name})"
    if slug:
        return slug
    if name:
        return name
    return None


def _reviewer_accounts(accounts: Iterable[tuple[object, str | None]]) -> list[ReviewerAccount]:
    """``(AccountInfo, role)`` rows that name someone, in order; anything else is skipped."""
    out: list[ReviewerAccount] = []
    for account, role in accounts:
        if not isinstance(account, dict):
            continue
        slug = account_slug_from_gerrit(account)
        if slug:
            account_id = account.get("_account_id")
            out.append(
                ReviewerAccount(
                    slug=slug, account_id=account_id if isinstance(account_id, int) else None, cc=role == "CC"
                )
            )
    return out


def reviewer_accounts_from_reviewer_list(rows: Sequence[Mapping[str, object]]) -> list[ReviewerAccount]:
    """Parse ``GET changes/<id>/reviewers/`` rows (flat accounts or ReviewerInfo)."""

    def _keeps(row: Mapping[str, object]) -> bool:
        if not isinstance(row, dict):
            return False
        state = row.get("state")
        return not isinstance(state, str) or state in ("REVIEWER", "CC")

    return _reviewer_accounts(
        (row["account"] if isinstance(row.get("account"), dict) else row, _str_or_none(row.get("state")))
        for row in rows
        if _keeps(row)
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def reviewer_accounts_from_change_info(
    detail: Mapping[str, object],
    *,
    roles: Sequence[str] = ("REVIEWER", "CC"),
) -> list[ReviewerAccount]:
    """Reviewer accounts in Gerrit API order, limited to *roles*.

    ChangeInfo stores reviewers in a ``REVIEWER`` / ``CC`` map. The list shape
    (``state`` on each row) is the same split. ``overwrite`` asks for
    ``REVIEWER`` only so CC accounts are not removed.
    """

    return _reviewer_accounts(_reviewer_account_infos(detail, roles))


def _reviewer_account_infos(
    detail: Mapping[str, object], roles: Sequence[str]
) -> Iterator[tuple[Mapping[str, object], str]]:
    """``(AccountInfo, role)`` for each ChangeInfo ``reviewers`` row in *roles* (map or list shape)."""
    reviewers = detail.get("reviewers")
    if isinstance(reviewers, dict):
        for role in roles:
            bucket = reviewers.get(role)
            if isinstance(bucket, list):
                yield from ((account, role) for account in bucket if isinstance(account, dict))
    elif isinstance(reviewers, list):
        for entry in reviewers:
            if isinstance(entry, dict) and entry.get("state") in roles and isinstance(entry.get("account"), dict):
                yield entry["account"], entry["state"]


def removable_reviewer_ids(detail: Mapping[str, object]) -> set[int] | None:
    """Account ids Gerrit lets the caller remove from the change, or ``None`` when the payload does not say.

    ``removable_reviewers`` comes with ``DETAILED_LABELS``. A change owner may remove only
    reviewers whose vote is zero or positive; removing a negative vote needs the
    *Remove Reviewer* right.
    """
    rows = detail.get("removable_reviewers")
    if not isinstance(rows, list):
        return None
    return {row["_account_id"] for row in rows if isinstance(row, dict) and isinstance(row.get("_account_id"), int)}


def reviewer_handles(details: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Account id by unique handle (id, username, email; casefolded) for everyone on *details*.

    Read from the reviewer and CC rows of ChangeInfo fetched with ``DETAILED_ACCOUNTS``. A full
    name is not a handle: it names one account only when it is unique on the whole server.
    """
    out: dict[str, int] = {}
    for detail in details:
        for account, _role in _reviewer_account_infos(detail, ("REVIEWER", "CC")):
            account_id = account.get("_account_id")
            if not isinstance(account_id, int):
                continue
            out[str(account_id)] = account_id
            for key in ("username", "email"):
                value = account.get(key)
                if isinstance(value, str) and value.strip():
                    out[value.strip().casefold()] = account_id
    return out


def reviewer_account_ids(
    rest: GerritRest, names: Sequence[str], *, known: Mapping[str, int] | None = None
) -> dict[str, int | None]:
    """The account id each reviewer name resolves to on Gerrit (username, email, full name or id).

    A name in *known* (see :func:`reviewer_handles`) resolves without a request; the rest
    ask ``GET accounts/{name}``. ``None`` when Gerrit names no single account for it (HTTP
    404 or 400): a group, an unknown or an ambiguous name. Any other failure (401, 5xx,
    Gerrit unreachable) raises :class:`GerritApiError`, since nothing is known then. Who
    someone is on a change is their account id; the name a user typed need not be the slug
    Gerrit reports for them.
    """
    local = known or {}

    def _lookup(name: str) -> int | None:
        try:
            raw = rest.get_account(name).get("_account_id")
        except GerritApiError as error:
            if error.status in (400, 404):
                return None
            raise
        return raw if isinstance(raw, int) else None

    unique = list(dict.fromkeys(names))
    out: dict[str, int | None] = {
        name: local[name.strip().casefold()] for name in unique if name.strip().casefold() in local
    }
    asked = [name for name in unique if name not in out]
    out.update(zip(asked, parallel_map(partial(_lookup, name) for name in asked), strict=True))
    return {name: out[name] for name in unique}


def parse_reviewer_names(raw: str) -> list[str]:
    """Split a comma- or space-separated reviewer list, dropping blanks and duplicates."""
    return list(dict.fromkeys(raw.replace(",", " ").split()))


def resolve_push_reviewers(
    cwd: Path,
    branch: str,
    reviewer_flag_segments: Sequence[str],
    *,
    settings: Settings,
) -> list[str]:
    """Reviewers for a push: flags, else the reviewer registry, else branch config."""
    if reviewer_flag_segments:
        return parse_reviewer_names(",".join(reviewer_flag_segments))
    try:
        rows = list(get_stack_snapshot(cwd, branch, head=branch, first_parent=True, settings=settings).commits)
    except Exception:  # pylint: disable=broad-exception-caught
        rows = []
    scripted = default_reviewers_via_registry(
        repo_toplevel(cwd),
        project=stack_project(cwd, settings=settings),
        branch=branch,
        commits=rows,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )
    if scripted is not None:
        return scripted
    return branch_reviewers(branch, settings=settings)


def branch_reviewers(branch: str, *, settings: Settings) -> list[str]:
    """Reviewers saved in *branch*'s ``gerReviewers`` config."""
    cfg = settings.branch_gerrit_reviewers(branch)
    return parse_reviewer_names(cfg) if cfg else []
