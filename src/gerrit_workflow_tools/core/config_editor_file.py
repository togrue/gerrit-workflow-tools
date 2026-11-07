"""Render and parse the ``ger config`` temp-file key=value format."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gerrit_workflow_tools.core.config import ConfigScope
from gerrit_workflow_tools.core.config_form import (
    ConfigSection,
    ResolvedSetting,
    SettingKind,
    SettingSpec,
    canonical_bool,
)
from gerrit_workflow_tools.core.config_write import ConfigWrite


_ASSIGNMENT = re.compile(r"^(\s*)(#\s*)?([A-Za-z0-9_.-]+)=(.*)$")
_WRITABLE_SCOPES = frozenset({ConfigScope.LOCAL, ConfigScope.GLOBAL, ConfigScope.WORKTREE})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class ParsedSetting:
    """One catalog key as found in the editor file."""

    key: str
    active: bool
    value: str
    line: int


@dataclass(frozen=True)
class ParseError:
    """A problem in the editor file, with a 1-based line when known."""

    message: str
    line: int | None = None


def _default_label(default: str) -> str:
    return default if default else "(unset)"


def _scope_label(scope: ConfigScope | None) -> str:
    if scope is None:
        return "unset (uncomment to write local)"
    return scope.value


def render_config_editor_text(sections: tuple[ConfigSection, ...]) -> str:
    """Render the commented key=value file opened by ``ger config``."""
    lines = [
        "# ger config — save and close to apply; delete all content to abort.",
        "# Uncomment a line to set it (writes local). Comment it out to unset.",
        "# An active line equal to the default keeps that value stored; it does not unset.",
        "# Settings are key=value pairs as in `git config --list`.",
        "# If both global and local define a key, only the effective (local) scope is shown;",
        "# commenting it out unsets local and may reveal the global value next time.",
        "",
    ]
    for section in sections:
        lines.append(f"# --- {section.title} ---")
        lines.append("")
        for resolved in section.settings:
            spec = resolved.spec
            lines.append(f"# {spec.description}")
            lines.append(f"# default: {_default_label(spec.default)}")
            if spec.choices:
                lines.append("# allowed: " + ", ".join(spec.choices))
            lines.append(f"# scope: {_scope_label(resolved.scope)}")
            assignment = f"{spec.key}={resolved.value}"
            lines.append(assignment if resolved.stored is not None else f"# {assignment}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _normalize_edited(spec: SettingSpec, raw: str) -> str | ParseError:
    text = raw.strip() if spec.kind is not SettingKind.TEXT else raw
    if spec.kind is SettingKind.BOOL:
        lowered = text.strip().lower()
        if lowered not in _TRUTHY and lowered not in ("0", "false", "no", "off"):
            return ParseError(f"invalid boolean for {spec.key}: {raw!r}")
        return canonical_bool(text)
    if spec.kind is SettingKind.CHOICE:
        if text not in spec.choices:
            return ParseError(f"invalid value for {spec.key}: {raw!r} (allowed: {', '.join(spec.choices)})")
        return text
    if spec.kind is SettingKind.INT:
        if text == "" and spec.default == "":
            return ""
        try:
            value = int(text)
        except ValueError:
            return ParseError(f"invalid integer for {spec.key}: {raw!r}")
        if value < 1:
            return ParseError(f"invalid integer for {spec.key}: {raw!r} (must be >= 1)")
        return str(value)
    return text


def parse_config_editor_text(
    text: str,
    sections: tuple[ConfigSection, ...],
) -> tuple[tuple[ParsedSetting, ...], tuple[ParseError, ...]]:
    """Parse active and commented ``key=value`` lines against the resolved catalog."""
    by_key = {setting.spec.key: setting for section in sections for setting in section.settings}
    found: dict[str, ParsedSetting] = {}
    errors: list[ParseError] = []

    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        match = _ASSIGNMENT.match(raw)
        if match is None:
            if stripped and not stripped.startswith("#"):
                errors.append(ParseError(f"expected key=value, got {raw!r}", line_no))
            continue
        key = match.group(3)
        value = match.group(4)
        active = match.group(2) is None

        if key not in by_key:
            errors.append(ParseError(f"unknown setting {key}", line_no))
            continue
        if key in found:
            errors.append(ParseError(f"duplicate setting {key}", line_no))
            continue
        normalized = _normalize_edited(by_key[key].spec, value)
        if isinstance(normalized, ParseError):
            errors.append(ParseError(normalized.message, line_no))
            continue
        found[key] = ParsedSetting(key, active, normalized, line_no)

    for key in by_key:
        if key not in found:
            errors.append(ParseError(f"missing setting {key}"))

    if errors:
        return (), tuple(errors)
    return tuple(found[key] for key in by_key), ()


def _write_scope_for_set(resolved: ResolvedSetting) -> ConfigScope:
    if resolved.scope in _WRITABLE_SCOPES:
        return resolved.scope  # type: ignore[return-value]
    return ConfigScope.LOCAL


def _write_scope_for_unset(resolved: ResolvedSetting) -> ConfigScope | ParseError:
    if resolved.scope in _WRITABLE_SCOPES:
        return resolved.scope  # type: ignore[return-value]
    if resolved.scope is None:
        return ConfigScope.LOCAL
    return ParseError(f"cannot unset {resolved.spec.key}: defined in {resolved.scope.value} config")


def planned_editor_writes(
    sections: tuple[ConfigSection, ...],
    parsed: tuple[ParsedSetting, ...],
) -> tuple[tuple[ConfigWrite, ...], tuple[ParseError, ...]]:
    """Diff the parsed editor file against the resolved form into scoped writes."""
    by_key = {setting.spec.key: setting for section in sections for setting in section.settings}
    writes: list[ConfigWrite] = []
    errors: list[ParseError] = []
    for item in parsed:
        resolved = by_key[item.key]
        spec = resolved.spec
        if item.active:
            if resolved.stored is None:
                writes.append(ConfigWrite(item.key, "set", item.value, ConfigScope.LOCAL))
                continue
            current = canonical_bool(resolved.stored) if spec.kind is SettingKind.BOOL else resolved.stored
            if item.value == current:
                continue
            writes.append(ConfigWrite(item.key, "set", item.value, _write_scope_for_set(resolved)))
            continue
        if resolved.stored is None:
            continue
        scope = _write_scope_for_unset(resolved)
        if isinstance(scope, ParseError):
            errors.append(ParseError(scope.message, item.line))
            continue
        writes.append(ConfigWrite(item.key, "unset", scope=scope))
    return tuple(writes), tuple(errors)
