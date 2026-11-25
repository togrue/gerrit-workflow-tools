"""One Gerrit ``ChangeInfo`` payload, parsed once.

REST payloads stop here. The Gerrit overlay (``build_log_commit``), the inbox's review chains
and ``ger show`` all read a :class:`ChangeView` instead of reaching into the dict, so labels,
revisions, parents and accounts are interpreted one way. Raw dicts stay below this module, in
``rest.py`` and the cache.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.core.changeish import Changeish
from gerrit_workflow_tools.core.gerrit.change_queries import change_freshness_key
from gerrit_workflow_tools.core.reviewer import ReviewerAccount, reviewer_accounts_from_change_info


def parse_gerrit_time(raw: object) -> datetime | None:
    """Parse a Gerrit timestamp (``yyyy-mm-dd hh:mm:ss.fffffffff``) as UTC."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T", 1)
    if "." in text:
        head, frac = text.split(".", 1)
        digits = ""
        rest = ""
        for index, char in enumerate(frac):
            if char.isdigit():
                digits += char
            else:
                rest = frac[index:]
                break
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _str(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def _sha(raw: object) -> str | None:
    """Lower-cased SHA text, or ``None`` when blank or not a string."""
    return raw.strip().lower() if isinstance(raw, str) and raw.strip() else None


def _label_value(labels: Mapping[str, Any], label_name: str) -> int | None:
    """The effective vote for a label, or ``None`` if nobody voted.

    A negative vote (e.g. Code-Review -2) outranks approvals: blocking votes win.
    """
    label = labels.get(label_name)
    if not isinstance(label, dict):
        return None

    def _all_values() -> list[int]:
        return [
            int(vote.get("value", 0))
            for vote in label.get("all", [])
            if isinstance(vote, dict) and vote.get("value") is not None
        ]

    v = label.get("value")
    if v is not None:
        try:
            iv = int(v)
            if iv == 0 and not any(x != 0 for x in _all_values()):
                return None
            return iv
        except (TypeError, ValueError):
            pass
    all_vals = _all_values()
    if all_vals:
        if min(all_vals) < 0:
            return min(all_vals)
        return max(all_vals) or None
    return None


@dataclass(frozen=True)
class Vote:
    """One account's vote on a label."""

    account_id: int | None
    value: int
    date: datetime | None
    who: str | None = None
    """The voter for display (full name, else username, else email), when Gerrit sent accounts."""


@dataclass(frozen=True)
class Revision:
    """One patch set of a change."""

    number: int | None
    created: datetime | None
    committer_date: datetime | None
    first_parent: str | None
    uploader_account_id: int | None = None


def _revision(raw: Mapping[str, Any]) -> Revision:
    number = raw.get("_number")
    commit = raw.get("commit")
    committer_date: datetime | None = None
    first_parent: str | None = None
    if isinstance(commit, dict):
        committer = commit.get("committer")
        if isinstance(committer, dict):
            committer_date = parse_gerrit_time(committer.get("date"))
        parents = commit.get("parents")
        if isinstance(parents, list) and parents:
            first = parents[0]
            first_parent = _sha(first.get("commit")) if isinstance(first, dict) else _sha(first)
    return Revision(
        number=number if isinstance(number, int) else None,
        created=parse_gerrit_time(raw.get("created")),
        committer_date=committer_date,
        first_parent=first_parent,
        uploader_account_id=_account_id(raw.get("uploader")),
    )


def _votes(labels: Mapping[str, Any], label_name: str) -> tuple[Vote, ...]:
    label = labels.get(label_name)
    rows = label.get("all") if isinstance(label, dict) else None
    if not isinstance(rows, list):
        return ()
    out: list[Vote] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = row.get("_account_id")
        value = row.get("value")
        name = _owner_name(row)
        out.append(
            Vote(
                account_id=account_id if isinstance(account_id, int) else None,
                value=value if isinstance(value, int) else 0,
                date=parse_gerrit_time(row.get("date")),
                who=None if name == "?" else name,
            )
        )
    return tuple(out)


def _label_votes(labels: Mapping[str, Any]) -> dict[str, tuple[Vote, ...]]:
    """Every label's non-zero votes, by label name (``DETAILED_LABELS``)."""
    out: dict[str, tuple[Vote, ...]] = {}
    for name in labels:
        votes = tuple(vote for vote in _votes(labels, name) if vote.value)
        if votes:
            out[name] = votes
    return out


def _permitted_votes(raw: object) -> dict[str, tuple[int, ...]]:
    """``permitted_labels`` (``DETAILED_LABELS``): the values you may vote, per label."""
    permitted: dict[str, tuple[int, ...]] = {}
    for label, values in (raw if isinstance(raw, dict) else {}).items():
        numbers = [int(v) for v in values if isinstance(v, str) and v.strip().lstrip("+-").isdigit()]
        permitted[str(label)] = tuple(sorted(numbers))
    return permitted


def _label_meanings(labels: Mapping[str, Any]) -> dict[str, dict[int, str]]:
    """What each value of each label means (``DETAILED_LABELS``' ``values``): ``+1``, "Looks good to me…"."""
    meanings: dict[str, dict[int, str]] = {}
    for label, info in labels.items():
        values = info.get("values") if isinstance(info, dict) else None
        for raw, text in (values if isinstance(values, dict) else {}).items():
            if isinstance(raw, str) and raw.strip().lstrip("+-").isdigit() and isinstance(text, str):
                meanings.setdefault(str(label), {})[int(raw)] = text.strip()
    return meanings


def _attention_set(raw: object) -> dict[int, datetime | None]:
    """``last_update`` per account id, from the key or from the entry's account (``attention_set``
    and ``removed_from_attention_set`` alike)."""
    out: dict[int, datetime | None] = {}
    if not isinstance(raw, dict):
        return out
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        stamp = parse_gerrit_time(entry.get("last_update"))
        account = entry.get("account")
        if isinstance(account, dict) and isinstance(account.get("_account_id"), int):
            out.setdefault(account["_account_id"], stamp)
        if isinstance(key, str) and key.isdigit():
            out[int(key)] = stamp
    return out


def _account_id(account: object) -> int | None:
    raw = account.get("_account_id") if isinstance(account, dict) else None
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _owner_name(owner: object) -> str:
    """Owner for display: full name first, then username, then email."""
    if not isinstance(owner, dict):
        return "?"
    for key in ("name", "username", "email"):
        value = owner.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "?"


@dataclass(frozen=True)
class ChangeView:  # pylint: disable=too-many-instance-attributes
    """A Gerrit ``ChangeInfo``, normalised. Build with :meth:`from_payload`."""

    key: str
    """Gerrit ``id`` (the triplet), or the change number when Gerrit sent no ``id``."""
    number: int | None
    change_id: str
    project: str
    branch: str
    subject: str
    status: str | None
    """Raw Gerrit ``status`` (``NEW``, ``MERGED``, ``ABANDONED``)."""
    updated: str | None
    created: str | None
    verified: int | None
    code_review: int | None
    submittable: bool
    unresolved_comments: int | None
    """``None`` when Gerrit did not send a count (a follow-up must fetch comments)."""
    reviewers: tuple[ReviewerAccount, ...] | None
    """``None`` when the payload had no ``reviewers`` field; empty is an answer."""
    current_revision: str | None
    revisions: Mapping[str, Revision]
    """Patch sets keyed by lower-cased SHA."""
    owner_name: str
    owner_email: str | None
    code_review_votes: tuple[Vote, ...]
    label_votes: Mapping[str, tuple[Vote, ...]] = field(default_factory=dict)
    """Non-zero votes per label: who voted what on the current patch set."""
    revision_info: bool = False
    """Gerrit sent ``current_revision`` and a ``revisions`` map (``CURRENT_REVISION`` asked)."""
    attention_set: Mapping[int, datetime | None] = field(default_factory=dict)
    """Gerrit's attention set: the accounts whose turn it is, each since when."""
    removed_from_attention_set: Mapping[int, datetime | None] = field(default_factory=dict)
    """The accounts Gerrit took out of the attention set, each when it last did."""
    owner_username: str | None = None
    """The owner's Gerrit username, when Gerrit sent it (``DETAILED_ACCOUNTS``)."""
    owner_account_id: int | None = None
    """The owner's account id: :meth:`owned_by` compares it with yours."""
    freshness: str | None = None
    work_in_progress: bool = False
    private: bool = False
    topic: str | None = None
    permitted_votes: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    """What you may vote, per label (``permitted_labels``); empty when Gerrit did not say."""
    label_meanings: Mapping[str, Mapping[int, str]] = field(default_factory=dict)
    """What each vote of a label means, as the project words it; empty when Gerrit did not say."""

    def contradicts(self, parsed: Changeish) -> str | None:
        """Why this change is not the one *parsed* addressed, or ``None`` when it is."""
        if parsed.kind == "url" and parsed.project and self.project and parsed.project != self.project:
            return f"{parsed.raw!r} names project {parsed.project!r}, but change {self.number} is in {self.project!r}"
        patchsets = [revision.number for revision in self.revisions.values() if revision.number is not None]
        if parsed.patchset and patchsets and parsed.patchset > max(patchsets):
            return f"change {self.number} has no patch set {parsed.patchset}"
        return None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ChangeView:
        """Parse one ChangeInfo dict. Never raises on a missing or malformed field."""
        labels = payload.get("labels")
        labels = labels if isinstance(labels, dict) else {}
        raw_revisions = payload.get("revisions")
        revisions: dict[str, Revision] = {}
        if isinstance(raw_revisions, dict):
            for sha, raw in raw_revisions.items():
                key = _sha(sha)
                if key and isinstance(raw, dict):
                    revisions[key] = _revision(raw)
        current = _sha(payload.get("current_revision"))
        number = payload.get("_number")
        raw_id = payload.get("id")
        owner = payload.get("owner")
        email = owner.get("email") if isinstance(owner, dict) else None
        comments = payload.get("unresolved_comment_count")
        return cls(
            key=raw_id if isinstance(raw_id, str) and raw_id else (str(number) if isinstance(number, int) else ""),
            number=number if isinstance(number, int) else None,
            change_id=_str(payload.get("change_id")) or "",
            project=_str(payload.get("project")) or "",
            branch=_str(payload.get("branch")) or "",
            subject=_str(payload.get("subject")) or "",
            status=_str(payload.get("status")),
            updated=_str(payload.get("updated")),
            created=_str(payload.get("created")),
            verified=_label_value(labels, "Verified"),
            code_review=_label_value(labels, "Code-Review"),
            submittable=bool(payload.get("submittable")),
            unresolved_comments=comments if isinstance(comments, int) else None,
            reviewers=tuple(reviewer_accounts_from_change_info(payload)) if "reviewers" in payload else None,
            current_revision=current,
            revisions=revisions,
            owner_name=_owner_name(owner),
            owner_email=email.strip() if isinstance(email, str) and email.strip() else None,
            code_review_votes=_votes(labels, "Code-Review"),
            label_votes=_label_votes(labels),
            revision_info=isinstance(payload.get("current_revision"), str) and isinstance(raw_revisions, dict),
            attention_set=_attention_set(payload.get("attention_set")),
            removed_from_attention_set=_attention_set(payload.get("removed_from_attention_set")),
            freshness=change_freshness_key(payload),
            owner_username=_str(owner.get("username")) if isinstance(owner, dict) else None,
            owner_account_id=_account_id(owner),
            work_in_progress=payload.get("work_in_progress") is True,
            private=payload.get("is_private") is True,
            topic=_str(payload.get("topic")) or None,
            permitted_votes=_permitted_votes(payload.get("permitted_labels")),
            label_meanings=_label_meanings(labels),
        )

    def updated_by(self, account_id: int | None) -> bool:
        """Whether *account_id* made the change's last update, as far as the ChangeInfo tells:
        a patchset it uploaded, or a vote it gave, at that moment. A comment alone does not tell.
        """
        updated = parse_gerrit_time(self.updated)
        if account_id is None or updated is None:
            return False
        moments = [rev.created for rev in self.revisions.values() if rev.uploader_account_id == account_id]
        votes = (*self.code_review_votes, *(vote for votes in self.label_votes.values() for vote in votes))
        moments += [vote.date for vote in votes if vote.account_id == account_id]
        return any(moment is not None and abs((updated - moment).total_seconds()) < 1 for moment in moments)

    def owned_by(self, account_id: int | None) -> bool:
        """Whether the account *account_id* (``GerritService.self_account_id``: you) owns this change."""
        return account_id is not None and self.owner_account_id == account_id

    @property
    def is_open(self) -> bool:
        """Still the kind the inbox lists (``NEW``, or no status at all)."""
        return not self.status or not self.status.strip() or self.status.strip().upper() == "NEW"

    @property
    def current(self) -> Revision | None:
        """The current patch set, when Gerrit sent it."""
        return self.revisions.get(self.current_revision) if self.current_revision else None

    @property
    def current_revision_number(self) -> int | None:
        """Patch-set number of ``current_revision``, if known."""
        current = self.current
        return current.number if current is not None else None

    @property
    def first_parent(self) -> str | None:
        """First parent of the current commit (Gerrit relation-chain parent)."""
        current = self.current
        return current.first_parent if current is not None else None

    def current_revision_created(self) -> datetime | None:
        """When the current patch set was uploaded, falling back to commit and change dates."""
        if not self.revision_info:
            return parse_gerrit_time(self.updated)
        current = self.current
        if current is None:
            return parse_gerrit_time(self.created)
        return current.created or current.committer_date or parse_gerrit_time(self.created)

    def url(self, web_base: str) -> str | None:
        """The change page URL, or ``None`` without project and number."""
        if not self.project or self.number is None:
            return None
        return f"{web_base}/c/{quote(self.project, safe='')}/+/{self.number}"


def missing_parent_shas(changes: list[ChangeView]) -> list[str]:
    """First-parent SHAs that are not another change's current revision."""
    known = {change.current_revision for change in changes if change.current_revision}
    parents = (change.first_parent for change in changes if change.first_parent)
    return list(dict.fromkeys(parent for parent in parents if parent not in known))
