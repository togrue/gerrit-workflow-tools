"""``--ai`` text for ``ger log`` and ``ger inbox``: one plain line per item, for a model to read.

The words are the human view's (``status_fmt``): the same hints, the same vote tokens. A fact is
written only when it has a value, a change is named by its number, and nothing is coloured,
linked or padded.
"""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.inbox import OUTGOING, InboxSection
from gerrit_workflow_tools.core.review_chain import ReviewChain, format_age
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.status_fmt import (
    AUTHOR,
    REVIEWER,
    SECTION_HEADINGS,
    attention_hints,
    code_review_token,
    member_hints,
    verified_token,
)


def vote_words(verified: int | None, code_review: int | None) -> list[str]:
    """``V+1``, ``CR+2``: the human view's vote tokens, for the votes that were cast (not 0, not none)."""
    words = [verified_token(verified)] if verified else []
    if code_review:
        words.append(code_review_token(code_review))
    return [word.strip().upper() for word in words]


def _item(head: str, facts: Sequence[str]) -> str:
    return f"{head} — {', '.join(facts)}" if facts else head


def commit_facts(commit: LogCommit) -> list[str]:
    """What one annotated commit carries: change number, votes, ``ger log``'s hints (CI names with a failure)."""
    facts = [f"change {commit.number}"] if commit.number else []
    facts += vote_words(commit.verified, commit.code_review)
    for key, text in attention_hints(commit):
        failures = commit.ci_failures if key == Attention.CI_FAILED else []
        facts.append(f"{text} ({', '.join(failures)})" if failures else text)
    return facts


def _uploads(commits: Sequence[LogCommit], upload: Sequence[LogCommit]) -> str:
    """``ger push uploads 2: commits 4, 5``, by their line numbers; ``uploads nothing`` with none."""
    if not upload:
        return "ger push uploads nothing"
    shas = {c.sha for c in upload}
    numbers = ", ".join(str(i) for i, c in enumerate(commits, 1) if c.sha in shas)
    return f"ger push uploads {len(upload)}: {'commit' if len(upload) == 1 else 'commits'} {numbers}"


def stack_ai_lines(
    commits: Sequence[LogCommit],
    *,
    project: str,
    branch: str,
    upload: Sequence[LogCommit],
    next_lines: Sequence[str],
) -> list[str]:
    """``ger log --ai``: the stack in one line, a numbered line per commit (oldest first), then the next steps.

    *upload* is what ``ger push`` would upload (``StackOverview.to_upload``).
    """
    lines = [f"stack: {project}, branch {branch}, {counted(len(commits), 'commit')}, {_uploads(commits, upload)}"]
    lines += [_item(f"{i}. {c.short_sha} {c.summary}", commit_facts(c)) for i, c in enumerate(commits, 1)]
    return [*lines, *next_lines]


def _chain_facts(chain: ReviewChain, *, outgoing: bool) -> list[str]:
    facts = [counted(chain.depth, "change")] if chain.depth > 1 else []
    if not outgoing:
        turn = (
            f"waiting on you {format_age(chain.unreviewed_age_seconds)}" if chain.your_turn else "waits on the author"
        )
        facts += [f"by {chain.owner_name}", turn]
    facts.append(f"last activity {format_age(chain.wait_age_seconds)}")
    facts += vote_words(chain.verified_tally.cast, chain.code_review_tally.cast)
    facts += [
        f"{label} missing on {tally.missing}"
        for label, tally in (("V", chain.verified_tally), ("CR", chain.code_review_tally))
        if 0 < tally.missing < chain.depth
    ]
    for member in chain.members:
        hints = member_hints(member, side=REVIEWER if outgoing else AUTHOR)
        if hints:
            facts.append(f"{member.number}: {' and '.join(text for _key, text in hints)}")
    return facts


def inbox_ai_lines(sections: Sequence[InboxSection], *, projects: Sequence[str]) -> list[str]:
    """``ger inbox --ai``: the project scope, then per section a count line and a line per chain."""
    lines = [f"project: {', '.join(projects) if projects else 'all projects'}"]
    for section in sections:
        name = SECTION_HEADINGS[section.name]
        chains = section.chains
        changes = sum(chain.depth for chain in chains)
        count = f"{counted(len(chains), 'chain')}, {counted(changes, 'change')}" if chains else "none"
        if waiting := len(section.waiting_on_author):
            count += f", {waiting} of them wait{'s' if waiting == 1 else ''} on the author"
        lines.append(f"{name}: {count}")
        outgoing = section.name == OUTGOING
        lines += [_item(f"change {c.top.number} {c.top.subject}", _chain_facts(c, outgoing=outgoing)) for c in chains]
    return lines
