"""Gerrit-backed reviewer name discovery for completion UIs (CLI-agnostic)."""

from __future__ import annotations

import re

from gerrit_workflow_tools.core.gerrit.rest import GerritClient
from gerrit_workflow_tools.core.reviewer import account_slug_from_gerrit

# Tokens accepted as bare reviewer login prefixes (aligned with push-input grammar).
REVIEWER_LOGIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def is_reviewer_login_token(s: str) -> bool:
    return bool(REVIEWER_LOGIN_TOKEN_RE.fullmatch(s.strip()))


def account_query_exact_lookup(reviewer: str) -> str:
    """Gerrit ``accounts/?q=`` query for resolving one typed reviewer token."""
    s = reviewer.strip()
    if "@" in s:
        return f"email:{s}"
    if REVIEWER_LOGIN_TOKEN_RE.fullmatch(s):
        return f"username:{s}"
    return s


def account_query_username_prefix(prefix: str) -> str:
    """Gerrit ``accounts/?q=`` query matching usernames starting with *prefix*."""
    return f"username:{prefix}*"


def slug_from_suggest_or_account_row(entry: dict[str, object]) -> str | None:
    """Normalize a row from ``suggest_reviewers`` or ``accounts/`` to a login slug."""
    nested = entry.get("account")
    if isinstance(nested, dict):
        slug = account_slug_from_gerrit(nested)
        if slug:
            return slug
    return account_slug_from_gerrit(entry)


def sorted_slugs_from_account_rows(
    rows: list[dict[str, object]],
    *,
    must_start_with: str | None,
) -> list[str]:
    """Deduplicate rows into sorted slug strings; optionally filter by lowercase prefix."""
    seen: set[str] = set()
    out: list[str] = []
    key = must_start_with.lower() if must_start_with else None
    for row in rows:
        slug = slug_from_suggest_or_account_row(row)
        if not slug:
            continue
        low = slug.lower()
        if key is not None and not low.startswith(key):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(slug)
    out.sort(key=lambda s: s.lower())
    return out


def fetch_suggested_reviewer_slugs(
    client: GerritClient,
    change_id: str,
    *,
    n: int = 100,
) -> list[str]:
    """Top suggested reviewer login names for a change (no typed-prefix filter)."""
    rows = client.suggest_change_reviewers(change_id, n=n)
    return sorted_slugs_from_account_rows(rows, must_start_with=None)


def fetch_reviewer_slugs_for_prefix(
    client: GerritClient,
    *,
    change_id: str | None,
    token: str,
    n: int = 100,
) -> list[str]:
    """Reviewer login names matching a typed prefix (suggest_reviewers ``q`` or account search)."""
    key = token.lower()
    if change_id:
        rows = client.suggest_change_reviewers(change_id, query=token, n=n)
    else:
        rows = client.query_accounts(account_query_username_prefix(token), n=min(n, 50))
    return sorted_slugs_from_account_rows(rows, must_start_with=key)
