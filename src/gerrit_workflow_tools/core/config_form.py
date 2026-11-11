"""Editable settings catalog for ``ger config``: sections, defaults, and resolution.

Credentials (``gerrit.token`` / ``gerrit.password``) stay on ``ger setup``, not this form.
Temp-file render/parse lives in :mod:`gerrit_workflow_tools.core.config_editor_file`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from gerrit_workflow_tools.core.config import EDIT_NEXT_SHOW_KEY, ConfigScope, Settings


_TRUTHY = frozenset({"1", "true", "yes", "on"})
_BOOL_CHOICES = ("true", "false")
_DEFAULT_STOP_PATTERN = r"^(?:dropme!|todo\b|test!|wip\b)"
_DEFAULT_WARNING_PATTERN = r"(?:^[^\s]+$|(?i:\b(?:wip|todo)\b))"
_REMOTE_POLICY_CHOICES = ("ignore-not-rebased", "warn-not-rebased", "error-not-rebased")

SECTION_TITLES: tuple[str, ...] = (
    "General",
    "ger show",
    "ger log",
    "ger push",
    "ger inbox",
    "ger review",
    "ger rebase",
    "ger edit",
    "ger serve",
)


class SettingKind(Enum):
    """How a setting is edited and compared."""

    TEXT = "text"
    INT = "int"
    BOOL = "bool"
    CHOICE = "choice"


@dataclass(frozen=True)
class SettingSpec:
    """One git config key the config editor can show."""

    key: str
    section: str
    description: str
    kind: SettingKind
    default: str
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSetting:
    """A spec plus the value and scope the editor should show."""

    spec: SettingSpec
    stored: str | None
    value: str
    scope: ConfigScope | None = None


@dataclass(frozen=True)
class ConfigSection:
    """One titled group of resolved settings, in display order."""

    title: str
    settings: tuple[ResolvedSetting, ...]


def _spec(
    key: str,
    section: str,
    description: str,
    kind: SettingKind,
    default: str,
    choices: tuple[str, ...] = (),
) -> SettingSpec:
    return SettingSpec(key, section, description, kind, default, choices)


def _bool(key: str, section: str, description: str, *, default: str) -> SettingSpec:
    return _spec(key, section, description, SettingKind.BOOL, default, _BOOL_CHOICES)


SETTING_SPECS: tuple[SettingSpec, ...] = (
    _spec("gerrit.remote", "General", "Remote name used for git push to Gerrit.", SettingKind.TEXT, "origin"),
    _spec(
        "gerrit.webUrl",
        "General",
        "HTTPS base URL of the Gerrit instance. Required for API commands.",
        SettingKind.TEXT,
        "",
    ),
    _spec("gerrit.user", "General", "Username for HTTP Basic auth to the REST API.", SettingKind.TEXT, ""),
    _spec(
        "gerrit.project",
        "General",
        "Gerrit project name. When unset, parsed from the remote URL.",
        SettingKind.TEXT,
        "",
    ),
    _spec(
        "gerrit.scriptsDir",
        "General",
        "Project-local root for extension registries. Relative paths resolve from the repository toplevel.",
        SettingKind.TEXT,
        ".ger",
    ),
    _spec(
        "gerrit.stopPattern",
        "General",
        "Regex matched against the commit subject. The first match starts the non-pushable tail.",
        SettingKind.TEXT,
        _DEFAULT_STOP_PATTERN,
    ),
    _spec(
        "gerrit.warningPattern",
        "General",
        "Regex for warning highlighting in log, push, show, and inbox.",
        SettingKind.TEXT,
        _DEFAULT_WARNING_PATTERN,
    ),
    _spec(
        "gerrit.wrapWidth",
        "ger show",
        "Max visible width for commit-message lines and comment text. Values below 1 use the default.",
        SettingKind.INT,
        "120",
    ),
    _bool(
        "ger.showCiContent",
        "ger show",
        "Fetch the CI logs behind failed CI links and attach their findings (same as --ci).",
        default="false",
    ),
    _bool(
        "gerrit.logShowUrl",
        "ger log",
        "Include Gerrit URLs in text output even when hyperlinks are off.",
        default="false",
    ),
    _bool(
        "gerrit.logShowChangeId",
        "ger log",
        "Append the Change-Id on each text line. -vv still shows it when this is off.",
        default="false",
    ),
    _bool(
        "gerrit.pushShowAttributes",
        "ger push",
        "Include reviewer, WIP, and private preview on the push preview.",
        default="false",
    ),
    _bool(
        "gerrit.lastPushedBranch",
        "ger push",
        "After a successful push, move local branch lastPush/<branch> to the pushed commit.",
        default="true",
    ),
    _spec(
        "gerrit.push.remotePolicy",
        "ger push",
        "How to treat a branch that is not linear on the fetched target tip.",
        SettingKind.CHOICE,
        "ignore-not-rebased",
        _REMOTE_POLICY_CHOICES,
    ),
    _spec(
        "branch.{branch}.gerritTarget",
        "ger push",
        "Destination branch for this working branch. When unset, inferred from upstream.",
        SettingKind.TEXT,
        "",
    ),
    _spec(
        "branch.{branch}.gerritReviewers",
        "ger push",
        "Comma-separated accounts merged into ger push ref options.",
        SettingKind.TEXT,
        "",
    ),
    _spec(
        "ger.reviewCheckout",
        "ger review",
        "Check out reviews in a separate worktree, or on a branch in the current checkout.",
        SettingKind.CHOICE,
        "worktree",
        ("worktree", "branch"),
    ),
    _spec(
        "ger.reviewWorktree",
        "ger review",
        "Path of the review worktree. Empty: <repository>-review beside the main worktree.",
        SettingKind.TEXT,
        "",
    ),
    _bool("ger.reviewStage", "ger review", "Stage the reviewed change's diff (HEAD at its parent).", default="true"),
    _bool(
        "inbox.requireVerified",
        "ger inbox",
        "Require label:<verifiedLabel>+1 on the to-review query.",
        default="true",
    ),
    _spec("inbox.verifiedLabel", "ger inbox", "CI label name used by the verified gate.", SettingKind.TEXT, "Verified"),
    _spec(
        "inbox.projects",
        "ger inbox",
        "Default project list, comma-separated. Unset means every project on the host.",
        SettingKind.TEXT,
        "",
    ),
    _spec("inbox.toReviewQuery", "ger inbox", "Replace the to-review query entirely.", SettingKind.TEXT, ""),
    _spec("inbox.limit", "ger inbox", "Default result limit, a positive integer.", SettingKind.INT, ""),
    _bool("gerrit.rebaseOntoRemote", "ger rebase", "Default for ger rebase --onto-remote.", default="false"),
    _bool(
        "gerrit.rebaseDropMergedEquivalent",
        "ger rebase",
        "Default for ger rebase --drop-merged-equivalent.",
        default="false",
    ),
    _spec(
        "ger.attentionCursorTtl",
        "ger edit",
        "Max age of a usable attention cursor, such as 14d, 2w, or 48h.",
        SettingKind.TEXT,
        "14d",
    ),
    _bool(
        EDIT_NEXT_SHOW_KEY,
        "ger edit",
        "After ger edit --next moves to a commit and that rebase succeeds, run ger show on it. "
        "Off means --next only moves.",
        default="false",
    ),
    _spec(
        "ger.eventStream",
        "ger serve",
        "Follow Gerrit's event stream over ssh: auto tries once quietly, on keeps trying and says so, off never.",
        SettingKind.CHOICE,
        "auto",
        ("auto", "on", "off"),
    ),
    _spec(
        "ger.eventStreamHost",
        "ger serve",
        "ssh host of Gerrit. Empty: from the remote or gerrit.webUrl.",
        SettingKind.TEXT,
        "",
    ),
    _spec(
        "ger.eventStreamPort",
        "ger serve",
        "ssh port of Gerrit. Empty: from the remote, else 29418.",
        SettingKind.INT,
        "",
    ),
    _spec(
        "ger.eventStreamUser",
        "ger serve",
        "ssh user on Gerrit. Empty: from the remote, else gerrit.user.",
        SettingKind.TEXT,
        "",
    ),
)


def _concrete_key(spec: SettingSpec, branch: str | None) -> str | None:
    if "{branch}" not in spec.key:
        return spec.key
    if not branch:
        return None
    return spec.key.format(branch=branch)


def canonical_bool(raw: str) -> str:
    """Normalize a git-config truthy spelling to ``true`` or ``false``."""
    return "true" if raw.strip().lower() in _TRUTHY else "false"


def _display_value(spec: SettingSpec, stored: str | None) -> str:
    if spec.kind is SettingKind.BOOL:
        if stored is None:
            return spec.default
        return canonical_bool(stored)
    if stored is None:
        return spec.default
    return stored


def resolve_config_form(settings: Settings, *, branch: str | None = None) -> tuple[ConfigSection, ...]:
    """Resolve every editable setting against *settings*.

    Branch-scoped keys are included only when *branch* is known. Their key is the
    concrete ``branch.<name>.*`` string.
    """
    grouped: dict[str, list[ResolvedSetting]] = {title: [] for title in SECTION_TITLES}
    for spec in SETTING_SPECS:
        key = _concrete_key(spec, branch)
        if key is None:
            continue
        concrete = spec if key == spec.key else replace(spec, key=key)
        stored = settings.get(key)
        grouped[spec.section].append(
            ResolvedSetting(concrete, stored, _display_value(concrete, stored), settings.scope(key))
        )
    return tuple(ConfigSection(title, tuple(grouped[title])) for title in SECTION_TITLES if grouped[title])
