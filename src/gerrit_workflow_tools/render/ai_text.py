"""``--ai`` text for ``ger log`` and ``ger inbox``: one plain line per item, for a model to read.

The words are the human view's (``status_fmt``): the same hints, the same vote tokens. A fact is
written only when it has a value, a change is named by its number, and nothing is coloured,
linked or padded.
"""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.inbox import OUTGOING, InboxSection
from gerrit_workflow_tools.core.review_chain import ReviewChain, format_age
from gerrit_workflow_tools.render.status_fmt import attention_hints, code_review_token, member_hints, verified_token


def vote_words(verified: int | None, code_review: int | None) -> list[str]:
    """``V+1``, ``CR+2``: the human view's vote tokens, for the votes that were cast (not 0, not none)."""
    words = [verified_token(verified)] if verified else []
    if code_review:
        words.append(code_review_token(code_review))
    return [word.strip().upper() for word in words]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _item(head: str, facts: Sequence[str]) -> str:
    return f"{head} — {', '.join(facts)}" if facts else head


def commit_facts(commit: LogCommit) -> list[str]:
    """What one annotated commit carries: change number, votes, ``ger log``'s hints (CI names with a failure)."""
    facts = [f"change {commit.number}"] if commit.number else []
    if commit.patchset_status == PatchsetStatus.MERGED_SAME:
        facts.append("merged")
    facts += vote_words(commit.verified, commit.code_review)
    for key, text in attention_hints(commit):
        failures = commit.ci_failures if key == Attention.CI_FAILED else []
        facts.append(f"{text} ({', '.join(failures)})" if failures else text)
    return facts


def stack_ai_lines(
    commits: Sequence[LogCommit], *, project: str, branch: str, ready: int, next_lines: Sequence[str]
) -> list[str]:
    """``ger log --ai``: the stack in one line, a numbered line per commit (oldest first), then the next steps.

    *ready* is how many commits, from the first, sit below the **ready boundary**.
    """
    sends = {0: "nothing", 1: "1"}.get(ready, f"1-{ready}")
    lines = [f"stack: {project}, branch {branch}, {_plural(len(commits), 'commit')}, ger push sends {sends}"]
    lines += [_item(f"{i}. {c.short_sha} {c.summary}", commit_facts(c)) for i, c in enumerate(commits, 1)]
    return [*lines, *next_lines]


def _chain_facts(chain: ReviewChain, *, outgoing: bool) -> list[str]:
    facts = [_plural(chain.depth, "change")] if chain.depth > 1 else []
    if not outgoing:
        facts += [f"by {chain.owner_name}", f"waiting on you {format_age(chain.unreviewed_age_seconds)}"]
    facts.append(f"last activity {format_age(chain.wait_age_seconds)}")
    facts += vote_words(chain.verified, chain.code_review)
    for member in chain.members:
        hints = member_hints(member.attention_reasons, member.comments_unresolved)
        if hints:
            facts.append(f"{member.number}: {' and '.join(text for _key, text in hints)}")
    return facts


def inbox_ai_lines(sections: Sequence[InboxSection], *, projects: Sequence[str]) -> list[str]:
    """``ger inbox --ai``: the project scope, then per section a count line and a line per chain."""
    lines = [f"project: {', '.join(projects) if projects else 'all projects'}"]
    for section in sections:
        name = section.name.replace("-", " ")
        chains = section.chains
        changes = sum(chain.depth for chain in chains)
        count = f"{_plural(len(chains), 'chain')}, {_plural(changes, 'change')}" if chains else "none"
        lines.append(f"{name}: {count}")
        outgoing = section.name == OUTGOING
        lines += [_item(f"change {c.top.number} {c.top.subject}", _chain_facts(c, outgoing=outgoing)) for c in chains]
    return lines
