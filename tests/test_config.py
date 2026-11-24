from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_keys import DEFAULT_STOP_PATTERN, DEFAULT_WARNING_PATTERN, ConfigScope
from gerrit_workflow_tools.core.git_run import git


# Most of these need no repository: a Settings is just the key/value map git would have
# produced, so the defaults and the parsing of each value can be stated directly.


def test_stop_pattern_from_settings() -> None:
    assert Settings.from_map({"ger.stopPattern": r"^hold:"}).stop_pattern == r"^hold:"


def test_warning_pattern_defaults() -> None:
    assert Settings.from_map({}).warning_pattern == DEFAULT_WARNING_PATTERN


def test_warning_pattern_from_settings() -> None:
    assert Settings.from_map({"ger.warningPattern": r"^feat:"}).warning_pattern == r"^feat:"


def test_rebase_defaults() -> None:
    assert Settings.from_map({}).rebase_defaults == {"onto_remote": False, "drop_merged_equivalent": False}
    configured = Settings.from_map(
        {"ger.rebaseOntoRemote": "true", "ger.rebaseDropMergedEquivalent": "1"},
    )
    assert configured.rebase_defaults == {"onto_remote": True, "drop_merged_equivalent": True}


def test_push_remote_policy_defaults_and_aliases() -> None:
    assert Settings.from_map({}).push_remote_policy == "ignore-not-rebased"
    assert Settings.from_map({"ger.pushRemotePolicy": "WARN-NOT-REBASED"}).push_remote_policy == "warn-not-rebased"
    assert Settings.from_map({"ger.pushRemotePolicy": "bogus"}).push_remote_policy == "ignore-not-rebased"


def test_flag_accepts_git_truthy_spellings() -> None:
    for raw in ("1", "true", "TRUE", "yes", "on"):
        assert Settings.from_map({"ger.someFlag": raw}).flag("ger.someFlag") is True
    for raw in ("0", "false", "no", "off", ""):
        assert Settings.from_map({"ger.someFlag": raw}).flag("ger.someFlag") is False
    assert Settings.from_map({}).flag("ger.someFlag", default=True) is True


def test_inbox_settings_defaults_and_overrides() -> None:
    unset = Settings.from_map({})
    assert unset.inbox_require_verified is True
    assert unset.inbox_verified_label == "Verified"
    assert unset.inbox_projects == []
    assert unset.inbox_to_review_query is None
    assert unset.inbox_limit is None
    configured = Settings.from_map(
        {
            "ger.inboxRequireVerified": "false",
            "ger.inboxVerifiedLabel": "CI",
            "ger.inboxProjects": "a, b",
            "ger.inboxToReviewQuery": "reviewer:self",
            "ger.inboxLimit": "20",
        }
    )
    assert configured.inbox_require_verified is False
    assert configured.inbox_verified_label == "CI"
    assert configured.inbox_projects == ["a", "b"]
    assert configured.inbox_to_review_query == "reviewer:self"
    assert configured.inbox_limit == 20


def test_branch_scoped_keys_preserve_branch_name_case() -> None:
    settings = Settings.from_map({"branch.Feature/X.gerTarget": "main"})
    assert settings.branch_gerrit_target("Feature/X") == "main"
    assert settings.branch_gerrit_target("feature/x") is None


def test_from_cwd_reads_effective_git_config(stack_repo: Path) -> None:
    """``git config --list --show-scope`` feeds both values and winning scopes."""
    git("config", "ger.stopPattern", r"^hold:", cwd=stack_repo)
    git("config", "ger.remote", "gerrit", cwd=stack_repo)

    settings = Settings.from_cwd(stack_repo)

    assert settings.stop_pattern == r"^hold:"
    assert settings.gerrit_remote == "gerrit"
    assert settings.scope("ger.stopPattern") is ConfigScope.LOCAL
    assert settings.scope("ger.remote") is ConfigScope.LOCAL
    assert settings.scope("ger.webUrl") is None


def test_from_map_defaults_scope_to_local_and_accepts_overrides() -> None:
    local = Settings.from_map({"ger.remote": "gerrit"})
    assert local.scope("ger.remote") is ConfigScope.LOCAL
    assert local.scope("missing") is None
    global_remote = Settings.from_map(
        {"ger.remote": "origin", "ger.webUrl": "https://g.example"},
        scopes={"ger.webUrl": ConfigScope.GLOBAL},
    )
    assert global_remote.scope("ger.remote") is ConfigScope.LOCAL
    assert global_remote.scope("ger.webUrl") is ConfigScope.GLOBAL


def test_from_cwd_is_a_snapshot_not_a_live_view(stack_repo: Path) -> None:
    """A write after the read is invisible; taking a new snapshot is how you see it."""
    before = Settings.from_cwd(stack_repo)
    git("config", "ger.stopPattern", r"^hold:", cwd=stack_repo)

    assert before.stop_pattern == DEFAULT_STOP_PATTERN
    assert Settings.from_cwd(stack_repo).stop_pattern == r"^hold:"
