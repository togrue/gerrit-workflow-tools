"""The **changeish** grammar: what an input that might name a commit or a Gerrit change looks like.

Pure syntax, no I/O and no Gerrit dialect. This module sits below
:mod:`gerrit_workflow_tools.core.gerrit`, so both the REST layer and the resolution layer can
use it — ``core/gerrit/rest.py`` imports only ``core.config`` and ``core.git_run``, and
``change_resolution`` imports ``rest``, so the grammar cannot live in either without a cycle.

:func:`parse` is **total**: every string yields a :class:`Changeish`, falling back to
``git-rev`` the way the classifier always has. Callers that want a narrower reading ask for
it (:meth:`Changeish.as_batch_ref`) and get ``None`` when the input is not that kind. Failing
to be a triplet is not an error, so it is not raised as one — it used to surface as a
:class:`GerritApiError`, which said something untrue about where the problem was.

Deciding what a changeish *means* — which Gerrit change it selects, which local commit it maps
to — stays in :mod:`gerrit_workflow_tools.core.gerrit.change_resolution`. Turning one into a
Gerrit search query stays in :mod:`gerrit_workflow_tools.core.gerrit.rest`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlparse


ChangeishKind = Literal[
    "git-rev",
    "change-id",
    "triplet",
    "change-number",
    "change-ref",
    "url",
    "query",
]

# A Change-Id is ``I`` plus 40 hex digits, **either case** — as CONTEXT.md defines it. One
# grammar, deliberately: this replaced four predicates that disagreed, one of which rejected
# uppercase hex and so called a Change-Id valid for `ger push` invalid for `ger change-id`.
CHANGE_ID_RE = re.compile(r"^[iI][0-9a-fA-F]{40}$")

_CHANGE_REF_RE = re.compile(r"^refs/changes/\d+/(\d+)/(\d+)$")
_GERRIT_URL_CHANGE_RE = re.compile(r"/c/(?:(?P<project>.+)/)?\+/(?P<number>\d+)")
_DIGITS_RE = re.compile(r"^\d+$")

_GIT_REV_PREFIXES = ("rev:", "git:")
_CHANGE_NUMBER_PREFIXES = ("change:", "cl:")
_QUERY_PREFIX = "q:"


# Kinds that address a change by number instead of carrying its Change-Id, so Gerrit has to be
# asked for the id before anything local can be matched. `ger fix` and `ger edit` each had
# their own version of this list, and they disagreed.
KINDS_NEEDING_GERRIT: tuple[ChangeishKind, ...] = ("change-number", "change-ref", "url", "query")


def is_change_id(s: str) -> bool:
    """Whether *s* is a well-formed Gerrit **Change-Id** (``I`` + 40 hex, either case)."""
    return bool(CHANGE_ID_RE.fullmatch(s.strip()))


def _strip_prefix(s: str, prefixes: tuple[str, ...] | str) -> str:
    """Return *s* without a leading prefix, matched case-insensitively."""
    candidates = (prefixes,) if isinstance(prefixes, str) else prefixes
    lower = s.lower()
    for prefix in candidates:
        if lower.startswith(prefix):
            return s[len(prefix) :].strip()
    return s.strip()


def _split_triplet(s: str) -> tuple[str, str, str] | None:
    """Split ``project~branch~changeId`` when all three parts are present and the id is valid.

    Both halves are checked here on purpose. The three splitters this replaced each checked
    one half: one required non-empty parts but accepted any id, another validated the id but
    accepted empty parts, a third checked neither.
    """
    if "~" not in s:
        return None
    parts = s.split("~")
    if len(parts) != 3:
        return None
    project, branch, change_id = (p.strip() for p in parts)
    if not project or not branch or not is_change_id(change_id):
        return None
    return project, branch, change_id


def _url_target(url: str) -> tuple[str | None, str | None]:
    """``(number, project)`` from a Gerrit change URL; either is ``None`` when it has none."""
    match = _GERRIT_URL_CHANGE_RE.search(url)
    if match:
        project = match.group("project")
        return match.group("number"), unquote(project) if project else None
    path = urlparse(url).path or ""
    tail = path.rstrip("/").split("/")[-1]
    return (tail if tail.isdigit() else None), None


@dataclass(frozen=True)
class Changeish:
    """One parsed **changeish**. Which fields are set follows from :attr:`kind`.

    Every field beyond ``raw`` and ``kind`` is optional because no single kind fills them all.
    ``change_id`` is kept exactly as written — canonicalizing case is the job of the layer that
    needs a particular form (``change_id_for_gerrit_rest_path`` wants a leading ``I``, the cache
    wants lowercase), not of the grammar.
    """

    raw: str
    kind: ChangeishKind
    change_id: str | None = None
    project: str | None = None
    branch: str | None = None
    number: str | None = None
    """Digits for ``change-ref`` and ``url``. For ``change-number`` this is whatever followed
    the prefix, digits or not — validating it is the caller's job, as it always was."""
    query: str | None = None
    rev: str | None = None
    patchset: int | None = None
    """The patch set a ``change-ref`` names."""

    @property
    def triplet(self) -> str | None:
        """``project~branch~changeId`` when this is a triplet, else ``None``."""
        if self.kind != "triplet":
            return None
        return f"{self.project}~{self.branch}~{self.change_id}"

    def as_batch_ref(self) -> str | None:
        """Read this as a batch/cache key, or ``None`` when it is not one.

        Batch refs are an internal key space — triplets and Gerrit change numbers produced by
        this codebase, never typed by a user. A bare number therefore means a change number
        here, where the same string as user input is a git rev (``120045`` is a plausible
        abbreviated SHA, and git wins that tie).
        """
        if self.kind == "triplet":
            return self.triplet
        if _DIGITS_RE.fullmatch(self.raw):
            return self.raw
        return None


def address_problem(parsed: Changeish, *, web_base: str | None) -> str | None:
    """Why *parsed* cannot be the address it looks like, or ``None`` when it can be."""
    if parsed.kind == "change-ref" and parsed.number is not None:
        shard = parsed.number[-2:].zfill(2)
        if parsed.raw.split("/")[2] != shard:
            return f"invalid change ref {parsed.raw!r}: change {parsed.number} lives under refs/changes/{shard}/"
    if parsed.kind == "url" and web_base:
        host, own_host = urlparse(parsed.raw).hostname, urlparse(web_base).hostname
        if host and own_host and host.lower() != own_host.lower():
            return f"{parsed.raw!r} is not on the configured Gerrit host {own_host}"
    return None


_CHANGE_ID_LOOKALIKE_RE = re.compile(r"^[iI][0-9A-Za-z]{6,}$")


# The not-found family: every command says "cannot resolve '<input>': <what is missing>",
# with the endings below, so one input reads the same wherever it fails.


def unresolved_rev_message(rev: str) -> str:
    """What to tell the user when *rev*, read as a git revision, names no commit.

    A bare number is a git rev by design, so the message says how to name the change
    instead; a near-miss Change-Id says what a valid one is.
    """
    message = f"cannot resolve {rev!r}: no commit by that name"
    if _DIGITS_RE.fullmatch(rev):
        return f"{message}; did you mean change:{rev}?"
    if _CHANGE_ID_LOOKALIKE_RE.fullmatch(rev) and not is_change_id(rev):
        return f"{message}, and no Change-Id either (I plus 40 hex digits)"
    return message


def unresolved_change_message(raw: str) -> str:
    """What to tell the user when *raw*, read as a Gerrit change, names none on Gerrit."""
    missing = "no Gerrit change has that Change-Id" if is_change_id(raw) else "Gerrit has no such change"
    return f"cannot resolve {raw!r}: {missing}"


def not_in_stack_message(raw: str, sha: str | None = None) -> str:
    """What to tell the user when *raw* names something, but no commit of the local stack."""
    if sha:
        return f"cannot resolve {raw!r}: commit {sha[:8]} is not in the local stack"
    return f"cannot resolve {raw!r}: no commit in the local stack has that Change-Id"


def no_commit_with_change_id_message(change_id: str) -> str:
    """What ``ger sha`` says when no commit it searched carries *change_id*."""
    return f"cannot resolve {change_id!r}: no commit has that Change-Id"


def parse(s: str) -> Changeish:
    """Parse *s* into a :class:`Changeish`. Total — anything unrecognized is a ``git-rev``.

    Classification order matters: explicit prefixes win over shape, so ``rev:120045`` is a git
    rev and ``change:120045`` a change number, whatever the digits look like.
    """
    raw = s.strip()
    lower = raw.lower()

    if lower.startswith(_GIT_REV_PREFIXES):
        return Changeish(raw=raw, kind="git-rev", rev=_strip_prefix(raw, _GIT_REV_PREFIXES))

    if lower.startswith(_CHANGE_NUMBER_PREFIXES):
        return Changeish(raw=raw, kind="change-number", number=raw.split(":", 1)[1])

    if lower.startswith(_QUERY_PREFIX):
        return Changeish(raw=raw, kind="query", query=_strip_prefix(raw, _QUERY_PREFIX))

    if raw.startswith(("http://", "https://")):
        number, project = _url_target(raw)
        return Changeish(raw=raw, kind="url", number=number, project=project)

    change_ref = _CHANGE_REF_RE.fullmatch(raw)
    if change_ref:
        return Changeish(raw=raw, kind="change-ref", number=change_ref[1], patchset=int(change_ref[2]))

    triplet = _split_triplet(raw)
    if triplet is not None:
        project, branch, change_id = triplet
        return Changeish(raw=raw, kind="triplet", project=project, branch=branch, change_id=change_id)

    if is_change_id(raw):
        return Changeish(raw=raw, kind="change-id", change_id=raw)

    return Changeish(raw=raw, kind="git-rev", rev=raw)
