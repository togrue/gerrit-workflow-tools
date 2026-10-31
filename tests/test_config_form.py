"""Resolution of editable settings: defaults when git config is silent, stored values when it is not."""

from __future__ import annotations

from gerrit_workflow_tools.core.config import EDIT_NEXT_SHOW_KEY, Settings
from gerrit_workflow_tools.core.config_form import (
    ResolvedSetting,
    SettingKind,
    format_setting_detail,
    planned_config_write,
    resolve_config_form,
)


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
        "ger rebase",
        "ger edit",
    )


def test_unset_keys_show_the_default_and_are_not_stored() -> None:
    settings = Settings.from_map({})
    resolved = _by_key(settings)
    remote = resolved["gerrit.remote"]
    assert remote.stored is None
    assert remote.value == "origin" == settings.gerrit_remote
    assert resolved["gerrit.wrapWidth"].value == str(settings.wrap_width)
    assert resolved["gerrit.push.remotePolicy"].value == settings.push_remote_policy
    assert resolved["gerrit.scriptsDir"].value == settings.scripts_dir
    assert resolved["gerrit.stopPattern"].value == settings.stop_pattern
    assert resolved["gerrit.warningPattern"].value == settings.warning_pattern
    assert resolved["inbox.verifiedLabel"].value == settings.inbox_verified_label
    assert resolved["gerrit.lastPushedBranch"].value == "true"
    assert resolved["inbox.requireVerified"].value == "true"
    edit_next = resolved[EDIT_NEXT_SHOW_KEY]
    assert edit_next.spec.section == "ger edit"
    assert edit_next.spec.kind is SettingKind.BOOL
    assert edit_next.stored is None
    assert edit_next.value == "false"
    assert settings.edit_next_show is False


def test_stored_value_wins_over_the_default() -> None:
    settings = Settings.from_map(
        {
            "gerrit.remote": "gerrit",
            "gerrit.wrapWidth": "0",
            EDIT_NEXT_SHOW_KEY: "yes",
            "gerrit.webUrl": "https://g.example",
        }
    )
    resolved = _by_key(settings)
    assert resolved["gerrit.remote"].stored == "gerrit"
    assert resolved["gerrit.remote"].value == "gerrit"
    # Invalid width stays visible as stored; the command still falls back, the editor does not.
    assert resolved["gerrit.wrapWidth"].value == "0"
    assert settings.wrap_width == 120
    edit_next = resolved[EDIT_NEXT_SHOW_KEY]
    assert edit_next.stored == "yes"
    assert edit_next.value == "true"
    assert settings.edit_next_show is True
    assert resolved["gerrit.webUrl"].value == "https://g.example"
    assert "default: (unset)" in format_setting_detail(resolved["gerrit.webUrl"])


def test_edit_next_show_detail_lists_default_allowed_and_description() -> None:
    resolved = _by_key(Settings.from_map({}))[EDIT_NEXT_SHOW_KEY]
    detail = format_setting_detail(resolved)
    assert "default: false" in detail
    assert "git config: (unset)" in detail
    assert "allowed: true, false" in detail
    assert "ger show" in detail
    assert "--next only moves" in detail


def test_branch_keys_need_a_branch() -> None:
    named = _by_key(Settings.from_map({}), branch="topic")
    assert "branch.topic.gerritTarget" in named
    assert named["branch.topic.gerritTarget"].value == ""
    detached = _by_key(Settings.from_map({}), branch=None)
    assert not any(key.startswith("branch.") for key in detached)


def test_planned_write_leaves_defaults_unstored_until_the_value_changes() -> None:
    unset = _by_key(Settings.from_map({}))
    edit_next = unset[EDIT_NEXT_SHOW_KEY]
    remote = unset["gerrit.remote"]
    assert planned_config_write(edit_next, "false") is None
    assert planned_config_write(remote, "origin") is None
    turned_on = planned_config_write(edit_next, "true")
    assert turned_on is not None
    assert turned_on.action == "set"
    assert turned_on.value == "true"
    renamed = planned_config_write(remote, "gerrit")
    assert renamed is not None and renamed.action == "set" and renamed.value == "gerrit"


def test_planned_write_unsets_a_stored_value_returned_to_the_default() -> None:
    stored = _by_key(
        Settings.from_map({EDIT_NEXT_SHOW_KEY: "yes", "gerrit.remote": "gerrit", "gerrit.lastPushedBranch": "false"})
    )
    # yes and true are the same flag; showing true is not a change.
    assert planned_config_write(stored[EDIT_NEXT_SHOW_KEY], "true") is None
    back_off = planned_config_write(stored[EDIT_NEXT_SHOW_KEY], "false")
    assert back_off is not None and back_off.action == "unset"
    back_to_origin = planned_config_write(stored["gerrit.remote"], "origin")
    assert back_to_origin is not None and back_to_origin.action == "unset"
    # Explicit value equal to the default stays if the user did not change it.
    kept = _by_key(Settings.from_map({"gerrit.remote": "origin"}))
    assert planned_config_write(kept["gerrit.remote"], "origin") is None


def test_edit_next_show_flag_defaults_off() -> None:
    assert Settings.from_map({}).edit_next_show is False
    assert Settings.from_map({EDIT_NEXT_SHOW_KEY: "true"}).edit_next_show is True
    assert Settings.from_map({EDIT_NEXT_SHOW_KEY: "false"}).edit_next_show is False
