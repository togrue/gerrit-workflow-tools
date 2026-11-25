"""Projections of **CI content**: JSON (the agent contract), human text, and Markdown.

Findings are grouped by **CI link** label. Plain text only — the caller adds color.

Human text and Markdown list a link's leading findings, then its collapsed warnings as one
count line unless the caller asks for them. JSON always carries every finding.
"""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiContent, CiFinding
from gerrit_workflow_tools.core.words import counted


def ci_content_json(content: CiContent, *, include_body: bool = False) -> dict[str, object]:
    """One link's **CI content** as JSON: link, provenance, findings, and the body when asked."""
    payload: dict[str, object] = {
        "label": content.link.label,
        "url": content.link.url,
        "build_url": content.link.build_url,
        "source": content.link.source,
        "verdict": content.link.verdict.value,
        "provenance": content.provenance.to_dict(),
        "findings": [f.to_dict() for f in content.findings],
        "collapse_warnings": content.collapse_warnings,
        "patch_set": content.patch_set,
        "lines_on_patchset": content.lines_on_patchset,
    }
    if include_body:
        payload["body"] = content.body
        payload["body_omitted_lines"] = content.body_omitted_lines
    return payload


def group_by_label(contents: Sequence[CiContent]) -> list[tuple[str, list[CiContent]]]:
    """Contents grouped by **CI link** label, labels in first-seen order."""
    groups: dict[str, list[CiContent]] = {}
    for content in contents:
        groups.setdefault(content.link.label, []).append(content)
    return list(groups.items())


def provenance_note(content: CiContent) -> str:
    """``Widget #55, cached, truncated`` — what a reader should know about where findings came from."""
    prov = content.provenance
    parts = (prov.build, "cached" if prov.cached else None, "log truncated" if prov.truncated else None)
    return ", ".join(p for p in parts if p)


def patchset_note(content: CiContent) -> str | None:
    """``from patchset 2, before your local changes``: findings that are not about the commit shown."""
    if content.patch_set is None:
        return None
    lines = f"; lines are patchset {content.patch_set}'s" if content.lines_on_patchset else ""
    return f"from patchset {content.patch_set} (the last pushed), before your local changes{lines}"


def where(finding: CiFinding, patch_set: int | None = None) -> str:
    """``path:line``, and where CI saw that line when the finding was mapped onto the local commit."""
    if not finding.location or patch_set is None:
        return finding.location
    if finding.line_changed:
        return f"{finding.location} (line {finding.patchset_line} on patchset {patch_set}, changed since)"
    if finding.patchset_line is not None:
        return f"{finding.location} (line {finding.patchset_line} on patchset {patch_set})"
    return finding.location


def _finding_human(finding: CiFinding, patch_set: int | None = None) -> list[str]:
    location = where(finding, patch_set)
    lines = [f"{finding.tool}{f'  {location}' if location else ''}  {finding.message}"]
    if finding.detail:
        lines += [f"    {d}" for d in finding.detail.splitlines()]
    return lines


def warnings_note(count: int) -> str:
    """The one line that stands for *count* collapsed warnings."""
    return f"{counted(count, 'warning')} not listed (ger ci --warnings lists them)"


def _build_and_log(content: CiContent) -> str:
    """The build URL as CI reported it, and the log URL ger read when that is another one."""
    build, log = content.link.build_url, content.provenance.url
    return build if not log or log.rstrip("/") == build.rstrip("/") else f"{build}  (log: {log})"


def format_ci_section_human(
    contents: Sequence[CiContent], *, indent: str = "    ", warnings: bool = False
) -> list[str]:
    """``label  url  (note)`` per link, its findings (or error) indented beneath, then any body.

    Errors come first; the warnings below them are a count unless *warnings* asks for the list.
    """
    lines: list[str] = []
    for label, group in group_by_label(contents):
        for content in group:
            note = provenance_note(content)
            # A link that could not be read names its log once, on the error line, to open by hand.
            shown = content.link.build_url if content.failed else _build_and_log(content)
            lines.append(f"{indent}{label}  {shown}" + (f"  ({note})" if note else ""))
            if old := patchset_note(content):
                lines.append(f"{indent}  {old}")
            if content.failed:
                lines.append(f"{indent}  error: {content.provenance.error}; open {content.link.url}")
                continue
            if not content.findings:
                lines.append(f"{indent}  (no findings)")
            for finding in content.leading_findings:
                lines += [f"{indent}  {line}" for line in _finding_human(finding, content.patch_set)]
            collapsed = content.collapsed_warnings
            if collapsed and warnings:
                lines.append(f"{indent}  warnings:")
                for finding in collapsed:
                    lines += [f"{indent}    {line}" for line in _finding_human(finding, content.patch_set)]
            elif collapsed:
                lines.append(f"{indent}  {warnings_note(len(collapsed))}")
            lines += _body_lines(content)
    return lines


def _body_lines(content: CiContent) -> list[str]:
    if content.body is None:
        return []
    head = "log"
    if content.body_omitted_lines:
        head = f"log (last lines; {content.body_omitted_lines} earlier omitted, --full for all)"
    return ["", f"--- {head} ---", *content.body.splitlines(), f"--- end of {content.link.label} log ---"]


def _finding_markdown(finding: CiFinding, patch_set: int | None = None) -> list[str]:
    location = f" `{finding.location}`" if finding.location else ""
    if location and (shown := where(finding, patch_set)) != finding.location:
        location += shown[len(finding.location) :]
    lines = [f"- **{finding.tool}** ({finding.kind}){location} — {finding.message}"]
    if finding.detail:
        lines += ["  ```", *(f"  {d}" for d in finding.detail.splitlines()), "  ```"]
    return lines


def format_ci_section_markdown(
    contents: Sequence[CiContent], *, heading: str = "###", warnings: bool = False
) -> list[str]:
    """``### CI findings``, a sub-heading per label, a bullet per finding; fenced body when present.

    Errors come first; the warnings below them are a count unless *warnings* asks for the list.
    """
    lines = [f"{heading} CI findings", ""]
    for label, group in group_by_label(contents):
        lines.append(f"{heading}# {label}")
        for content in group:
            note = provenance_note(content)
            lines.append(f"- CI: {_build_and_log(content)}" + (f" ({note})" if note else ""))
            if old := patchset_note(content):
                lines.append(f"- {old[0].upper()}{old[1:]}")
            if content.failed:
                lines.append(f"- error: {content.provenance.error}")
                continue
            if not content.findings:
                lines.append("- (no findings)")
            for finding in content.leading_findings:
                lines += _finding_markdown(finding, content.patch_set)
            collapsed = content.collapsed_warnings
            if collapsed and warnings:
                lines.append(f"- Warnings ({len(collapsed)}):")
                for finding in collapsed:
                    lines += [f"  {line}" for line in _finding_markdown(finding, content.patch_set)]
            elif collapsed:
                lines.append(f"- {warnings_note(len(collapsed))}")
            if content.body is not None:
                omitted = f" ({content.body_omitted_lines} earlier lines omitted)" if content.body_omitted_lines else ""
                lines += ["", f"Log{omitted}:", "", "```text", *content.body.splitlines(), "```"]
        lines.append("")
    return lines
