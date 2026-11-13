"""Projections of **CI content**: JSON (the agent contract), human text, and Markdown.

Findings are grouped by **CI link** label. Plain text only — the caller adds color.

Human text and Markdown list a link's leading findings, then its collapsed warnings as one
count line unless the caller asks for them. JSON always carries every finding.
"""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.ci_content import CiContent, CiFinding


def ci_content_json(content: CiContent, *, include_body: bool = False) -> dict[str, object]:
    """One link's **CI content** as JSON: link, provenance, findings, and the body when asked."""
    payload: dict[str, object] = {
        "label": content.link.label,
        "url": content.link.url,
        "source": content.link.source,
        "provenance": content.provenance.to_dict(),
        "findings": [f.to_dict() for f in content.findings],
        "collapse_warnings": content.collapse_warnings,
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


def _finding_human(finding: CiFinding) -> list[str]:
    where = f"  {finding.location}" if finding.location else ""
    lines = [f"{finding.tool}{where}  {finding.message}"]
    if finding.detail:
        lines += [f"    {d}" for d in finding.detail.splitlines()]
    return lines


def warnings_note(count: int) -> str:
    """The one line that stands for *count* collapsed warnings."""
    return f"{count} warning{'' if count == 1 else 's'} not listed (ger ci --warnings lists them)"


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
            lines.append(f"{indent}{label}  {content.provenance.url}" + (f"  ({note})" if note else ""))
            if content.failed:
                lines.append(f"{indent}  error: {content.provenance.error}")
                continue
            if not content.findings:
                lines.append(f"{indent}  (no findings)")
            for finding in content.leading_findings:
                lines += [f"{indent}  {line}" for line in _finding_human(finding)]
            collapsed = content.collapsed_warnings
            if collapsed and warnings:
                lines.append(f"{indent}  warnings:")
                for finding in collapsed:
                    lines += [f"{indent}    {line}" for line in _finding_human(finding)]
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


def _finding_markdown(finding: CiFinding) -> list[str]:
    where = f" `{finding.location}`" if finding.location else ""
    lines = [f"- **{finding.tool}** ({finding.kind}){where} — {finding.message}"]
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
            lines.append(f"- CI: {content.provenance.url}" + (f" ({note})" if note else ""))
            if content.failed:
                lines.append(f"- error: {content.provenance.error}")
                continue
            if not content.findings:
                lines.append("- (no findings)")
            for finding in content.leading_findings:
                lines += _finding_markdown(finding)
            collapsed = content.collapsed_warnings
            if collapsed and warnings:
                lines.append(f"- Warnings ({len(collapsed)}):")
                for finding in collapsed:
                    lines += [f"  {line}" for line in _finding_markdown(finding)]
            elif collapsed:
                lines.append(f"- {warnings_note(len(collapsed))}")
            if content.body is not None:
                omitted = f" ({content.body_omitted_lines} earlier lines omitted)" if content.body_omitted_lines else ""
                lines += ["", f"Log{omitted}:", "", "```text", *content.body.splitlines(), "```"]
        lines.append("")
    return lines
