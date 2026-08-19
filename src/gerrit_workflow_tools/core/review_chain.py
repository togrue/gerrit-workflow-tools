"""Review chains — Gerrit's relation chain, assembled from ChangeInfo payloads.

A **review chain** is a maximal set of open changes linked by current-revision
parent/child SHAs. Distinct from the **local stack**: this is derived from a
query result and never looks at local commits.

Pure functions over payloads, so unit tests drive them through ``ChangeStore``
rows without a host or a clone.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from gerrit_workflow_tools.core.gerrit_change_status import extract_label_value, gerrit_change_url

# Query options that give parent SHAs, votes (with dates), and owner names.
INBOX_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "CURRENT_REVISION",
    "CURRENT_COMMIT",
    "DETAILED_ACCOUNTS",
    "SUBMITTABLE",
)

_OPEN_STATUSES = frozenset({"NEW", ""})


@dataclass(frozen=True)
class ChainMember:  # pylint: disable=too-many-instance-attributes
    """One open change in a review chain, oldest (base) to newest (top)."""

    number: int
    change_id: str
    subject: str
    project: str
    branch: str
    owner_name: str
    owner_email: str | None
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    updated: datetime | None
    url: str | None
    attention_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ReviewChain:  # pylint: disable=too-many-instance-attributes
    """One review unit: the chain you decide to start or not start."""

    key: str
    top: ChainMember
    members: tuple[ChainMember, ...]
    depth: int
    owner_name: str
    owner_email: str | None
    project: str
    branch: str
    wait_age_seconds: int
    unreviewed_age_seconds: int
    last_activity: datetime | None
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    attention_reasons: tuple[str, ...]
    partial_chain: bool
    url: str | None


def parse_gerrit_time(raw: str | None) -> datetime | None:
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


def format_age(seconds: int) -> str:
    """Compact age for the inbox: ``12m``, ``4h``, ``3d``."""
    age = max(0, int(seconds))
    minutes = age // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def host_from_web_base(web_base: str) -> str:
    """Hostname of a Gerrit web base, used as the chain-key prefix."""
    parsed = urlparse(web_base.rstrip("/"))
    host = parsed.netloc or parsed.path
    return host.split("@", 1)[-1].split(":", 1)[0] or "unknown"


def current_revision_sha(payload: dict[str, Any]) -> str | None:
    """Current revision SHA, lowercased, or ``None`` when Gerrit omitted it."""
    raw = payload.get("current_revision")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return None


def first_parent_sha(payload: dict[str, Any]) -> str | None:  # pylint: disable=too-many-return-statements
    """First parent of the current commit (Gerrit relation-chain parent)."""
    current = payload.get("current_revision")
    revisions = payload.get("revisions")
    if not isinstance(current, str) or not isinstance(revisions, dict):
        return None
    revision = revisions.get(current)
    if not isinstance(revision, dict):
        lowered = current.lower()
        revision = next(
            (row for key, row in revisions.items() if isinstance(key, str) and key.lower() == lowered),
            None,
        )
    if not isinstance(revision, dict):
        return None
    commit = revision.get("commit")
    if not isinstance(commit, dict):
        return None
    parents = commit.get("parents")
    if not isinstance(parents, list) or not parents:
        return None
    first = parents[0]
    if isinstance(first, str) and first.strip():
        return first.strip().lower()
    if isinstance(first, dict):
        sha = first.get("commit")
        if isinstance(sha, str) and sha.strip():
            return sha.strip().lower()
    return None


def current_revision_created(payload: dict[str, Any]) -> datetime | None:
    """When the current patch set was uploaded."""
    current = payload.get("current_revision")
    revisions = payload.get("revisions")
    if not isinstance(current, str) or not isinstance(revisions, dict):
        return parse_gerrit_time(payload.get("updated") if isinstance(payload.get("updated"), str) else None)
    revision = revisions.get(current)
    if not isinstance(revision, dict):
        return parse_gerrit_time(payload.get("created") if isinstance(payload.get("created"), str) else None)
    created = parse_gerrit_time(revision.get("created") if isinstance(revision.get("created"), str) else None)
    if created is not None:
        return created
    commit = revision.get("commit")
    if isinstance(commit, dict):
        committer = commit.get("committer")
        if isinstance(committer, dict):
            return parse_gerrit_time(committer.get("date") if isinstance(committer.get("date"), str) else None)
    return parse_gerrit_time(payload.get("created") if isinstance(payload.get("created"), str) else None)


def is_open_change(payload: dict[str, Any]) -> bool:
    """True when the change is still the kind inbox lists (open / NEW)."""
    raw = payload.get("status")
    if not isinstance(raw, str) or not raw.strip():
        return True
    return raw.strip().upper() in _OPEN_STATUSES


def missing_parent_shas(payloads: list[dict[str, Any]]) -> list[str]:
    """First-parent SHAs that are not another payload's current revision."""
    known: set[str] = set()
    for payload in payloads:
        sha = current_revision_sha(payload)
        if sha:
            known.add(sha)
    missing: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        parent = first_parent_sha(payload)
        if parent and parent not in known and parent not in seen:
            seen.add(parent)
            missing.append(parent)
    return missing


def _payload_id(payload: dict[str, Any]) -> str:
    raw = payload.get("id")
    if isinstance(raw, str) and raw:
        return raw
    number = payload.get("_number")
    return str(number) if isinstance(number, int) else ""


def _owner_name(payload: dict[str, Any]) -> str:
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        return "?"
    for key in ("name", "username", "email"):
        value = owner.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "?"


def _owner_email(payload: dict[str, Any]) -> str | None:
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        return None
    email = owner.get("email")
    return email.strip() if isinstance(email, str) and email.strip() else None


def _self_vote_date(payload: dict[str, Any], self_account_id: int | None) -> datetime | None:
    if self_account_id is None:
        return None
    labels = payload.get("labels")
    if not isinstance(labels, dict):
        return None
    code_review = labels.get("Code-Review")
    if not isinstance(code_review, dict):
        return None
    votes = code_review.get("all")
    if not isinstance(votes, list):
        return None
    latest: datetime | None = None
    for vote in votes:
        if not isinstance(vote, dict) or vote.get("_account_id") != self_account_id:
            continue
        stamp = parse_gerrit_time(vote.get("date") if isinstance(vote.get("date"), str) else None)
        if stamp is not None and (latest is None or stamp > latest):
            latest = stamp
    return latest


def _attention_last_update(payload: dict[str, Any], self_account_id: int | None) -> datetime | None:
    if self_account_id is None:
        return None
    attention = payload.get("attention_set")
    if not isinstance(attention, dict):
        return None
    entry = attention.get(str(self_account_id))
    if not isinstance(entry, dict):
        for value in attention.values():
            if not isinstance(value, dict):
                continue
            account = value.get("account")
            if isinstance(account, dict) and account.get("_account_id") == self_account_id:
                entry = value
                break
        else:
            return None
    raw = entry.get("last_update")
    return parse_gerrit_time(raw if isinstance(raw, str) else None)


def member_unreviewed_since(payload: dict[str, Any], self_account_id: int | None) -> datetime | None:
    """When this change last started waiting for *self*'s review, or ``None`` if current.

    Prefers the attention-set clock (Gerrit's "your turn"). Falls back to "current
    patch set uploaded after my last Code-Review vote", then to change creation
    when I have never voted.
    """
    attention_at = _attention_last_update(payload, self_account_id)
    if attention_at is not None:
        return attention_at
    last_vote = _self_vote_date(payload, self_account_id)
    patch_created = current_revision_created(payload)
    if last_vote is None:
        created = parse_gerrit_time(payload.get("created") if isinstance(payload.get("created"), str) else None)
        return patch_created or created
    if patch_created is not None and last_vote < patch_created:
        return patch_created
    return None


def _labels(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("labels")
    return raw if isinstance(raw, dict) else {}


def member_attention_reasons(payload: dict[str, Any]) -> tuple[str, ...]:
    """Inbox member reasons — CI, comments, negative vote. No local patchset states."""
    labels = _labels(payload)
    verified = extract_label_value(labels, "Verified")
    code_review = extract_label_value(labels, "Code-Review")
    raw_comments = payload.get("unresolved_comment_count")
    comments = raw_comments if isinstance(raw_comments, int) else 0
    reasons: list[str] = []
    if verified is not None and verified <= -1:
        reasons.append("ci-failed")
    if comments > 0:
        reasons.append("unresolved-comments")
    if code_review is not None and code_review < 0:
        reasons.append("review-issues")
    return tuple(reasons)


def _worst_label(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _to_member(payload: dict[str, Any], web_base: str) -> ChainMember | None:
    number = payload.get("_number")
    if not isinstance(number, int):
        return None
    labels = _labels(payload)
    raw_comments = payload.get("unresolved_comment_count")
    subject = payload.get("subject")
    change_id = payload.get("change_id")
    project = payload.get("project")
    branch = payload.get("branch")
    return ChainMember(
        number=number,
        change_id=change_id if isinstance(change_id, str) else "",
        subject=subject if isinstance(subject, str) else "",
        project=project if isinstance(project, str) else "",
        branch=branch if isinstance(branch, str) else "",
        owner_name=_owner_name(payload),
        owner_email=_owner_email(payload),
        verified=extract_label_value(labels, "Verified"),
        code_review=extract_label_value(labels, "Code-Review"),
        comments_unresolved=raw_comments if isinstance(raw_comments, int) else 0,
        updated=parse_gerrit_time(payload.get("updated") if isinstance(payload.get("updated"), str) else None),
        url=gerrit_change_url(web_base, payload),
        attention_reasons=member_attention_reasons(payload),
    )


def _index_payloads(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Key payloads by Gerrit ``id``; last write wins for a duplicate id."""
    out: dict[str, dict[str, Any]] = {}
    for payload in rows:
        key = _payload_id(payload)
        if key:
            out[key] = payload
    return out


class _UnionFind:
    """Disjoint-set of change ids used while grouping a query result into chains."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        """Ensure *item* is a set of its own."""
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        """Return the representative for *item*'s set."""
        parent = self._parent[item]
        if parent != item:
            parent = self.find(parent)
            self._parent[item] = parent
        return parent

    def union(self, left: str, right: str) -> None:
        """Merge the sets containing *left* and *right*."""
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self._parent[root_right] = root_left


def _order_members(
    component: set[str],
    payloads: dict[str, dict[str, Any]],
    sha_to_id: dict[str, str],
) -> list[str]:
    """Base-to-top order along first-parent links; siblings by change number."""
    children: dict[str, list[str]] = defaultdict(list)
    bases: list[str] = []
    for member_id in component:
        parent_sha = first_parent_sha(payloads[member_id])
        parent_id = sha_to_id.get(parent_sha) if parent_sha else None
        if parent_id and parent_id in component:
            children[parent_id].append(member_id)
        else:
            bases.append(member_id)
    for child_ids in children.values():
        child_ids.sort(key=lambda item: payloads[item].get("_number") or 0)
    bases.sort(key=lambda item: payloads[item].get("_number") or 0)
    ordered: list[str] = []
    seen: set[str] = set()

    def _walk(member_id: str) -> None:
        if member_id in seen:
            return
        seen.add(member_id)
        ordered.append(member_id)
        for child_id in children.get(member_id, []):
            _walk(child_id)

    for base_id in bases:
        _walk(base_id)
    for member_id in sorted(component, key=lambda item: payloads[item].get("_number") or 0):
        _walk(member_id)
    return ordered


def _pick_top(
    ordered_ids: list[str],
    component: set[str],
    payloads: dict[str, dict[str, Any]],
    sha_to_id: dict[str, str],
) -> str:
    """Member whose current revision is not the parent of any other member."""
    parent_ids: set[str] = set()
    for member_id in component:
        parent_sha = first_parent_sha(payloads[member_id])
        parent_id = sha_to_id.get(parent_sha) if parent_sha else None
        if parent_id and parent_id in component:
            parent_ids.add(parent_id)
    tips = [member_id for member_id in ordered_ids if member_id not in parent_ids]
    if not tips:
        return ordered_ids[-1]
    return max(tips, key=lambda item: payloads[item].get("_number") or 0)


def _seconds_since(moment: datetime | None, now: datetime) -> int:
    """Seconds from *moment* to *now*, floored at zero. Missing *moment* is zero."""
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds()))


def _chain_from_component(  # pylint: disable=too-many-arguments,too-many-locals
    component: set[str],
    payloads: dict[str, dict[str, Any]],
    sha_to_id: dict[str, str],
    *,
    web_base: str,
    host: str,
    now: datetime,
    self_account_id: int | None,
    unmatched: set[str],
) -> ReviewChain | None:
    """Build one ReviewChain from a connected component, or None if it has no numbers."""
    ordered_ids = _order_members(component, payloads, sha_to_id)
    members: list[ChainMember] = []
    for member_id in ordered_ids:
        member = _to_member(payloads[member_id], web_base)
        if member is not None:
            members.append(member)
    if not members:
        return None
    top_id = _pick_top(ordered_ids, component, payloads, sha_to_id)
    top_number = payloads[top_id].get("_number")
    top_member = next((member for member in members if member.number == top_number), members[-1])
    last_activity = max((member.updated for member in members if member.updated is not None), default=None)
    unreviewed_at: datetime | None = None
    for member_id in ordered_ids:
        stamp = member_unreviewed_since(payloads[member_id], self_account_id)
        if stamp is not None and (unreviewed_at is None or stamp < unreviewed_at):
            unreviewed_at = stamp
    attention: list[str] = []
    seen_reasons: set[str] = set()
    for member in members:
        for reason in member.attention_reasons:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                attention.append(reason)
    partial = False
    for member_id in component:
        parent_sha = first_parent_sha(payloads[member_id])
        if parent_sha and parent_sha in unmatched:
            partial = True
            break
    project = top_member.project
    return ReviewChain(
        key=f"{host}~{project}~{top_member.number}",
        top=top_member,
        members=tuple(members),
        depth=len(members),
        owner_name=top_member.owner_name,
        owner_email=top_member.owner_email,
        project=project,
        branch=top_member.branch,
        wait_age_seconds=_seconds_since(last_activity, now),
        unreviewed_age_seconds=_seconds_since(unreviewed_at, now) if unreviewed_at is not None else 0,
        last_activity=last_activity,
        verified=_worst_label([member.verified for member in members]),
        code_review=_worst_label([member.code_review for member in members]),
        comments_unresolved=sum(member.comments_unresolved for member in members),
        attention_reasons=tuple(attention),
        partial_chain=partial,
        url=top_member.url,
    )


def assemble_review_chains(  # pylint: disable=too-many-arguments,too-many-locals
    queried: list[dict[str, Any]],
    follow_up: list[dict[str, Any]],
    *,
    web_base: str,
    now: datetime,
    self_account_id: int | None,
    follow_up_unmatched: set[str] | None = None,
) -> list[ReviewChain]:
    """Assemble review chains from a query result plus optional parent follow-up rows.

    *queried* members seed chains even when you are not a reviewer on every
    follow-up row — those extra open changes are context. Merged/abandoned
    follow-up rows are ignored (they are the ground the chain sits on).
    *follow_up_unmatched* are parent SHAs that a follow-up returned for a
    non-current revision: the chain is then flagged ``partial_chain``.
    """
    queried_ids = {_payload_id(row) for row in queried if _payload_id(row)}
    open_follow_up = [row for row in follow_up if is_open_change(row) and _payload_id(row) not in queried_ids]
    payloads = _index_payloads(list(queried) + open_follow_up)
    if not payloads:
        return []

    sha_to_id: dict[str, str] = {}
    for member_id, payload in payloads.items():
        sha = current_revision_sha(payload)
        if sha:
            sha_to_id[sha] = member_id

    union = _UnionFind()
    for member_id in payloads:
        union.add(member_id)
    for member_id, payload in payloads.items():
        parent_sha = first_parent_sha(payload)
        parent_id = sha_to_id.get(parent_sha) if parent_sha else None
        if parent_id:
            union.union(member_id, parent_id)

    groups: dict[str, set[str]] = defaultdict(set)
    for member_id in payloads:
        groups[union.find(member_id)].add(member_id)

    unmatched = follow_up_unmatched or set()
    host = host_from_web_base(web_base)
    chains: list[ReviewChain] = []
    for component in groups.values():
        chain = _chain_from_component(
            component,
            payloads,
            sha_to_id,
            web_base=web_base,
            host=host,
            now=now,
            self_account_id=self_account_id,
            unmatched=unmatched,
        )
        if chain is not None:
            chains.append(chain)
    chains.sort(key=lambda chain: (-chain.unreviewed_age_seconds, -chain.wait_age_seconds, chain.top.number))
    return chains
