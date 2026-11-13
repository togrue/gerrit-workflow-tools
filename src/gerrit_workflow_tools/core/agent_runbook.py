"""The **agent runbook**: the attention walk laid out as work an agent can do unattended.

One **stop** per local stack commit that needs edit attention, oldest first: the comment chains
to address and the **CI findings** to fix there. Two things are kept away from the agent:

* a **hold** — a comment chain you keep for yourself, because it needs a decision, not a fix;
* findings a commit merely inherits: they are fixed where they were introduced, further down.

Holds live in ``<git dir>/ger/agent-hold.json``. They are yours and local: not Gerrit state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.attention import commit_needs_edit_attention
from gerrit_workflow_tools.core.ci_content import CiContent, CiFinding, introduced_fingerprints
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit
from gerrit_workflow_tools.core.gerrit_show import ShownCommit
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.git_state import git_dir


_HOLD_FILE = ("ger", "agent-hold.json")


def _hold_path(cwd: Path | str) -> Path:
    repo_dir = git_dir(cwd)
    if repo_dir is None:
        raise GitError("not inside a git repository")
    return repo_dir.joinpath(*_HOLD_FILE)


def load_holds(cwd: Path | str) -> frozenset[str]:
    """Root ids of the comment chains on hold. An unreadable file holds nothing."""
    try:
        raw = json.loads(_hold_path(cwd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else frozenset()


def set_hold(cwd: Path | str, root_id: str, held: bool) -> frozenset[str]:
    """Put the chain *root_id* on hold, or release it; return the holds now in force."""
    holds = set(load_holds(cwd))
    (holds.add if held else holds.discard)(root_id)
    path = _hold_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(holds), indent=2) + "\n", encoding="utf-8")
    return frozenset(holds)


@dataclass(frozen=True)
class RunbookStop:
    """One commit of the walk, with what there is to do on it and for whom."""

    commit: LogCommit
    for_agent: list[CommentChain] = field(default_factory=list)
    """Unresolved Gerrit comment chains the agent addresses."""
    held: list[CommentChain] = field(default_factory=list)
    """Unresolved chains on hold: left for you."""
    to_fix: list[CiFinding] = field(default_factory=list)
    """Findings to fix on this commit: introduced here, or of unknown origin."""
    inherited: list[CiFinding] = field(default_factory=list)
    """Findings the parent already has: not this commit's to fix."""
    origin_known: bool = True
    """False when it could not be told which findings this commit introduced."""
    unread: list[CiContent] = field(default_factory=list)
    """Failed CI links whose content could not be fetched."""

    @property
    def has_agent_work(self) -> bool:
        """There is something here an agent can act on."""
        return bool(self.for_agent or self.to_fix or self.unread)


def plan_runbook(
    shown: Sequence[ShownCommit], holds: frozenset[str] = frozenset(), *, only: str | None = None
) -> list[RunbookStop]:
    """The stops of the walk over *shown* (the local stack, oldest first, with CI content loaded).

    *only* (a commit id prefix or a Change-Id) narrows the walk to one commit; introduced
    findings are still worked out against the whole stack.
    """
    introduced = introduced_fingerprints([item.ci_verdict_contents for item in shown])
    wanted = only.lower() if only else None
    stops: list[RunbookStop] = []
    for item, new in zip(shown, introduced, strict=True):
        commit = item.commit
        if not commit_needs_edit_attention(commit):
            continue
        if wanted and not (commit.sha.startswith(wanted) or (commit.change_id or "").lower() == wanted):
            continue
        open_chains = [chain for chain in item.chains if not chain.resolved and not chain.local]
        contents = item.ci_contents or []
        findings = [finding for content in contents if not content.failed for finding in content.leading_findings]
        stops.append(
            RunbookStop(
                commit=commit,
                for_agent=[chain for chain in open_chains if chain.root_id not in holds],
                held=[chain for chain in open_chains if chain.root_id in holds],
                to_fix=[f for f in findings if new is None or f.fingerprint in new],
                inherited=[f for f in findings if new is not None and f.fingerprint not in new],
                origin_known=new is not None,
                unread=[content for content in contents if content.failed],
            )
        )
    return stops
