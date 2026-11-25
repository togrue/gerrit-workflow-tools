"""The relation graph of a **review chain**: which changes link up along current-revision
first-parent SHAs, in what order, and which one is the **chain top**.

Pure functions over :class:`ChangeView` values keyed by Gerrit id; :mod:`review_chain`
builds the chains from what they return.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView


@dataclass(frozen=True)
class ChainComponent:
    """One connected set of changes: their ids base to top, and the chain top's id."""

    ordered: list[str]
    top: str


def chain_components(payloads: Mapping[str, ChangeView]) -> list[ChainComponent]:
    """*payloads* grouped into connected components along first-parent links, each ordered base
    to top (siblings by change number) with its chain top."""
    sha_to_id = {change.current_revision: key for key, change in payloads.items() if change.current_revision}
    union = _UnionFind()
    for member_id in payloads:
        union.add(member_id)
    for member_id, change in payloads.items():
        parent_id = _parent_id(change, sha_to_id)
        if parent_id:
            union.union(member_id, parent_id)
    groups: dict[str, set[str]] = defaultdict(set)
    for member_id in payloads:
        groups[union.find(member_id)].add(member_id)
    components: list[ChainComponent] = []
    for component in groups.values():
        ordered = _order_members(component, payloads, sha_to_id)
        components.append(ChainComponent(ordered, _pick_top(ordered, component, payloads, sha_to_id)))
    return components


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


def _parent_id(change: ChangeView, sha_to_id: dict[str, str]) -> str | None:
    return sha_to_id.get(change.first_parent) if change.first_parent else None


def _order_members(
    component: set[str],
    payloads: Mapping[str, ChangeView],
    sha_to_id: dict[str, str],
) -> list[str]:
    """Base-to-top order along first-parent links; siblings by change number."""
    children: dict[str, list[str]] = defaultdict(list)
    bases: list[str] = []
    for member_id in component:
        parent_id = _parent_id(payloads[member_id], sha_to_id)
        if parent_id and parent_id in component:
            children[parent_id].append(member_id)
        else:
            bases.append(member_id)
    for child_ids in children.values():
        child_ids.sort(key=lambda item: payloads[item].number or 0)
    bases.sort(key=lambda item: payloads[item].number or 0)
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
    for member_id in sorted(component, key=lambda item: payloads[item].number or 0):
        _walk(member_id)
    return ordered


def _pick_top(
    ordered_ids: list[str],
    component: set[str],
    payloads: Mapping[str, ChangeView],
    sha_to_id: dict[str, str],
) -> str:
    """Member whose current revision is not the parent of any other member."""
    parent_ids = {_parent_id(payloads[member_id], sha_to_id) for member_id in component}
    tips = [member_id for member_id in ordered_ids if member_id not in parent_ids]
    if not tips:
        return ordered_ids[-1]
    return max(tips, key=lambda item: payloads[item].number or 0)
