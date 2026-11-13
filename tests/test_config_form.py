"""Resolution and temp-file format for editable ger settings."""

from __future__ import annotations

from gerrit_workflow_tools.core.config import SHOW_AFTER_EDIT_KEY, ConfigScope, Settings
from gerrit_workflow_tools.core.config_editor_file import (
    parse_config_editor_text,
    planned_editor_writes,
    render_config_editor_text,
)
from gerrit_workflow_tools.core.config_form import ResolvedSetting, SettingKind, resolve_config_form


def _by_key(settings: Settings, *, branch: str | None = "feature") -> dict[str, ResolvedSetting]:
    form = resolve_config_form(settings, branch=branch)
    return {setting.spec.key: setting for section in form for setting in section.settings}


def test_sections_follow_command_order() -> None:
    form = resolve_config_form(Settings.from_map({}), branch="feature")
    assert tuple(section.title for section in form) == (
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


def test_unset_keys_show_the_default_and_are_not_stored() -> None:
    settings = Settings.from_map({})
    resolved = _by_key(settings)
    remote = resolved["gerrit.remote"]
    assert remote.stored is None
    assert remote.scope is None
    assert remote.value == "origin" == settings.gerrit_remote
    assert resolved["gerrit.wrapWidth"].value == str(settings.wrap_width)
    assert resolved["gerrit.push.remotePolicy"].value == settings.push_remote_policy
    assert "gerrit.token" not in resolved
    assert "gerrit.password" not in resolved


def test_stored_value_wins_and_keeps_scope() -> None:
    settings = Settings.from_map(
        {
            "gerrit.remote": "gerrit",
            SHOW_AFTER_EDIT_KEY: "yes",
            "gerrit.webUrl": "https://g.example",
        },
        scopes={"gerrit.webUrl": ConfigScope.GLOBAL},
    )
    resolved = _by_key(settings)
    assert resolved["gerrit.remote"].stored == "gerrit"
    assert resolved["gerrit.remote"].scope is ConfigScope.LOCAL
    assert resolved["gerrit.webUrl"].scope is ConfigScope.GLOBAL
    assert resolved[SHOW_AFTER_EDIT_KEY].value == "true"
    assert resolved[SHOW_AFTER_EDIT_KEY].stored == "yes"


def test_branch_keys_need_a_branch() -> None:
    named = _by_key(Settings.from_map({}), branch="topic")
    assert "branch.topic.gerritTarget" in named
    detached = _by_key(Settings.from_map({}), branch=None)
    assert not any(key.startswith("branch.") for key in detached)


def test_render_comments_out_unset_and_shows_default() -> None:
    form = resolve_config_form(Settings.from_map({"gerrit.remote": "gerrit"}), branch=None)
    text = render_config_editor_text(form)
    assert "# default: origin" in text
    assert "gerrit.remote=gerrit" in text
    assert "# scope: local" in text
    assert "# gerrit.stopPattern=" in text
    assert "# scope: unset (uncomment to write local)" in text
    assert "gerrit.token" not in text


def test_round_trip_unchanged_file_plans_no_writes() -> None:
    settings = Settings.from_map({"gerrit.remote": "gerrit", SHOW_AFTER_EDIT_KEY: "true"})
    form = resolve_config_form(settings, branch="feature")
    text = render_config_editor_text(form)
    parsed, errors = parse_config_editor_text(text, form)
    assert errors == ()
    writes, write_errors = planned_editor_writes(form, parsed)
    assert write_errors == ()
    assert writes == ()


def test_uncomment_default_writes_local_explicit_value() -> None:
    form = resolve_config_form(Settings.from_map({}), branch=None)
    text = render_config_editor_text(form).replace(
        f"# {SHOW_AFTER_EDIT_KEY}=true",
        f"{SHOW_AFTER_EDIT_KEY}=false",
    )
    parsed, errors = parse_config_editor_text(text, form)
    assert errors == ()
    writes, write_errors = planned_editor_writes(form, parsed)
    assert write_errors == ()
    assert len(writes) == 1
    assert writes[0].key == SHOW_AFTER_EDIT_KEY
    assert writes[0].action == "set"
    assert writes[0].value == "false"
    assert writes[0].scope is ConfigScope.LOCAL


def test_comment_out_unsets_at_current_scope() -> None:
    form = resolve_config_form(
        Settings.from_map({"gerrit.webUrl": "https://g.example"}, scopes={"gerrit.webUrl": ConfigScope.GLOBAL}),
        branch=None,
    )
    text = render_config_editor_text(form).replace(
        "gerrit.webUrl=https://g.example",
        "# gerrit.webUrl=https://g.example",
    )
    parsed, errors = parse_config_editor_text(text, form)
    assert errors == ()
    writes, write_errors = planned_editor_writes(form, parsed)
    assert write_errors == ()
    assert len(writes) == 1
    assert writes[0].action == "unset"
    assert writes[0].scope is ConfigScope.GLOBAL


def test_active_default_keeps_stored_value() -> None:
    form = resolve_config_form(Settings.from_map({"gerrit.remote": "origin"}), branch=None)
    text = render_config_editor_text(form)
    assert "gerrit.remote=origin" in text
    parsed, errors = parse_config_editor_text(text, form)
    assert errors == ()
    writes, _ = planned_editor_writes(form, parsed)
    assert writes == ()


def test_missing_key_is_a_parse_error() -> None:
    form = resolve_config_form(Settings.from_map({}), branch=None)
    text = "\n".join(line for line in render_config_editor_text(form).splitlines() if "gerrit.remote=" not in line)
    _parsed, errors = parse_config_editor_text(text + "\n", form)
    assert any("missing setting gerrit.remote" in err.message for err in errors)


def test_show_after_edit_is_bool_under_ger_edit_and_on_by_default() -> None:
    resolved = _by_key(Settings.from_map({}))[SHOW_AFTER_EDIT_KEY]
    assert resolved.spec.section == "ger edit"
    assert resolved.spec.kind is SettingKind.BOOL
    assert resolved.value == "true"
