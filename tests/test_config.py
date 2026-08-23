from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import (
    _DEFAULT_STOP_PATTERN,
    _DEFAULT_WARNING_PATTERN,
    Settings,
)
from gerrit_workflow_tools.core.git_run import git

# Most of these need no repository: a Settings is just the key/value map git would have
# produced, so the defaults and the parsing of each value can be stated directly.


def test_stop_pattern_defaults() -> None:
    assert Settings.from_map({}).stop_pattern == _DEFAULT_STOP_PATTERN


def test_stop_pattern_from_settings() -> None:
    assert Settings.from_map({"gerrit.stopPattern": r"^hold:"}).stop_pattern == r"^hold:"


def test_warning_pattern_defaults() -> None:
    assert Settings.from_map({}).warning_pattern == _DEFAULT_WARNING_PATTERN


def test_warning_pattern_from_settings() -> None:
    assert Settings.from_map({"gerrit.warningPattern": r"^feat:"}).warning_pattern == r"^feat:"


def test_rebase_defaults() -> None:
    assert Settings.from_map({}).rebase_defaults == {"onto_remote": False, "drop_merged_equivalent": False}
    configured = Settings.from_map(
        {"gerrit.rebaseOntoRemote": "true", "gerrit.rebaseDropMergedEquivalent": "1"},
    )
    assert configured.rebase_defaults == {"onto_remote": True, "drop_merged_equivalent": True}


def test_push_remote_policy_defaults_and_aliases() -> None:
    assert Settings.from_map({}).push_remote_policy == "ignore-not-rebased"
    assert Settings.from_map({"gerrit.push.remotePolicy": "WARN-NOT-REBASED"}).push_remote_policy == "warn-not-rebased"
    assert Settings.from_map({"gerrit.push.remotePolicy": "bogus"}).push_remote_policy == "ignore-not-rebased"


def test_gerrit_remote_defaults_to_origin() -> None:
    assert Settings.from_map({}).gerrit_remote == "origin"
    assert Settings.from_map({"gerrit.remote": "gerrit"}).gerrit_remote == "gerrit"


def test_flag_accepts_git_truthy_spellings() -> None:
    for raw in ("1", "true", "TRUE", "yes", "on"):
        assert Settings.from_map({"gerrit.someFlag": raw}).flag("gerrit.someFlag") is True
    for raw in ("0", "false", "no", "off", ""):
        assert Settings.from_map({"gerrit.someFlag": raw}).flag("gerrit.someFlag") is False
    assert Settings.from_map({}).flag("gerrit.someFlag", default=True) is True


def test_inbox_settings_defaults_and_overrides() -> None:
    unset = Settings.from_map({})
    assert unset.inbox_require_verified is True
    assert unset.inbox_verified_label == "Verified"
    assert unset.inbox_projects == []
    assert unset.inbox_to_review_query is None
    assert unset.inbox_limit is None
    configured = Settings.from_map(
        {
            "inbox.requireVerified": "false",
            "inbox.verifiedLabel": "CI",
            "inbox.projects": "a, b",
            "inbox.toReviewQuery": "reviewer:self",
            "inbox.limit": "20",
        }
    )
    assert configured.inbox_require_verified is False
    assert configured.inbox_verified_label == "CI"
    assert configured.inbox_projects == ["a", "b"]
    assert configured.inbox_to_review_query == "reviewer:self"
    assert configured.inbox_limit == 20


def test_keys_are_canonicalized_like_git_config_list() -> None:
    """Git lowercases the last segment only; a snapshot must answer either spelling."""
    settings = Settings.from_map({"gerrit.webUrl": "https://g.example"})
    assert settings.get("gerrit.weburl") == "https://g.example"
    assert settings.get("gerrit.WEBURL") == "https://g.example"
    assert settings.gerrit_web_url == "https://g.example"


def test_values_are_stripped_and_blank_reads_as_unset() -> None:
    settings = Settings.from_map({"gerrit.remote": "  gerrit  ", "gerrit.project": "   "})
    assert settings.gerrit_remote == "gerrit"
    assert settings.get("gerrit.remote") == "gerrit"
    assert settings.gerrit_project is None


def test_branch_scoped_keys_preserve_branch_name_case() -> None:
    settings = Settings.from_map({"branch.Feature/X.gerritTarget": "main"})
    assert settings.branch_gerrit_target("Feature/X") == "main"
    assert settings.branch_gerrit_target("feature/x") is None


def test_from_cwd_reads_effective_git_config(stack_repo: Path) -> None:
    """The one test that needs a repository: that ``git config --list`` is what feeds the map."""
    git("config", "gerrit.stopPattern", r"^hold:", cwd=stack_repo)
    git("config", "gerrit.remote", "gerrit", cwd=stack_repo)

    settings = Settings.from_cwd(stack_repo)

    assert settings.stop_pattern == r"^hold:"
    assert settings.gerrit_remote == "gerrit"


def test_from_cwd_is_a_snapshot_not_a_live_view(stack_repo: Path) -> None:
    """A write after the read is invisible; taking a new snapshot is how you see it."""
    before = Settings.from_cwd(stack_repo)
    git("config", "gerrit.stopPattern", r"^hold:", cwd=stack_repo)

    assert before.stop_pattern == _DEFAULT_STOP_PATTERN
    assert Settings.from_cwd(stack_repo).stop_pattern == r"^hold:"
